#include "protocol.h"
#include "assets.h"

#include <esp_log.h>

#define TAG "Protocol"

void Protocol::AddTextFontCapabilities(cJSON* root) {
    auto capability = Assets::GetInstance().text_font_capability();
    cJSON* features = cJSON_GetObjectItem(root, "features");
    if (cJSON_IsObject(features)) {
        cJSON_AddBoolToObject(features, "glyph_push", capability.glyph_push);
    }

    if (!capability.glyph_push) {
        return;
    }
    cJSON* font = cJSON_CreateObject();
    cJSON_AddStringToObject(font, "bundle", capability.bundle.c_str());
    cJSON_AddStringToObject(font, "charset", capability.charset.c_str());
    cJSON_AddNumberToObject(font, "size", capability.size);
    cJSON_AddNumberToObject(font, "bpp", capability.bpp);
    cJSON_AddItemToObject(root, "text_font", font);
}

void Protocol::OnIncomingJson(std::function<void(const cJSON* root)> callback) {
    on_incoming_json_ = callback;
}

void Protocol::OnIncomingAudio(
    std::function<void(std::unique_ptr<AudioStreamPacket> packet)> callback) {
    on_incoming_audio_ = callback;
}

void Protocol::OnAudioChannelOpened(std::function<void()> callback) {
    on_audio_channel_opened_ = callback;
}

void Protocol::OnAudioChannelClosed(std::function<void()> callback) {
    on_audio_channel_closed_ = callback;
}

void Protocol::OnNetworkError(std::function<void(const std::string& message)> callback) {
    on_network_error_ = callback;
}

void Protocol::OnConnected(std::function<void()> callback) { on_connected_ = callback; }

void Protocol::OnDisconnected(std::function<void()> callback) { on_disconnected_ = callback; }

void Protocol::SetError(const std::string& message) {
    error_occurred_ = true;
    if (on_network_error_ != nullptr) {
        on_network_error_(message);
    }
}

void Protocol::SendAbortSpeaking(AbortReason reason) {
    std::string message = "{\"session_id\":\"" + session_id_ + "\",\"type\":\"abort\"";
    if (reason == kAbortReasonWakeWordDetected) {
        message += ",\"reason\":\"wake_word_detected\"";
    }
    message += "}";
    SendTextChecked(message, "abort");
}

void Protocol::SendWakeWordDetected(const std::string& wake_word) {
    std::string json = "{\"session_id\":\"" + session_id_ +
                       "\",\"type\":\"listen\",\"state\":\"detect\",\"text\":\"" + wake_word +
                       "\"}";
    SendTextChecked(json, "listen(detect)");
}

void Protocol::SendStartListening(ListeningMode mode) {
    std::string message = "{\"session_id\":\"" + session_id_ + "\"";
    message += ",\"type\":\"listen\",\"state\":\"start\"";
    if (mode == kListeningModeRealtime) {
        message += ",\"mode\":\"realtime\"";
    } else if (mode == kListeningModeAutoStop) {
        message += ",\"mode\":\"auto\"";
    } else {
        message += ",\"mode\":\"manual\"";
    }
    message += "}";
    SendTextChecked(message, "listen(start)");
}

void Protocol::SendStopListening() {
    std::string message =
        "{\"session_id\":\"" + session_id_ + "\",\"type\":\"listen\",\"state\":\"stop\"}";
    SendTextChecked(message, "listen(stop)");
}

void Protocol::SendMcpMessage(const std::string& payload) {
    std::string message =
        "{\"session_id\":\"" + session_id_ + "\",\"type\":\"mcp\",\"payload\":" + payload + "}";
    SendTextChecked(message, "mcp");
}

void Protocol::SendTextChecked(const std::string& message, const char* kind) {
    // 这五个 sender 都是 void——`SendText` 的失败在此前**无处可归**：调用方
    // 拿不到返回值，传输层丢掉时又不一定出声（issue #32：工具应答与 abort
    // 一起不翼而飞，服务端零记录，而设备侧一切正常）。
    //
    // 这里不改变任何行为（仍然是 void、不重试、不改状态），只把「哪一类
    // 消息被丢/被发」记到串口上。判读时对照服务端收到的类型：设备说发过而
    // 服务端没有的，就是被丢（或对端未记）的那一类。
    //
    // **为什么还要打「尝试发送」这条**：只记失败的话，复现时一条日志都没有
    // 会有两种截然相反的解释——「根本没走到这里」与「走到了、而且 Send()
    // 谎报成功」（半死的 TCP 上 `Send()` 可能返回真：字节进了缓冲，RST 还没
    // 被看到）。加上这条，串口静默才是一个可判读的信号。文本消息是
    // 用户事件频率（非每帧音频），代价可忽。
    ESP_LOGI(TAG, "Sending text: %s", kind);
    if (!SendText(message)) {
        // 传输层可能已经打过更具体的日志（见 websocket_protocol.cc 的
        // SendText）；这一条只负责说明**消息类型**，不重复原因。
        ESP_LOGW(TAG, "Outgoing text dropped: %s", kind);
    }
}

bool Protocol::IsTimeout() const {
    const int kTimeoutSeconds = 120;
    auto now = std::chrono::steady_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::seconds>(now - last_incoming_time_);
    bool timeout = duration.count() > kTimeoutSeconds;
    if (timeout) {
        ESP_LOGE(TAG, "Channel timeout %ld seconds", (long)duration.count());
    }
    return timeout;
}
