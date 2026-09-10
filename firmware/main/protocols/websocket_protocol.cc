#include "websocket_protocol.h"
#include "application.h"
#include "board.h"
#include "settings.h"
#include "system_info.h"

#include <esp_log.h>
#include <arpa/inet.h>
#include <cJSON.h>
#include <cstring>
#include "assets/lang_config.h"

#define TAG "WS"

namespace {

// 走备用时每隔多久探一次内网主地址。
constexpr int kPrimaryProbeIntervalMs = 30 * 1000;
constexpr int kPrimaryProbeTaskStack = 4096;
constexpr int kPrimaryProbeTaskPriority = 2;

// 从 ws://host:port/path 解出 host 与 port（wss/https 缺省 443，其余 80）。
// 与 web_socket.cc 的 Uri 解析保持一致，特别是 https 不算 TLS —— 那边只认 wss。
bool ParseHostPort(const std::string& url, std::string& host, int& port) {
    auto scheme_end = url.find("://");
    if (scheme_end == std::string::npos) {
        return false;
    }
    std::string scheme = url.substr(0, scheme_end);
    size_t begin = scheme_end + 3;
    size_t slash = url.find('/', begin);
    size_t end = (slash == std::string::npos) ? url.size() : slash;
    size_t colon = url.find(':', begin);

    if (colon != std::string::npos && colon < end) {
        host = url.substr(begin, colon - begin);
        port = 0;
        for (size_t i = colon + 1; i < end; ++i) {
            if (url[i] < '0' || url[i] > '9') {
                return false;
            }
            port = port * 10 + (url[i] - '0');
        }
    } else {
        host = url.substr(begin, end - begin);
        port = (scheme == "wss" || scheme == "https") ? 443 : 80;
    }
    return !host.empty() && port > 0;
}

}  // namespace

WebsocketProtocol::WebsocketProtocol()
    : probe_state_(std::make_shared<ProbeState>()) {
    event_group_handle_ = xEventGroupCreate();
}

WebsocketProtocol::~WebsocketProtocol() {
    // 让后台探测任务自己退出。任务不碰 event group，也无须在这里等它——
    // ProbeState 由任务侧 shared_ptr 持有，协议对象先走不会悬垂访问。
    probe_state_->prefer_backup = false;
    vEventGroupDelete(event_group_handle_);
}

bool WebsocketProtocol::ConnectTo(const std::string& url) {
    ESP_LOGI(TAG, "Connecting to websocket server: %s with version: %d", url.c_str(), version_);
    if (websocket_->Connect(url.c_str())) {
        return true;
    }
    ESP_LOGE(TAG, "Failed to connect to websocket server %s, code=%d", url.c_str(),
             websocket_->GetLastError());
    return false;
}

void WebsocketProtocol::StartPrimaryProbe(const std::shared_ptr<ProbeState>& state) {
    if (state->probe_running.exchange(true)) {
        return;  // 已有探测任务，它下一轮会读到新目标
    }
    auto* arg = new std::shared_ptr<ProbeState>(state);
    if (xTaskCreate(PrimaryProbeTask, "ws_probe", kPrimaryProbeTaskStack, arg,
                    kPrimaryProbeTaskPriority, nullptr) != pdPASS) {
        delete arg;
        state->probe_running = false;
        ESP_LOGW(TAG, "Failed to start primary probe task");
    }
}

void WebsocketProtocol::NoteBackupInUse(const std::string& primary_url) {
    probe_state_->prefer_backup = true;
    {
        std::lock_guard<std::mutex> lock(probe_state_->mutex);
        probe_state_->primary_url = primary_url;
    }
    StartPrimaryProbe(probe_state_);
}

void WebsocketProtocol::PrimaryProbeTask(void* arg) {
    std::unique_ptr<std::shared_ptr<ProbeState>> holder(
        static_cast<std::shared_ptr<ProbeState>*>(arg));
    auto state = *holder;

    while (state->prefer_backup) {
        vTaskDelay(pdMS_TO_TICKS(kPrimaryProbeIntervalMs));
        if (!state->prefer_backup) {
            break;
        }
        std::string url;
        {
            std::lock_guard<std::mutex> lock(state->mutex);
            url = state->primary_url;
        }
        std::string host;
        int port = 0;
        if (!ParseHostPort(url, host, port)) {
            break;
        }
        auto network = Board::GetInstance().GetNetwork();
        auto tcp = network != nullptr ? network->CreateTcp(-1) : nullptr;
        if (tcp == nullptr) {
            continue;
        }
        if (tcp->Connect(host, port)) {
            tcp->Disconnect();
            state->prefer_backup = false;
            ESP_LOGI(TAG, "Primary websocket %s is reachable again", url.c_str());
            break;
        }
        ESP_LOGD(TAG, "Primary websocket %s still unreachable, keep using backup", url.c_str());
    }

    state->probe_running = false;
    if (state->prefer_backup) {
        // 与 StartPrimaryProbe 的 exchange 存在竞态窗口：这里补起一个，
        // 否则“备用已切换但探测任务已退出”会让内网恢复探测永久缺失。
        StartPrimaryProbe(state);
    }
    vTaskDelete(nullptr);
}

bool WebsocketProtocol::Start() {
    // Only connect to server when audio channel is needed
    return true;
}

bool WebsocketProtocol::SendAudio(std::unique_ptr<AudioStreamPacket> packet) {
    if (websocket_ == nullptr || !websocket_->IsConnected()) {
        return false;
    }

    if (version_ == 2) {
        std::string serialized;
        serialized.resize(sizeof(BinaryProtocol2) + packet->payload.size());
        auto bp2 = (BinaryProtocol2*)serialized.data();
        bp2->version = htons(version_);
        bp2->type = 0;
        bp2->reserved = 0;
        bp2->timestamp = htonl(packet->timestamp);
        bp2->payload_size = htonl(packet->payload.size());
        memcpy(bp2->payload, packet->payload.data(), packet->payload.size());

        return websocket_->Send(serialized.data(), serialized.size(), true);
    } else if (version_ == 3) {
        std::string serialized;
        serialized.resize(sizeof(BinaryProtocol3) + packet->payload.size());
        auto bp3 = (BinaryProtocol3*)serialized.data();
        bp3->type = 0;
        bp3->reserved = 0;
        bp3->payload_size = htons(packet->payload.size());
        memcpy(bp3->payload, packet->payload.data(), packet->payload.size());

        return websocket_->Send(serialized.data(), serialized.size(), true);
    } else {
        return websocket_->Send(packet->payload.data(), packet->payload.size(), true);
    }
}

bool WebsocketProtocol::SendText(const std::string& text) {
    if (websocket_ == nullptr || !websocket_->IsConnected()) {
        return false;
    }

    if (!websocket_->Send(text)) {
        ESP_LOGE(TAG, "Failed to send text: %s", text.c_str());
        SetError(Lang::Strings::SERVER_ERROR);
        return false;
    }

    return true;
}

bool WebsocketProtocol::IsAudioChannelOpened() const {
    return websocket_ != nullptr && websocket_->IsConnected() && !error_occurred_ && !IsTimeout();
}

void WebsocketProtocol::CloseAudioChannel(bool send_goodbye) {
    (void)send_goodbye;  // Websocket doesn't need to send goodbye message
    websocket_.reset();
}

bool WebsocketProtocol::OpenAudioChannel() {
    Settings settings("websocket", false);
    std::string url = settings.GetString("url");
    std::string backup_url = settings.GetString("backup_url");
    std::string token = settings.GetString("token");
    int version = settings.GetInt("version");
    if (version != 0) {
        version_ = version;
    }

    error_occurred_ = false;

    auto network = Board::GetInstance().GetNetwork();
    websocket_ = network->CreateWebSocket(1);
    if (websocket_ == nullptr) {
        ESP_LOGE(TAG, "Failed to create websocket");
        return false;
    }

    if (!token.empty()) {
        // If token not has a space, add "Bearer " prefix
        if (token.find(" ") == std::string::npos) {
            token = "Bearer " + token;
        }
        websocket_->SetHeader("Authorization", token.c_str());
    }
    websocket_->SetHeader("Protocol-Version", std::to_string(version_).c_str());
    websocket_->SetHeader("Device-Id", SystemInfo::GetMacAddress().c_str());
    websocket_->SetHeader("Client-Id", Board::GetInstance().GetUuid().c_str());

    websocket_->OnData([this](const char* data, size_t len, bool binary) {
        if (binary) {
            if (on_incoming_audio_ != nullptr) {
                if (version_ == 2) {
                    BinaryProtocol2* bp2 = (BinaryProtocol2*)data;
                    bp2->version = ntohs(bp2->version);
                    bp2->type = ntohs(bp2->type);
                    bp2->timestamp = ntohl(bp2->timestamp);
                    bp2->payload_size = ntohl(bp2->payload_size);
                    auto payload = (uint8_t*)bp2->payload;
                    on_incoming_audio_(std::make_unique<AudioStreamPacket>(AudioStreamPacket{
                        .sample_rate = server_sample_rate_,
                        .frame_duration = server_frame_duration_,
                        .timestamp = bp2->timestamp,
                        .payload = std::vector<uint8_t>(payload, payload + bp2->payload_size)}));
                } else if (version_ == 3) {
                    BinaryProtocol3* bp3 = (BinaryProtocol3*)data;
                    bp3->type = bp3->type;
                    bp3->payload_size = ntohs(bp3->payload_size);
                    auto payload = (uint8_t*)bp3->payload;
                    on_incoming_audio_(std::make_unique<AudioStreamPacket>(AudioStreamPacket{
                        .sample_rate = server_sample_rate_,
                        .frame_duration = server_frame_duration_,
                        .timestamp = 0,
                        .payload = std::vector<uint8_t>(payload, payload + bp3->payload_size)}));
                } else {
                    on_incoming_audio_(std::make_unique<AudioStreamPacket>(AudioStreamPacket{
                        .sample_rate = server_sample_rate_,
                        .frame_duration = server_frame_duration_,
                        .timestamp = 0,
                        .payload = std::vector<uint8_t>((uint8_t*)data, (uint8_t*)data + len)}));
                }
            }
        } else {
            // Parse JSON data
            auto root = cJSON_ParseWithLength(data, len);
            auto type = cJSON_GetObjectItem(root, "type");
            if (cJSON_IsString(type)) {
                if (strcmp(type->valuestring, "hello") == 0) {
                    ParseServerHello(root);
                } else {
                    if (on_incoming_json_ != nullptr) {
                        on_incoming_json_(root);
                    }
                }
            } else {
                ESP_LOGE(TAG, "Missing message type, data: %s", std::string(data, len).c_str());
            }
            cJSON_Delete(root);
        }
        last_incoming_time_ = std::chrono::steady_clock::now();
    });

    websocket_->OnDisconnected([this]() {
        ESP_LOGI(TAG, "Websocket disconnected");
        if (on_audio_channel_closed_ != nullptr) {
            on_audio_channel_closed_();
        }
    });

    const bool has_backup = !backup_url.empty() && backup_url != url;
    bool connected = false;

    if (has_backup && probe_state_->prefer_backup) {
        // 上次主地址不通：本轮直接走备用。内网一旦恢复，后台探测会清掉这个偏好。
        connected = ConnectTo(backup_url);
        if (connected) {
            ESP_LOGW(TAG, "Using backup websocket: %s", backup_url.c_str());
        } else {
            websocket_->Close();
            connected = ConnectTo(url);
            if (connected) {
                // 备用不通、主地址反而通了：立刻把首选改回内网
                probe_state_->prefer_backup = false;
            }
        }
    } else {
        // 内网主地址永远是首选；备用只在主地址摸不到时接管本次连接
        connected = ConnectTo(url);
        if (connected) {
            probe_state_->prefer_backup = false;
        } else if (has_backup) {
            websocket_->Close();
            if (ConnectTo(backup_url)) {
                connected = true;
                ESP_LOGW(TAG, "Primary websocket %s failed, using backup: %s", url.c_str(),
                         backup_url.c_str());
                NoteBackupInUse(url);
            }
        }
    }

    if (!connected) {
        SetError(Lang::Strings::SERVER_NOT_CONNECTED);
        return false;
    }

    // Send hello message to describe the client
    auto message = GetHelloMessage();
    if (!SendText(message)) {
        return false;
    }

    // Wait for server hello
    EventBits_t bits =
        xEventGroupWaitBits(event_group_handle_, WEBSOCKET_PROTOCOL_SERVER_HELLO_EVENT, pdTRUE,
                            pdFALSE, pdMS_TO_TICKS(10000));
    if (!(bits & WEBSOCKET_PROTOCOL_SERVER_HELLO_EVENT)) {
        ESP_LOGE(TAG, "Failed to receive server hello");
        SetError(Lang::Strings::SERVER_TIMEOUT);
        return false;
    }

    if (on_audio_channel_opened_ != nullptr) {
        on_audio_channel_opened_();
    }

    return true;
}

std::string WebsocketProtocol::GetHelloMessage() {
    // keys: message type, version, audio_params (format, sample_rate, channels)
    cJSON* root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "type", "hello");
    cJSON_AddNumberToObject(root, "version", version_);
    cJSON* features = cJSON_CreateObject();
#if CONFIG_USE_SERVER_AEC
    cJSON_AddBoolToObject(features, "aec", true);
#endif
    cJSON_AddBoolToObject(features, "mcp", true);
    cJSON_AddItemToObject(root, "features", features);
    AddTextFontCapabilities(root);
    cJSON_AddStringToObject(root, "transport", "websocket");
    cJSON* audio_params = cJSON_CreateObject();
    cJSON_AddStringToObject(audio_params, "format", "opus");
    cJSON_AddNumberToObject(audio_params, "sample_rate", 16000);
    cJSON_AddNumberToObject(audio_params, "channels", 1);
    cJSON_AddNumberToObject(audio_params, "frame_duration", OPUS_FRAME_DURATION_MS);
    cJSON_AddItemToObject(root, "audio_params", audio_params);
    auto json_str = cJSON_PrintUnformatted(root);
    std::string message(json_str);
    cJSON_free(json_str);
    cJSON_Delete(root);
    return message;
}

void WebsocketProtocol::ParseServerHello(const cJSON* root) {
    auto transport = cJSON_GetObjectItem(root, "transport");
    if (transport == nullptr || strcmp(transport->valuestring, "websocket") != 0) {
        ESP_LOGE(TAG, "Unsupported transport: %s", transport->valuestring);
        return;
    }

    auto session_id = cJSON_GetObjectItem(root, "session_id");
    if (cJSON_IsString(session_id)) {
        session_id_ = session_id->valuestring;
        ESP_LOGI(TAG, "Session ID: %s", session_id_.c_str());
    }

    auto audio_params = cJSON_GetObjectItem(root, "audio_params");
    if (cJSON_IsObject(audio_params)) {
        auto sample_rate = cJSON_GetObjectItem(audio_params, "sample_rate");
        if (cJSON_IsNumber(sample_rate)) {
            server_sample_rate_ = sample_rate->valueint;
        }
        auto frame_duration = cJSON_GetObjectItem(audio_params, "frame_duration");
        if (cJSON_IsNumber(frame_duration)) {
            server_frame_duration_ = frame_duration->valueint;
        }
    }

    xEventGroupSetBits(event_group_handle_, WEBSOCKET_PROTOCOL_SERVER_HELLO_EVENT);
}
