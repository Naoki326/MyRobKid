#include "application.h"
#include "assets.h"
#include "assets/lang_config.h"
#include "audio_codec.h"
#include "board.h"
#include "display.h"
#include "mcp_server.h"
#include "mqtt_protocol.h"
#include "settings.h"
#include "system_info.h"
#include "text_glyph_payload.h"
#include "websocket_protocol.h"

#include <driver/gpio.h>
#include <esp_log.h>
#include <arpa/inet.h>
#include <cJSON.h>
#include <cstring>
#include <limits>

#define TAG "Application"

// TTS playback strategy (see TTS_PLAYBACK_STRATEGY in Kconfig):
// - false: stream as it arrives, with a large jitter buffer (default)
// - true: buffer the whole reply and play it after tts stop
#if CONFIG_TTS_PLAYBACK_FULL_PREBUFFER
static constexpr bool kTtsPrebufferEnabled = true;
#else
static constexpr bool kTtsPrebufferEnabled = false;
#endif

// 会话性暂停的静默阈值（issue #4）：答完进聆听后连续这么多秒无人说话，
// 就回待机并自动续播。5s 是「用户还在想下一句」与「他其实说完了」之间的
// 折中：太短会打断追问，太长会让音乐接得莫名其妙。复用 1Hz tick，恰好整秒。
static constexpr int kPauseAutoResumeQuietTicks = 5;

Application::Application() : notify_player_(audio_service_), music_player_(audio_service_) {
    event_group_ = xEventGroupCreate();

#if CONFIG_USE_DEVICE_AEC && CONFIG_USE_SERVER_AEC
#error "CONFIG_USE_DEVICE_AEC and CONFIG_USE_SERVER_AEC cannot be enabled at the same time"
#elif CONFIG_USE_DEVICE_AEC
    aec_mode_ = kAecOnDeviceSide;
#elif CONFIG_USE_SERVER_AEC
    aec_mode_ = kAecOnServerSide;
#else
    aec_mode_ = kAecOff;
#endif

    esp_timer_create_args_t clock_timer_args = {.callback =
                                                    [](void* arg) {
                                                        Application* app = (Application*)arg;
                                                        xEventGroupSetBits(app->event_group_,
                                                                           MAIN_EVENT_CLOCK_TICK);
                                                    },
                                                .arg = this,
                                                .dispatch_method = ESP_TIMER_TASK,
                                                .name = "clock_timer",
                                                .skip_unhandled_events = true};
    esp_timer_create(&clock_timer_args, &clock_timer_handle_);
}

Application::~Application() {
    notify_player_.Stop();
    if (clock_timer_handle_ != nullptr) {
        esp_timer_stop(clock_timer_handle_);
        esp_timer_delete(clock_timer_handle_);
    }
    vEventGroupDelete(event_group_);
}

bool Application::SetDeviceState(DeviceState state) { return state_machine_.TransitionTo(state); }

void Application::Initialize() {
    auto& board = Board::GetInstance();
    SetDeviceState(kDeviceStateStarting);

    // Setup the display
    auto display = board.GetDisplay();
    display->SetupUI();
    // Print board name/version info
    display->SetChatMessage("system", SystemInfo::GetUserAgent().c_str());

    // Setup the audio service
    auto codec = board.GetAudioCodec();
    audio_service_.Initialize(codec);
    audio_service_.Start();

    AudioServiceCallbacks callbacks;
    callbacks.on_send_queue_available = [this]() {
        xEventGroupSetBits(event_group_, MAIN_EVENT_SEND_AUDIO);
    };
    callbacks.on_wake_word_detected = [this](const std::string& wake_word) {
        xEventGroupSetBits(event_group_, MAIN_EVENT_WAKE_WORD_DETECTED);
    };
    callbacks.on_vad_change = [this](bool speaking) {
        xEventGroupSetBits(event_group_, MAIN_EVENT_VAD_CHANGE);
    };
    callbacks.on_playback_drained = [this]() {
        xEventGroupSetBits(event_group_, MAIN_EVENT_PLAYBACK_DRAINED);
    };
    callbacks.on_playback_progress = [this](uint32_t playback_id, uint32_t media_position_ms) {
        notify_player_.OnPlaybackProgress(playback_id, media_position_ms);
    };
    audio_service_.SetCallbacks(callbacks);

    // Add state change listeners
    state_machine_.AddStateChangeListener([this](DeviceState old_state, DeviceState new_state) {
        xEventGroupSetBits(event_group_, MAIN_EVENT_STATE_CHANGED);
    });

    // Start the clock timer to update the status bar
    esp_timer_start_periodic(clock_timer_handle_, 1000000);

    // Add MCP common tools (only once during initialization)
    auto& mcp_server = McpServer::GetInstance();
    mcp_server.AddCommonTools();
    mcp_server.AddUserOnlyTools();

    // Set network event callback for UI updates and network state handling
    board.SetNetworkEventCallback([this](NetworkEvent event, const std::string& data) {
        auto display = Board::GetInstance().GetDisplay();

        switch (event) {
            case NetworkEvent::Scanning:
                display->ShowNotification(Lang::Strings::SCANNING_WIFI, 30000);
                xEventGroupSetBits(event_group_, MAIN_EVENT_NETWORK_DISCONNECTED);
                break;
            case NetworkEvent::Connecting: {
                if (data.empty()) {
                    // Cellular network - registering without carrier info yet
                    display->SetStatus(Lang::Strings::REGISTERING_NETWORK);
                } else {
                    // WiFi or cellular with carrier info
                    std::string msg = Lang::Strings::CONNECT_TO;
                    msg += data;
                    msg += "...";
                    display->ShowNotification(msg.c_str(), 30000);
                }
                break;
            }
            case NetworkEvent::Connected: {
                std::string msg = Lang::Strings::CONNECTED_TO;
                msg += data;
                display->ShowNotification(msg.c_str(), 30000);
                xEventGroupSetBits(event_group_, MAIN_EVENT_NETWORK_CONNECTED);
                break;
            }
            case NetworkEvent::Disconnected:
                xEventGroupSetBits(event_group_, MAIN_EVENT_NETWORK_DISCONNECTED);
                break;
            case NetworkEvent::WifiConfigModeEnter:
                // WiFi config mode enter is handled by WifiBoard internally
                break;
            case NetworkEvent::WifiConfigModeExit:
                // WiFi config mode exit is handled by WifiBoard internally
                break;
            // Cellular modem specific events
            case NetworkEvent::ModemDetecting:
                display->SetStatus(Lang::Strings::DETECTING_MODULE);
                break;
            case NetworkEvent::ModemErrorNoSim:
                Alert(Lang::Strings::ERROR, Lang::Strings::PIN_ERROR, "warning",
                      Lang::Sounds::OGG_ERR_PIN);
                break;
            case NetworkEvent::ModemErrorRegDenied:
                Alert(Lang::Strings::ERROR, Lang::Strings::REG_ERROR, "warning",
                      Lang::Sounds::OGG_ERR_REG);
                break;
            case NetworkEvent::ModemErrorInitFailed:
                Alert(Lang::Strings::ERROR, Lang::Strings::MODEM_INIT_ERROR, "warning",
                      Lang::Sounds::OGG_EXCLAMATION);
                break;
            case NetworkEvent::ModemErrorTimeout:
                display->SetStatus(Lang::Strings::REGISTERING_NETWORK);
                break;
        }
    });

    // Start network asynchronously
    board.StartNetwork();

    // Update the status bar immediately to show the network state
    display->UpdateStatusBar(true);
}

void Application::Run() {
    // Set the priority of the main task to 10
    vTaskPrioritySet(nullptr, 10);

    const EventBits_t ALL_EVENTS =
        MAIN_EVENT_SCHEDULE | MAIN_EVENT_SEND_AUDIO | MAIN_EVENT_WAKE_WORD_DETECTED |
        MAIN_EVENT_VAD_CHANGE | MAIN_EVENT_CLOCK_TICK | MAIN_EVENT_ERROR |
        MAIN_EVENT_NETWORK_CONNECTED | MAIN_EVENT_NETWORK_DISCONNECTED | MAIN_EVENT_TOGGLE_CHAT |
        MAIN_EVENT_START_LISTENING | MAIN_EVENT_STOP_LISTENING | MAIN_EVENT_ACTIVATION_DONE |
        MAIN_EVENT_STATE_CHANGED | MAIN_EVENT_PLAYBACK_DRAINED;

    while (true) {
        auto bits = xEventGroupWaitBits(event_group_, ALL_EVENTS, pdTRUE, pdFALSE, portMAX_DELAY);

        if (bits & MAIN_EVENT_ERROR) {
            if (GetDeviceState() == kDeviceStateNotifying) {
                StopNotification();
            }
            SetDeviceState(kDeviceStateIdle);
            Alert(Lang::Strings::ERROR, last_error_message_.c_str(), "cancel",
                  Lang::Sounds::OGG_EXCLAMATION);
        }

        if (bits & MAIN_EVENT_NETWORK_CONNECTED) {
            HandleNetworkConnectedEvent();
        }

        if (bits & MAIN_EVENT_NETWORK_DISCONNECTED) {
            HandleNetworkDisconnectedEvent();
        }

        if (bits & MAIN_EVENT_ACTIVATION_DONE) {
            HandleActivationDoneEvent();
        }

        if (bits & MAIN_EVENT_STATE_CHANGED) {
            HandleStateChangedEvent();
        }

        if (bits & MAIN_EVENT_PLAYBACK_DRAINED) {
            if (audio_service_.IsPlaybackIdle()) {
                notify_player_.OnPlaybackDrained();
            }
            // Deferred listening start (auto mode): the playback queue has
            // drained, so it is now safe to enable voice processing.
            if (pending_listening_start_ && GetDeviceState() == kDeviceStateListening &&
                audio_service_.IsPlaybackIdle()) {
                pending_listening_start_ = false;
                StartListeningAudio();
            }
        }

        if (bits & MAIN_EVENT_TOGGLE_CHAT) {
            HandleToggleChatEvent();
        }

        if (bits & MAIN_EVENT_START_LISTENING) {
            HandleStartListeningEvent();
        }

        if (bits & MAIN_EVENT_STOP_LISTENING) {
            HandleStopListeningEvent();
        }

        if (bits & MAIN_EVENT_SEND_AUDIO) {
            while (auto packet = audio_service_.PopPacketFromSendQueue()) {
                if (protocol_ && !protocol_->SendAudio(std::move(packet))) {
                    // Drop the remaining packets. Leaving them in the queue would
                    // stall the Opus codec task (it waits for queue space), which in
                    // turn deadlocks the whole audio input pipeline, as no new
                    // MAIN_EVENT_SEND_AUDIO event would ever be triggered again.
                    while (audio_service_.PopPacketFromSendQueue())
                        ;
                    break;
                }
            }
        }

        if (bits & MAIN_EVENT_WAKE_WORD_DETECTED) {
            HandleWakeWordDetectedEvent();
        }

        if (bits & MAIN_EVENT_VAD_CHANGE) {
            if (GetDeviceState() == kDeviceStateListening) {
                auto led = Board::GetInstance().GetLed();
                led->OnStateChanged();
                // 静默期内用户再说话：这一轮的自动续播作废（不打断追问）。
                // VAD 说「有人在说」就足以取消——等他真说完再判会晚一整轮。
                if (audio_service_.IsVoiceDetected()) {
                    pause_user_spoke_ = true;
                    if (auto_resume_armed_ && pause_quiet_ticks_ > 0) {
                        ESP_LOGI(TAG, "Music auto-resume cancelled: user speaking");
                    }
                    auto_resume_armed_ = false;
                    pause_quiet_ticks_ = 0;
                }
            }
        }

        if (bits & MAIN_EVENT_SCHEDULE) {
            std::unique_lock<std::mutex> lock(mutex_);
            auto tasks = std::move(main_tasks_);
            lock.unlock();
            for (auto& task : tasks) {
                task();
            }
        }

        if (bits & MAIN_EVENT_CLOCK_TICK) {
            clock_ticks_++;
            auto display = Board::GetInstance().GetDisplay();
            display->UpdateStatusBar();

            // 会话性暂停的自动续播（issue #4）：TTS 说完 → 进聆听 → 静默
            // 计数 → 数秒仍无人说话则回待机并续播。用现成的 1Hz tick，不另
            // 起定时器；**不依赖「设备自然回待机」**（那条路是服务端 120s
            // 无语音超时，中途还会触发 end_prompt 让机器人说一句告别语）。
            UpdatePauseAutoResume();

            // Print debug info every 10 seconds
            if (clock_ticks_ % 10 == 0) {
                SystemInfo::PrintHeapStats();
                // SystemInfo::PrintTaskList();
                // SystemInfo::PrintTaskCpuUsage(pdMS_TO_TICKS(1000));
            }
        }
    }
}

void Application::HandleNetworkConnectedEvent() {
    ESP_LOGI(TAG, "Network connected");
    auto state = GetDeviceState();

    if (state == kDeviceStateStarting || state == kDeviceStateWifiConfiguring) {
        // Network is ready, start activation
        SetDeviceState(kDeviceStateActivating);
        if (activation_task_handle_ != nullptr) {
            ESP_LOGW(TAG, "Activation task already running");
            return;
        }

        xTaskCreate(
            [](void* arg) {
                Application* app = static_cast<Application*>(arg);
                app->ActivationTask();
                app->activation_task_handle_ = nullptr;
                vTaskDelete(NULL);
            },
            "activation", 4096 * 2, this, 2, &activation_task_handle_);
    }

    // Update the status bar immediately to show the network state
    auto display = Board::GetInstance().GetDisplay();
    display->UpdateStatusBar(true);
}

void Application::HandleNetworkDisconnectedEvent() {
    // Close current conversation when network disconnected
    auto state = GetDeviceState();
    if (state == kDeviceStateNotifying) {
        StopNotification();
    }
    if (state == kDeviceStateConnecting || state == kDeviceStateListening ||
        state == kDeviceStateSpeaking) {
        ESP_LOGI(TAG, "Closing audio channel due to network disconnection");
        protocol_->CloseAudioChannel();
    }

    // Update the status bar immediately to show the network state
    auto display = Board::GetInstance().GetDisplay();
    display->UpdateStatusBar(true);
}

void Application::HandleActivationDoneEvent() {
    ESP_LOGI(TAG, "Activation done");

    SystemInfo::PrintHeapStats();
    SetDeviceState(kDeviceStateIdle);

    has_server_time_ = ota_->HasServerTime();

    auto display = Board::GetInstance().GetDisplay();
    std::string message = std::string(Lang::Strings::VERSION) + ota_->GetCurrentVersion();
    display->ShowNotification(message.c_str());
    display->SetChatMessage("system", "");

    // Release OTA object after activation is complete
    ota_.reset();
    auto& board = Board::GetInstance();
    board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);

    Schedule([this]() {
        // Play the success sound to indicate the device is ready
        audio_service_.PlaySound(Lang::Sounds::OGG_SUCCESS);
    });
}

void Application::ActivationTask() {
    // Create OTA object for activation process
    ota_ = std::make_unique<Ota>();

    // Check for new assets version
    CheckAssetsVersion();

    // Check for new firmware version
    CheckNewVersion();

    // Initialize the protocol
    InitializeProtocol();

    // Signal completion to main loop
    xEventGroupSetBits(event_group_, MAIN_EVENT_ACTIVATION_DONE);
}

void Application::CheckAssetsVersion() {
    // Only allow CheckAssetsVersion to be called once
    if (assets_version_checked_) {
        return;
    }
    assets_version_checked_ = true;

    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    auto& assets = Assets::GetInstance();

    if (!assets.partition_valid()) {
        ESP_LOGW(TAG, "Assets partition is disabled for board %s", BOARD_NAME);
        return;
    }

    Settings settings("assets", true);
    // Check if there is a new assets need to be downloaded
    std::string download_url = settings.GetString("download_url");

    if (!download_url.empty()) {
        settings.EraseKey("download_url");

        char message[256];
        snprintf(message, sizeof(message), Lang::Strings::FOUND_NEW_ASSETS, download_url.c_str());
        Alert(Lang::Strings::LOADING_ASSETS, message, "cloud_download", Lang::Sounds::OGG_UPGRADE);

        // Wait for the audio service to be idle for 3 seconds
        vTaskDelay(pdMS_TO_TICKS(3000));
        SetDeviceState(kDeviceStateUpgrading);
        board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
        display->SetChatMessage("system", Lang::Strings::PLEASE_WAIT);

        bool success =
            assets.Download(download_url, [this, display](int progress, size_t speed) -> void {
                char buffer[32];
                snprintf(buffer, sizeof(buffer), "%d%% %uKB/s", progress, speed / 1024);
                Schedule([display, message = std::string(buffer)]() {
                    display->SetChatMessage("system", message.c_str());
                });
            });

        board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
        vTaskDelay(pdMS_TO_TICKS(1000));

        if (!success) {
            Alert(Lang::Strings::ERROR, Lang::Strings::DOWNLOAD_ASSETS_FAILED, "cancel",
                  Lang::Sounds::OGG_EXCLAMATION);
            vTaskDelay(pdMS_TO_TICKS(2000));
            SetDeviceState(kDeviceStateActivating);
            return;
        }
    }

    // Apply assets
    assets.Apply();
    display->SetChatMessage("system", "");
    display->SetEmotion("robot_2");
}

void Application::CheckNewVersion() {
    const int MAX_RETRY = 10;
    int retry_count = 0;
    int retry_delay = 10;  // Initial retry delay in seconds

    auto& board = Board::GetInstance();
    while (true) {
        auto display = board.GetDisplay();
        display->SetStatus(Lang::Strings::CHECKING_NEW_VERSION);

        esp_err_t err = ota_->CheckVersion();
        if (err != ESP_OK) {
            retry_count++;
            if (retry_count >= MAX_RETRY) {
                ESP_LOGE(TAG, "Too many retries, exit version check");
                return;
            }

            char error_message[128];
            int error_message_length =
                snprintf(error_message, sizeof(error_message), "code=%d, url=%s", err,
                         ota_->GetCheckVersionUrl().c_str());
            if (error_message_length < 0 ||
                error_message_length >= static_cast<int>(sizeof(error_message))) {
                snprintf(error_message, sizeof(error_message), "code=%d", err);
            }

            char buffer[320];
            int alert_message_length =
                snprintf(buffer, sizeof(buffer), Lang::Strings::CHECK_NEW_VERSION_FAILED,
                         retry_delay, error_message);
            if (alert_message_length < 0 ||
                alert_message_length >= static_cast<int>(sizeof(buffer))) {
                snprintf(buffer, sizeof(buffer), "code=%d", err);
            }
            Alert(Lang::Strings::ERROR, buffer, "cloud_off", Lang::Sounds::OGG_EXCLAMATION);

            ESP_LOGW(TAG, "Check new version failed, retry in %d seconds (%d/%d)", retry_delay,
                     retry_count, MAX_RETRY);
            for (int i = 0; i < retry_delay; i++) {
                vTaskDelay(pdMS_TO_TICKS(1000));
                if (GetDeviceState() == kDeviceStateIdle) {
                    break;
                }
            }
            retry_delay *= 2;  // Double the retry delay
            continue;
        }
        retry_count = 0;
        retry_delay = 10;  // Reset retry delay

        if (ota_->HasNewVersion()) {
            if (UpgradeFirmware(ota_->GetFirmwareUrl(), ota_->GetFirmwareVersion())) {
                return;  // This line will never be reached after reboot
            }
            // If upgrade failed, continue to normal operation
        }

        // No new version, mark the current version as valid
        ota_->MarkCurrentVersionValid();
        if (!ota_->HasActivationCode() && !ota_->HasActivationChallenge()) {
            // Exit the loop if done checking new version
            break;
        }

        display->SetStatus(Lang::Strings::ACTIVATION);
        // Activation code is shown to the user and waiting for the user to input
        if (ota_->HasActivationCode()) {
            ShowActivationCode(ota_->GetActivationCode(), ota_->GetActivationMessage());
        }

        // This will block the loop until the activation is done or timeout
        for (int i = 0; i < 10; ++i) {
            ESP_LOGI(TAG, "Activating... %d/%d", i + 1, 10);
            esp_err_t err = ota_->Activate();
            if (err == ESP_OK) {
                break;
            } else if (err == ESP_ERR_TIMEOUT) {
                vTaskDelay(pdMS_TO_TICKS(3000));
            } else {
                vTaskDelay(pdMS_TO_TICKS(10000));
            }
            if (GetDeviceState() == kDeviceStateIdle) {
                break;
            }
        }
    }
}

void Application::InitializeProtocol() {
    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    auto codec = board.GetAudioCodec();

    display->SetStatus(Lang::Strings::LOADING_PROTOCOL);

    if (ota_->HasMqttConfig()) {
        protocol_ = std::make_unique<MqttProtocol>();
    } else if (ota_->HasWebsocketConfig()) {
        protocol_ = std::make_unique<WebsocketProtocol>();
    } else {
        ESP_LOGW(TAG, "No protocol specified in the OTA config, using MQTT");
        protocol_ = std::make_unique<MqttProtocol>();
    }

    protocol_->OnConnected([this]() { DismissAlert(); });

    protocol_->OnNetworkError([this](const std::string& message) {
        last_error_message_ = message;
        xEventGroupSetBits(event_group_, MAIN_EVENT_ERROR);
    });

    protocol_->OnIncomingAudio([this](std::unique_ptr<AudioStreamPacket> packet) {
        bool buffered = false;
        bool overflow = false;
        {
            std::lock_guard<std::mutex> lock(tts_buffer_mutex_);
            if (tts_buffering_) {
                tts_buffer_bytes_ += packet->payload.size();
                tts_buffer_.push_back(std::move(packet));
                buffered = true;
                overflow = tts_buffer_bytes_ >= kTtsPreloadMaxBytes;
            }
        }
        if (buffered) {
            if (overflow) {
                // Extremely long reply: enqueue what we have and stream the
                // rest directly, same as the original behavior.
                FlushTtsBuffer();
            }
            return;
        }
        if (GetDeviceState() == kDeviceStateSpeaking) {
            audio_service_.PushPacketToDecodeQueue(std::move(packet));
        }
    });

    protocol_->OnAudioChannelOpened([this, codec, &board]() {
        board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
        if (protocol_->server_sample_rate() != codec->output_sample_rate()) {
            ESP_LOGW(TAG,
                     "Server sample rate %d does not match device output sample rate %d, "
                     "resampling may cause distortion",
                     protocol_->server_sample_rate(), codec->output_sample_rate());
        }
    });

    protocol_->OnAudioChannelClosed([this, &board]() {
        // Music keeps streaming after the conversation ends: keep Wi-Fi at
        // full performance while the music player is feeding the speaker.
        // 暂停也算「还占着」：worker 与那条连接都还在，恢复时不许被省电档
        // 撕裂（issue #4 的省电判定与 :1345 处同一谓词，不各写一份）。
        if (!IsMusicBusy()) {
            board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
        }
        ResetTtsBuffer();
        Schedule([this]() {
            auto display = Board::GetInstance().GetDisplay();
            // 音乐会话还在（在播或暂停）时，这条路径**不**清消息区：这是
            // 服务端 120s 无语音超时的收尾，音乐本身没结束，屏幕上的曲目是
            // 用户唯一的「它在放」凭据。清掉它，剩下两个音乐出口（工具/日志）
            // 都看不见——那就是本 spec 要修的那个「屏在说谎」。
            if (!IsMusicBusy()) {
                RepaintOrClearMusicScreen([display]() {
                    display->SetChatMessage("system", "");
                });
            }
            // 会话结束了：静默期不存在了，自动续播作废（issue #4 的取消条件
            // 之一）。音乐留在暂停态——唤醒词已恢复，用户可以说「继续」。
            CancelPauseAutoResume();
            SetDeviceState(kDeviceStateIdle);
        });
    });

    protocol_->OnIncomingJson([this, display](const cJSON* root) {
        // Parse JSON data
        auto type = cJSON_GetObjectItem(root, "type");
        if (!cJSON_IsString(type)) {
            ESP_LOGW(TAG, "Incoming JSON message has no type");
            return;
        }
        if (strcmp(type->valuestring, "notify") == 0) {
            auto audio_url = cJSON_GetObjectItem(root, "audio_url");
            if (!cJSON_IsString(audio_url) || audio_url->valuestring[0] == '\0') {
                ESP_LOGW(TAG, "Notify message requires audio_url");
                return;
            }

            std::vector<NotifySubtitle> subtitles;
            auto subtitles_json = cJSON_GetObjectItem(root, "subtitles");
            if (subtitles_json != nullptr && !cJSON_IsArray(subtitles_json)) {
                ESP_LOGW(TAG, "Notify subtitles must be an array");
                return;
            }
            if (cJSON_IsArray(subtitles_json)) {
                cJSON* item = nullptr;
                cJSON_ArrayForEach (item, subtitles_json) {
                    auto start_ms = cJSON_GetObjectItem(item, "start_ms");
                    auto text = cJSON_GetObjectItem(item, "text");
                    if (!cJSON_IsNumber(start_ms) || start_ms->valuedouble < 0 ||
                        start_ms->valuedouble > std::numeric_limits<uint32_t>::max() ||
                        !cJSON_IsString(text)) {
                        ESP_LOGW(TAG, "Ignoring invalid notify subtitle");
                        continue;
                    }
                    subtitles.push_back({.start_ms = static_cast<uint32_t>(start_ms->valuedouble),
                                         .text = text->valuestring});
                }
            }

            Schedule([this, url = std::string(audio_url->valuestring),
                      subtitles = std::move(subtitles)]() mutable {
                StartNotification(std::move(url), std::move(subtitles));
            });
        } else if (strcmp(type->valuestring, "tts") == 0) {
            auto state = cJSON_GetObjectItem(root, "state");
            if (!cJSON_IsString(state)) {
                return;
            }
            if (strcmp(state->valuestring, "start") == 0) {
                {
                    // Drop any leftover audio. Buffer the response only when
                    // the full-prebuffer strategy is selected; otherwise
                    // packets stream straight to the decode queue.
                    std::lock_guard<std::mutex> lock(tts_buffer_mutex_);
                    tts_buffer_.clear();
                    tts_buffer_bytes_ = 0;
                    tts_buffering_ = kTtsPrebufferEnabled;
                }
                Schedule([this]() {
                    aborted_ = false;
                    SetDeviceState(kDeviceStateSpeaking);
                });
            } else if (strcmp(state->valuestring, "stop") == 0) {
                // Flush the buffered audio before the state change so that
                // playback starts with the complete response.
                FlushTtsBuffer();
                Schedule([this]() {
                    if (GetDeviceState() == kDeviceStateSpeaking) {
                        if (!pending_music_url_.empty()) {
                            // The reply finished speaking: start the deferred
                            // music now (and stay in idle — no listening while
                            // the music plays).
                            LaunchPendingMusic();
                        } else if (pending_music_resume_) {
                            // 用户在答话途中说了「继续」：现在这句答完了，
                            // 按位点接上（与换歌同一时刻、同一处收口）。
                            pending_music_resume_ = false;
                            ResumeMusicNow();
                        } else if (listening_mode_ == kListeningModeManualStop) {
                            SetDeviceState(kDeviceStateIdle);
                        } else {
                            SetDeviceState(kDeviceStateListening);
                        }
                    }
                });
            } else if (strcmp(state->valuestring, "sentence_start") == 0) {
                auto text = cJSON_GetObjectItem(root, "text");
                if (cJSON_IsString(text)) {
                    std::vector<TextGlyph> glyphs;
                    uint8_t bpp = 0;
                    if (!TextGlyphPayload::Parse(root, glyphs, bpp)) {
                        glyphs.clear();
                    }
                    ESP_LOGI(TAG, "<< %s", text->valuestring);
                    Schedule([display, message = std::string(text->valuestring),
                              glyphs = std::move(glyphs), bpp]() {
                        display->AddTextGlyphs(glyphs, bpp);
                        display->SetChatMessage("assistant", message.c_str());
                    });
                }
            }
        } else if (strcmp(type->valuestring, "stt") == 0) {
            auto text = cJSON_GetObjectItem(root, "text");
            if (cJSON_IsString(text)) {
                std::vector<TextGlyph> glyphs;
                uint8_t bpp = 0;
                if (!TextGlyphPayload::Parse(root, glyphs, bpp)) {
                    glyphs.clear();
                }
                ESP_LOGI(TAG, ">> %s", text->valuestring);
                Schedule([display, message = std::string(text->valuestring),
                          glyphs = std::move(glyphs), bpp]() {
                    display->AddTextGlyphs(glyphs, bpp);
                    display->SetChatMessage("user", message.c_str());
                });
            }
        } else if (strcmp(type->valuestring, "llm") == 0) {
            auto emotion = cJSON_GetObjectItem(root, "emotion");
            if (cJSON_IsString(emotion)) {
                Schedule([display, emotion_str = std::string(emotion->valuestring)]() {
                    display->SetEmotion(emotion_str.c_str());
                });
            }
        } else if (strcmp(type->valuestring, "mcp") == 0) {
            auto payload = cJSON_GetObjectItem(root, "payload");
            if (cJSON_IsObject(payload)) {
                McpServer::GetInstance().ParseMessage(payload);
            }
        } else if (strcmp(type->valuestring, "system") == 0) {
            auto command = cJSON_GetObjectItem(root, "command");
            if (cJSON_IsString(command)) {
                ESP_LOGI(TAG, "System command: %s", command->valuestring);
                if (strcmp(command->valuestring, "reboot") == 0) {
                    // Do a reboot if user requests a OTA update
                    Schedule([this]() { Reboot(); });
                } else {
                    ESP_LOGW(TAG, "Unknown system command: %s", command->valuestring);
                }
            }
        } else if (strcmp(type->valuestring, "alert") == 0) {
            auto status = cJSON_GetObjectItem(root, "status");
            auto message = cJSON_GetObjectItem(root, "message");
            auto emotion = cJSON_GetObjectItem(root, "emotion");
            if (cJSON_IsString(status) && cJSON_IsString(message) && cJSON_IsString(emotion)) {
                Alert(status->valuestring, message->valuestring, emotion->valuestring,
                      Lang::Sounds::OGG_VIBRATION);
            } else {
                ESP_LOGW(TAG, "Alert command requires status, message and emotion");
            }
#if CONFIG_RECEIVE_CUSTOM_MESSAGE
        } else if (strcmp(type->valuestring, "custom") == 0) {
            auto payload = cJSON_GetObjectItem(root, "payload");
            ESP_LOGI(TAG, "Received custom message: %s", cJSON_PrintUnformatted(root));
            if (cJSON_IsObject(payload)) {
                Schedule(
                    [this, display, payload_str = std::string(cJSON_PrintUnformatted(payload))]() {
                        display->SetChatMessage("system", payload_str.c_str());
                    });
            } else {
                ESP_LOGW(TAG, "Invalid custom message format: missing payload");
            }
#endif
        } else {
            ESP_LOGW(TAG, "Unknown message type: %s", type->valuestring);
        }
    });

    protocol_->Start();
}

void Application::ShowActivationCode(const std::string& code, const std::string& message) {
    struct digit_sound {
        char digit;
        const std::string_view& sound;
    };
    static const std::array<digit_sound, 10> digit_sounds{
        {digit_sound{'0', Lang::Sounds::OGG_0}, digit_sound{'1', Lang::Sounds::OGG_1},
         digit_sound{'2', Lang::Sounds::OGG_2}, digit_sound{'3', Lang::Sounds::OGG_3},
         digit_sound{'4', Lang::Sounds::OGG_4}, digit_sound{'5', Lang::Sounds::OGG_5},
         digit_sound{'6', Lang::Sounds::OGG_6}, digit_sound{'7', Lang::Sounds::OGG_7},
         digit_sound{'8', Lang::Sounds::OGG_8}, digit_sound{'9', Lang::Sounds::OGG_9}}};

    // This sentence uses 9KB of SRAM, so we need to wait for it to finish
    Alert(Lang::Strings::ACTIVATION, message.c_str(), "link", Lang::Sounds::OGG_ACTIVATION);

    for (const auto& digit : code) {
        auto it = std::find_if(digit_sounds.begin(), digit_sounds.end(),
                               [digit](const digit_sound& ds) { return ds.digit == digit; });
        if (it != digit_sounds.end()) {
            audio_service_.PlaySound(it->sound);
        }
    }
}

void Application::Alert(const char* status, const char* message, const char* emotion,
                        const std::string_view& sound) {
    ESP_LOGW(TAG, "Alert [%s] %s: %s", emotion, status, message);
    auto display = Board::GetInstance().GetDisplay();
    display->SetStatus(status);
    display->SetEmotion(emotion);
    display->SetChatMessage("system", message);
    if (!sound.empty()) {
        audio_service_.PlaySound(sound);
    }
}

void Application::DismissAlert() {
    if (GetDeviceState() == kDeviceStateIdle) {
        auto display = Board::GetInstance().GetDisplay();
        display->SetStatus(Lang::Strings::STANDBY);
        display->SetEmotion("neutral");
        // 音乐还在时那块区域的内容是曲目，不是告警文案的灰烬（issue #8）：
        // 重画而不是清空，否则「刚一连上服务端、屏幕就空了」——而音乐没停。
        RepaintOrClearMusicScreen([display]() {
            display->SetChatMessage("system", "");
        });
    }
}

void Application::ToggleChatState() { xEventGroupSetBits(event_group_, MAIN_EVENT_TOGGLE_CHAT); }

void Application::StartListening() { xEventGroupSetBits(event_group_, MAIN_EVENT_START_LISTENING); }

void Application::StopListening() { xEventGroupSetBits(event_group_, MAIN_EVENT_STOP_LISTENING); }

void Application::HandleToggleChatEvent() {
    auto state = GetDeviceState();

    if (state == kDeviceStateNotifying) {
        StopNotification();
        state = kDeviceStateIdle;
    }

    // 让位优先于停止（issue #4）：唤醒/新对话只**暂停**音乐（会话性暂停），
    // 答完静默数秒自动接上；真停止只由 stop_music 工具触发（StopMusic）。
    // 按钮打断与唤醒词走同一路径，行为自动一致。
    PauseMusic(PauseKind::kConversation);
    // 新一轮对话作废上一轮登记的延迟起播/延迟续播（陈旧状态不清会意外起播）。
    Schedule([this]() {
        pending_music_url_.clear();
        pending_music_resume_ = false;
    });

    if (state == kDeviceStateActivating) {
        SetDeviceState(kDeviceStateIdle);
        return;
    } else if (state == kDeviceStateWifiConfiguring) {
        audio_service_.EnableAudioTesting(true);
        SetDeviceState(kDeviceStateAudioTesting);
        return;
    } else if (state == kDeviceStateAudioTesting) {
        audio_service_.EnableAudioTesting(false);
        SetDeviceState(kDeviceStateWifiConfiguring);
        return;
    }

    if (!protocol_) {
        ESP_LOGE(TAG, "Protocol not initialized");
        return;
    }

    if (state == kDeviceStateIdle) {
        ListeningMode mode = GetDefaultListeningMode();
        if (!protocol_->IsAudioChannelOpened()) {
            SetDeviceState(kDeviceStateConnecting);
            // Schedule to let the state change be processed first (UI update)
            Schedule([this, mode]() { ContinueOpenAudioChannel(mode); });
            return;
        }
        SetListeningMode(mode);
    } else if (state == kDeviceStateSpeaking) {
        AbortSpeaking(kAbortReasonNone);
    } else if (state == kDeviceStateListening) {
        protocol_->CloseAudioChannel();
    }
}

void Application::ContinueOpenAudioChannel(ListeningMode mode) {
    // Check state again in case it was changed during scheduling
    if (GetDeviceState() != kDeviceStateConnecting) {
        return;
    }

    // Switch to performance mode before connecting to reduce latency
    auto& board = Board::GetInstance();
    board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);

    if (!protocol_->IsAudioChannelOpened()) {
        if (!protocol_->OpenAudioChannel()) {
            // Return to idle so the device is not stuck in the connecting
            // state (not every failure path reports a network error)
            SetDeviceState(kDeviceStateIdle);
            return;
        }
    }

    SetListeningMode(mode);
}

void Application::HandleStartListeningEvent() {
    auto state = GetDeviceState();

    if (state == kDeviceStateNotifying) {
        StopNotification();
        state = kDeviceStateIdle;
    }

    // 让位优先于停止（issue #4）：先暂停（放着的话），再说下一步。
    PauseMusic(PauseKind::kConversation);
    // 新一轮对话作废上一轮登记的延迟起播/延迟续播。
    Schedule([this]() {
        pending_music_url_.clear();
        pending_music_resume_ = false;
    });

    if (state == kDeviceStateActivating) {
        SetDeviceState(kDeviceStateIdle);
        return;
    } else if (state == kDeviceStateWifiConfiguring) {
        audio_service_.EnableAudioTesting(true);
        SetDeviceState(kDeviceStateAudioTesting);
        return;
    }

    if (!protocol_) {
        ESP_LOGE(TAG, "Protocol not initialized");
        return;
    }

    if (state == kDeviceStateIdle) {
        if (!protocol_->IsAudioChannelOpened()) {
            SetDeviceState(kDeviceStateConnecting);
            // Schedule to let the state change be processed first (UI update)
            Schedule([this]() { ContinueOpenAudioChannel(kListeningModeManualStop); });
            return;
        }
        SetListeningMode(kListeningModeManualStop);
    } else if (state == kDeviceStateSpeaking) {
        AbortSpeaking(kAbortReasonNone);
        SetListeningMode(kListeningModeManualStop);
    }
}

void Application::HandleStopListeningEvent() {
    auto state = GetDeviceState();

    if (state == kDeviceStateNotifying) {
        StopNotification();
    } else if (state == kDeviceStateAudioTesting) {
        audio_service_.EnableAudioTesting(false);
        SetDeviceState(kDeviceStateWifiConfiguring);
        return;
    } else if (state == kDeviceStateListening) {
        if (protocol_) {
            protocol_->SendStopListening();
        }
        SetDeviceState(kDeviceStateIdle);
    }
}

void Application::HandleWakeWordDetectedEvent() {
    if (!protocol_) {
        return;
    }

    auto state = GetDeviceState();
    auto wake_word = audio_service_.GetLastWakeWord();
    ESP_LOGI(TAG, "Wake word detected: %s (state: %d)", wake_word.c_str(), (int)state);

    if (state == kDeviceStateIdle) {
        BeginWakeWordInvoke(wake_word);
    } else if (state == kDeviceStateNotifying) {
        StopNotification();
        BeginWakeWordInvoke(wake_word);
    } else if (state == kDeviceStateSpeaking || state == kDeviceStateListening) {
        AbortSpeaking(kAbortReasonWakeWordDetected);
        // Clear send queue to avoid sending residues to server
        while (audio_service_.PopPacketFromSendQueue())
            ;

        if (state == kDeviceStateListening) {
            protocol_->SendStartListening(GetDefaultListeningMode());
            audio_service_.ResetDecoder();
            audio_service_.PlaySound(Lang::Sounds::OGG_POPUP);
            // Re-enable wake word detection as it was stopped by the detection itself
            audio_service_.EnableWakeWordDetection(true);
        } else {
            // Play popup sound and start listening again
            play_popup_on_listening_ = true;
            SetListeningMode(GetDefaultListeningMode());
        }
    } else if (state == kDeviceStateActivating) {
        // Restart the activation check if the wake word is detected during activation
        SetDeviceState(kDeviceStateIdle);
    }
}

void Application::BeginWakeWordInvoke(const std::string& wake_word) {
    // Must run in the main task with the device in idle state
    audio_service_.EncodeWakeWord();

    // Always pass through the connecting state, even if the audio channel is
    // already opened. ContinueWakeWordInvoke() rejects any other state, so
    // skipping this transition would silently drop the wake word invocation.
    if (!SetDeviceState(kDeviceStateConnecting)) {
        // Wake word detection was stopped by the detection itself; restore it
        // so the device does not become unresponsive to wake words.
        audio_service_.EnableWakeWordDetection(true);
        return;
    }

    if (!protocol_->IsAudioChannelOpened()) {
        // Schedule to let the state change be processed first (UI update),
        // then continue with OpenAudioChannel which may block for ~1 second
        Schedule([this, wake_word]() { ContinueWakeWordInvoke(wake_word); });
        return;
    }
    // Channel already opened, continue directly
    ContinueWakeWordInvoke(wake_word);
}

void Application::ContinueWakeWordInvoke(const std::string& wake_word) {
    // Check state again in case it was changed during scheduling
    if (GetDeviceState() != kDeviceStateConnecting) {
        return;
    }

    // Switch to performance mode before connecting to reduce latency
    auto& board = Board::GetInstance();
    board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);

    if (!protocol_->IsAudioChannelOpened()) {
        if (!protocol_->OpenAudioChannel()) {
            // Return to idle so the device is not stuck in the connecting
            // state (not every failure path reports a network error), and
            // wake word detection is re-enabled by the idle state handler.
            SetDeviceState(kDeviceStateIdle);
            return;
        }
    }

    ESP_LOGI(TAG, "Wake word detected: %s", wake_word.c_str());
#if CONFIG_SEND_WAKE_WORD_DATA
    // Encode and send the wake word data to the server
    while (auto packet = audio_service_.PopWakeWordPacket()) {
        protocol_->SendAudio(std::move(packet));
    }
    // Set the chat state to wake word detected
    protocol_->SendWakeWordDetected(wake_word);
    SetListeningMode(GetDefaultListeningMode());
#else
    // Set flag to play popup sound after state changes to listening
    // (PlaySound here would be cleared by ResetDecoder in EnableVoiceProcessing)
    play_popup_on_listening_ = true;
    SetListeningMode(GetDefaultListeningMode());
#endif
}

void Application::HandleStateChangedEvent() {
    DeviceState new_state = state_machine_.GetState();
    clock_ticks_ = 0;
    // Any state change invalidates a pending deferred listening start;
    // the Listening case below re-arms it when needed.
    pending_listening_start_ = false;

    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    auto led = board.GetLed();
    led->OnStateChanged();

    switch (new_state) {
        case kDeviceStateUnknown:
        case kDeviceStateIdle:
            display->SetStatus(Lang::Strings::STANDBY);
            // 消息区的手交（issue #8）：音乐恰恰在「说话态 → 空闲态」这条路径
            // 上起播，而下面那句 ClearChatMessages 会抹掉消息区。曲目写在状态
            // 转移**之后**（经 Schedule），所以正常顺序是 clear → 写曲目；但
            // 两条路都能回到这里（tts stop → idle、音频通道关闭 → idle），
            // 「先写后清」的次序一旦出现，用户就会看到放着歌、屏幕空着。
            // 因此：音乐握着这块区域时就重画而不是清掉，且**重画的内容取自
            // 播放器快照**（不是缓存文本）——换歌后重画出来的就是新曲目，与
            // 「换歌立刻更新」同一事实源。重画代数（遥测用手交佐证）在这里
            // 递增：它记的是「idle 分支走过一次重画」，不是通用写屏次数，
            // 所以留在分支里而不进 helper。
            if (music_screen_owns_content_ && IsMusicBusy()) {
                idle_repaint_gen_++;
            }
            RepaintOrClearMusicScreen([display]() {
                display->ClearChatMessages();  // Clear messages first
            });
            display->SetEmotion("neutral");  // Then set emotion (wechat mode checks child count)
            audio_service_.EnableVoiceProcessing(false);
            // 回 idle 时恢复唤醒词，但**音乐在出声时除外**：流式播放需要那条
            // TCP 收包路径（AFE 会饿死它，ADR-0008）。暂停态不算「在出声」，
            // 所以暂停期间唤醒词照常可用。
            audio_service_.EnableWakeWordDetection(!IsMusicPlaying());
            break;
        case kDeviceStateConnecting:
            display->SetStatus(Lang::Strings::CONNECTING);
            display->SetEmotion("neutral");
            display->SetChatMessage("system", "");
            break;
        case kDeviceStateListening:
            display->SetStatus(Lang::Strings::LISTENING);
            display->SetEmotion("neutral");

            // 新一轮静默窗口从「进聆听」这一拍开始：上一句已经被答完，
            // 用户要接话就在这几秒里接（issue #4 的自动续播计时口径）。
            pause_user_spoke_ = false;

            // Make sure the audio processor is running
            if (play_popup_on_listening_ || !audio_service_.IsAudioProcessorRunning()) {
                // For auto mode, wait for the playback queue to drain before enabling
                // voice processing. This prevents audio truncation when STOP arrives
                // late due to network jitter. Instead of blocking the main loop here,
                // defer the start until MAIN_EVENT_PLAYBACK_DRAINED arrives.
                if (listening_mode_ == kListeningModeAutoStop && !audio_service_.IsPlaybackIdle()) {
                    pending_listening_start_ = true;
                } else {
                    StartListeningAudio();
                }
            } else {
                ConfigureWakeWordForListening();
            }
            break;
        case kDeviceStateSpeaking:
            display->SetStatus(Lang::Strings::SPEAKING);

            if (listening_mode_ != kListeningModeRealtime) {
                audio_service_.EnableVoiceProcessing(false);
                // Only AFE wake word can be detected in speaking mode
                audio_service_.EnableWakeWordDetection(audio_service_.IsAfeWakeWord());
            }
            audio_service_.ResetDecoder();
            break;
        case kDeviceStateNotifying:
            display->SetStatus(Lang::Strings::SPEAKING);
            audio_service_.EnableVoiceProcessing(false);
            audio_service_.EnableWakeWordDetection(audio_service_.IsAfeWakeWord());
            break;
        case kDeviceStateWifiConfiguring:
            audio_service_.EnableVoiceProcessing(false);
            audio_service_.EnableWakeWordDetection(false);
            break;
        default:
            // Do nothing
            break;
    }
}

void Application::StartListeningAudio() {
    // Runs in the main loop, either directly from HandleStateChangedEvent or
    // deferred via MAIN_EVENT_PLAYBACK_DRAINED once the playback queue drains.
    if (GetDeviceState() != kDeviceStateListening) {
        return;
    }

    // Send the start listening command
    protocol_->SendStartListening(listening_mode_);
    audio_service_.EnableVoiceProcessing(true);

    ConfigureWakeWordForListening();

    // Play popup sound after ResetDecoder (in EnableVoiceProcessing) has been called
    if (play_popup_on_listening_) {
        play_popup_on_listening_ = false;
        audio_service_.PlaySound(Lang::Sounds::OGG_POPUP);
    }
}

void Application::ConfigureWakeWordForListening() {
#ifdef CONFIG_WAKE_WORD_DETECTION_IN_LISTENING
    // Enable wake word detection in listening mode (configured via Kconfig)
    audio_service_.EnableWakeWordDetection(audio_service_.IsAfeWakeWord());
#else
    // Disable wake word detection in listening mode
    audio_service_.EnableWakeWordDetection(false);
#endif
}

void Application::StartNotification(std::string audio_url, std::vector<NotifySubtitle> subtitles) {
    if (GetDeviceState() != kDeviceStateIdle || notify_player_.IsBusy()) {
        ESP_LOGW(TAG, "Ignoring notify message while device is busy");
        return;
    }

    auto& board = Board::GetInstance();
    board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
    audio_service_.EnableVoiceProcessing(false);
    audio_service_.EnableWakeWordDetection(audio_service_.IsAfeWakeWord());
    audio_service_.ReleaseWakeWordResources();
    while (audio_service_.PopPacketFromSendQueue()) {
        // Discard microphone audio left over from a previous conversation.
    }

    if (!SetDeviceState(kDeviceStateNotifying)) {
        board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
        return;
    }

    audio_service_.ResetDecoder();
    uint32_t playback_id = ++notification_playback_id_;
    if (playback_id == 0) {
        playback_id = ++notification_playback_id_;
    }
    audio_service_.PlaySound(Lang::Sounds::OGG_POPUP);

    bool started = notify_player_.Start(
        std::move(audio_url), std::move(subtitles), playback_id,
        [this](uint32_t id, const std::string& text) {
            Schedule([this, id, text]() {
                if (GetDeviceState() == kDeviceStateNotifying && notification_playback_id_ == id) {
                    Board::GetInstance().GetDisplay()->SetChatMessage("assistant", text.c_str());
                }
            });
        },
        [this](uint32_t id, bool success) {
            Schedule([this, id, success]() { HandleNotificationFinished(id, success); });
        });

    if (!started) {
        ESP_LOGE(TAG, "Failed to start notification playback");
        StopNotification();
    }
}

bool Application::StartMusic(const std::string& url) {
    // Notifications own the speaker exclusively; do not race them.
    if (GetDeviceState() == kDeviceStateNotifying || notify_player_.IsBusy()) {
        ESP_LOGW(TAG, "Ignoring music request while a notification is playing");
        return false;
    }

    // 换歌 = 替换语义（issue #4）：新播放请求清掉暂停态与待恢复标记，
    // 撤掉自动续播的计时——旧会话没有资格再被接回来。
    CancelPauseAutoResume();
    music_player_.CancelPause();

    // While a conversation is active the LLM reply (TTS) has not been
    // spoken yet: defer the stream until the reply finishes playing, so the
    // robot says "coming right up" first and the music starts clean after
    // it. Registration happens on the main loop, which also consumes it.
    // 注：状态门只看对话状态——暂停态的音乐不该被误判成「要延迟起播」。
    DeviceState state = GetDeviceState();
    if (state == kDeviceStateConnecting || state == kDeviceStateListening ||
        state == kDeviceStateSpeaking) {
        Schedule([this, url]() {
            pending_music_url_ = url;
            ESP_LOGI(TAG, "Music deferred until the reply finishes: %s", url.c_str());
        });
        return true;
    }
    return StartMusicNow(url);
}

bool Application::StartMusicNow(const std::string& url) {
    // Drop in-flight conversation audio (TTS) so the music starts clean;
    // audio queued afterwards from the ongoing conversation is discarded
    // by the bumped playback generation until the conversation ends.
    audio_service_.ResetDecoder();

    // Keep Wi-Fi at full performance while streaming (modem-sleep tears
    // continuous audio into pieces).
    Board::GetInstance().SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);

    if (!music_player_.Start(url, [this](const MusicPlayer::FinishedResult& result) {
            Schedule([this, result]() { HandleMusicFinished(result); });
        })) {
        ESP_LOGE(TAG, "Failed to start music: %s", url.c_str());
        Board::GetInstance().SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
        // 起流就没成功：没有暂停会话可谈（暂停态若还在，那是上一首的残影，
        // 一并撤掉——播放器内部 Start 已清，这里清会话层的计时）。
        CancelPauseAutoResume();
        // 屏幕出口（issue #8）：换歌失败时屏幕上正挂着**上一首**的曲目名，
        // 而播放器已经空闲——那就是「没在放歌，屏幕写着歌」，本票要消掉的那
        // 类假话的另一半。只在自己确实写过曲目时清（否则会把别人的告警文案
        // 擦掉：首首起播失败时消息区本来就没有曲目）。
        Schedule([this]() {
            if (music_screen_owns_content_) {
                ShowMusicScreen("skip", "", /*owns=*/false, nullptr);
            }
        });
        return false;
    }
    // While streaming, drop wake-word detection: the AFE feed saturates the
    // input core and starves the low-priority TCP receive task (~7KB/s
    // throughput, stuttering audio). The button still pauses the music.
    audio_service_.EnableWakeWordDetection(false);
    // 屏幕出口（issue #8）：起播成功后写曲目与作者。经 Schedule 排到主循环
    // 的下轮，晚于同一个任务刚刚触发的 pending 转态——「曲目文本必须在状态
    // 转移之后设置，否则会被 idle 分支的清屏吃掉」。
    //
    // 为什么不在这里直接 SetChatMessage：换歌走这条路（StartMusicNow 也由
    // 先前的对话结束路径过来），而 StartMusicNow 在**任意**任务上运行；写屏
    // 必须在主循环，与 idle 分支的重画同线程、有确定先后。
    ScheduleMusicNowPlaying();
    return true;
}

bool Application::PauseMusic(PauseKind kind) {
    // 已经在播才谈得上暂停（没有活着的会话时播放器会拒绝）。注意：重复的
    // 让位调用仍然会往下走完唤醒词/省电这些副作用——它们本身幂等，但并
    // 不是「没发生」；这里不做「重复调用跳过」的记账，只是不重复计时。
    // 用户暂停可从会话性暂停升级（用户说「暂停」= 我就是要它停着）。
    if (!music_player_.Pause(kind)) {
        return false;
    }
    // 暂停 = 用户已不在播放态：唤醒词/聆听必须恢复，否则暂停成了「半死」
    // 状态（唤醒不了、也停不掉）。续播时再关。答复进行中（speaking）是个例外
    // ——那时的唤醒词口径与 HandleStateChangedEvent 一致（只有 AFE 唤醒词
    // 能在说话期间工作），否则会打开一条与语音处理抢输入的通道。
    if (GetDeviceState() == kDeviceStateSpeaking) {
        audio_service_.EnableWakeWordDetection(audio_service_.IsAfeWakeWord());
    } else {
        audio_service_.EnableWakeWordDetection(true);
    }
    // 省电等级保持 PERFORMANCE：worker 与连接都还占着，且恢复要求即时出声。
    //
    // 种类现读播放器实际持有的那个（谓词内部问 GetPauseState()）：用户暂停
    // 是降不下来的（播放器内部会拒绝从 user 回到 conversation），若照抄入参，
    // 就会把「用户暂停」误记成会话性暂停，于是随便聊一句音乐自己回来了——
    // 两种语义的分水岭就在这一行。
    if (!IsMusicPauseConversational()) {
        // 用户暂停绝不自动续：必须说「继续」。
        auto_resume_armed_ = false;
        pause_quiet_ticks_ = 0;
    }
    return true;
}

Application::ResumeOutcome Application::ResumeMusic() {
    // 正在说话（TTS）时不能立刻出声：音乐一旦开始推帧就会与尚未念完的 TTS
    // 抢同一条播放队列（听感是串音），而聆听态下麦克风还会把音乐当人声送去
    // ASR。沿用既有的「说完话再播」：登记待恢复，等 tts stop 执行——与换歌
    // （pending_music_url_）同一条路。返回 kDeferred 而不是 kResumed：这时
    // 音乐尚未出声，工具若回「resumed」用户会听到假话。
    if (GetDeviceState() == kDeviceStateSpeaking) {
        if (!IsMusicPaused()) {
            return ResumeOutcome::kNothing;
        }
        Schedule([this]() {
            pending_music_resume_ = true;
            ESP_LOGI(TAG, "Music resume deferred until the reply finishes");
        });
        return ResumeOutcome::kDeferred;
    }
    return ResumeMusicNow() ? ResumeOutcome::kResumed : ResumeOutcome::kNothing;
}

bool Application::ResumeMusicNow() {
    if (!music_player_.Resume()) {
        return false;
    }
    // 音乐要立刻出声（用户说「继续」不该等），且不能与对话音频拼在同一条
    // 队列里：与 StartMusicNow 同一取舍——音乐接管扬声器，剩下的话就不念了。
    // 复位解码器后，后续到达的 TTS 包因状态已不是 speaking 而被丢弃（见
    // OnIncomingAudio），不会串音。
    audio_service_.ResetDecoder();
    // 续播期间唤醒词让位（与起播同一原因：AFE 抢输入核、饿死 TCP 收包）。
    audio_service_.EnableWakeWordDetection(false);
    Board::GetInstance().SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
    CancelPauseAutoResume();
    // 回到待机：聆听态下麦克风开着，会把音乐当人声送去 ASR。先告知服务端
    // 停止聆听（几毫秒的事，与服务端 120s 无语音超时那条路无关），再切状态。
    if (GetDeviceState() == kDeviceStateListening && protocol_ != nullptr) {
        protocol_->SendStopListening();
    }
    if (GetDeviceState() != kDeviceStateIdle) {
        SetDeviceState(kDeviceStateIdle);
    }
    // 屏幕出口（issue #8）：续播成功（含暂停态恢复）后回到「正在播放」。
    // 同样经 Schedule：对话途中的续播会在 tts stop 那一刻执行，而那一刻
    // idle 分支可能刚重画过消息区（“说完话再播”这条路的清屏在前、写曲目在
    // 后），次序不能靠运气。
    ScheduleMusicNowPlaying();
    return true;
}

void Application::CancelPauseAutoResume() {
    // 只重置计时器：暂停种类归播放器持有，这里没有副本可清（也正是不该
    // 有——缓存会与播放器分叉）。
    auto_resume_armed_ = false;
    pause_quiet_ticks_ = 0;
}

void Application::UpdatePauseAutoResume() {
    // 会话性暂停的自动续播（issue #4）：答完进聆听 → 静默计数 → 数秒仍无人
    // 说话则回待机并续播。只在聆听态里数——进了 speaking 说明在答，回了 idle
    // 说明这次会话已经结束，都不该倒数。用户暂停永不 arm（IsMusicPause-
    // Conversational 现读播放器、只认会话性），这是「说暂停后随便聊一句音乐
    // 不自动响」的唯一实现手段。
    if (!IsMusicPauseConversational() || GetDeviceState() != kDeviceStateListening ||
        pause_user_spoke_) {
        // 只在真的数过秒时才打，免得每拍刷日志。
        if (auto_resume_armed_ && pause_quiet_ticks_ > 0) {
            ESP_LOGI(TAG, "Music auto-resume cancelled: quiet=%ds interrupted",
                     pause_quiet_ticks_);
        }
        auto_resume_armed_ = false;
        pause_quiet_ticks_ = 0;
        return;
    }
    auto_resume_armed_ = true;
    if (++pause_quiet_ticks_ < kPauseAutoResumeQuietTicks) {
        return;
    }
    pause_quiet_ticks_ = 0;
    auto_resume_armed_ = false;
    ESP_LOGI(TAG, "Music auto-resume: quiet=%ds", kPauseAutoResumeQuietTicks);
    // ResumeMusic 才是那一步：它非阻塞（探读与必要时重起流都在 worker 里），
    // 顺手把聆听态收掉（音乐会重新占用扬声器与麦克风）——若在这里先切 idle、
    // 由 ResumeMusic 再切一次，两处就会各写一份「谁该收聆听」的规矩。
    ResumeMusic();
}

void Application::MusicStartTaskEntry(void* arg) {
    auto* url = static_cast<std::string*>(arg);
    bool ok = Application::GetInstance().StartMusicNow(*url);
    delete url;
    if (!ok) {
        // Could not start: fall back to listening so the user is not left
        // with a silent device.
        Application::GetInstance().Schedule([]() {
            auto& app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateIdle) {
                app.SetDeviceState(kDeviceStateListening);
            }
        });
    }
    vTaskDelete(nullptr);
}

void Application::LaunchPendingMusic() {
    if (pending_music_url_.empty()) {
        return;
    }
    std::string url = std::move(pending_music_url_);
    pending_music_url_.clear();
    // 换歌先撤暂停态与自动续播计时（新播放请求 = 替换，不是恢复）。
    CancelPauseAutoResume();
    music_player_.CancelPause();
    // Music plays with the device in idle: no listening while it plays,
    // wake word / button still pauses it and reopens the conversation.
    SetDeviceState(kDeviceStateIdle);
    auto* url_copy = new std::string(std::move(url));
    if (xTaskCreate(MusicStartTaskEntry, "music_start", 8192, url_copy, 5,
                    nullptr) != pdPASS) {
        delete url_copy;
        ESP_LOGE(TAG, "Failed to create music start task");
        // 起播根本没发生（issue #8）：若屏幕上还挂着上一首，得清掉——
        // 不然后面回 Listening 时那块区域会一直写着已经不在放的曲目。
        if (music_screen_owns_content_) {
            ShowMusicScreen("skip", "", /*owns=*/false, nullptr);
        }
        if (GetDeviceState() == kDeviceStateIdle) {
            SetDeviceState(kDeviceStateListening);
        }
    }
}

void Application::StopMusic() {
    // 真停止（stop_music 工具）：会话结束、位点丢弃，不再有「接着放」。
    // 必须**能从暂停态停止**——暂停态下 IsMusicBusy() 仍为 true，Cancel()
    // 一样有效，不会卡死。
    CancelPauseAutoResume();
    music_player_.CancelPause();
    if (IsMusicBusy()) {
        // Cancel is non-blocking: the main loop must not wait on an HTTP
        // read. The finished callback restores the power save level once
        // the worker has drained out.
        music_player_.Cancel();
        audio_service_.ResetDecoder();
    }
}

void Application::ShowMusicScreen(const char* action, const std::string& text,
                                  bool owns, const MusicScreenFacts* facts) {
    // 只在主循环线程调用（写屏与所有权标记必须与 idle 分支同线程，否则
    // 「重画而不是清掉」的判据会与清屏赛跑）。
    // 空文本 = 清空消息区（起播失败走这条：屏幕不该留着上一首——「放着歌屏幕
    // 写着待机」的反面就是「没放歌屏幕写着歌」）。清空用 ClearChatMessages 而
    // 不是 SetChatMessage("", "")：后者在 WeChat 气泡变体里是个空操作（那个
    // 实现遇到空串直接返回），留下的气泡会一直挂着旧曲目名。
    auto display = Board::GetInstance().GetDisplay();
    if (text.empty()) {
        display->ClearChatMessages();
    } else {
        display->SetChatMessage("system", text.c_str());
    }
    // 所有权跟着文本走：没有文本就没东西可保（end-state 写完之后，消息区
    // 不再归音乐，之后的 idle 清屏照旧生效，结束态不会自己复活）。
    music_screen_owns_content_ = owns && !text.empty();
    // 写屏序号（issue #8 的手交佐证）：主循环每写一次递增，锚点行里报出来——
    // 抓取脚本据此看出「谁先谁后」，不靠两行日志的时间戳赛跑。
    const unsigned seq = ++music_screen_seq_;
    char total_field[16] = "none";
    if (facts != nullptr && MusicScreenShowsTotal(*facts)) {
        snprintf(total_field, sizeof(total_field), "%s",
                 FormatMusicClock(facts->duration_s).c_str());
    }
    // 屏幕出口锚点（issue #8）：文本、曲目/作者/形态/总量与**写屏那一刻的设备
    // 状态**同一条行里对齐。「曲目在状态转移之后设置」这件事因此不靠人看屏幕：
    // now-playing 行的 device= 必须是 idle（音乐在空闲态下播）、且它晚于
    // `State: … -> idle` 那一行；idle 分支的重画另打 repaint。
    ESP_LOGI(TAG,
             "Music screen: action=%s seq=%u owns=%s idle_gen=%u device=%s "
             "title='%s' author='%s' form=%s duration=%ds total=%s text='%s'",
             action, seq, music_screen_owns_content_ ? "on" : "off",
             idle_repaint_gen_, DeviceStateMachine::GetStateName(GetDeviceState()),
             facts != nullptr ? facts->title.c_str() : "",
             facts != nullptr ? facts->author.c_str() : "",
             facts == nullptr ? "none" : (facts->live ? "live" : "finite"),
             facts != nullptr ? facts->duration_s : 0, total_field, text.c_str());
}

void Application::WriteMusicNowPlaying(const char* action) {
    // 快照现取（不是缓存）：暂停期间它仍给出当前曲目，恢复后报的是同一首，
    // 换歌后报的是新的那首——「换歌立刻更新」就是这一句的自然结果。
    const MusicPlaybackStatus status = music_player_.GetPlaybackStatus();
    if (status.state == MusicPlaybackStatus::State::kIdle) {
        // 没有活着的会话：没有曲目可报。拼一次只剩 Lang 前缀的文本（「正在
        // 播放：」）再写到屏幕上就是新的谎话，所以这里显式清空并交还所有权。
        // 调用点（起播/续播成功、idle 重画）都在会话活着时才到这里，这是兜底。
        ShowMusicScreen("skip", "", /*owns=*/false, nullptr);
    } else {
        MusicScreenFacts facts;
        facts.title = status.title;
        facts.author = status.author;
        facts.duration_s = status.duration_s;
        // 内容形态的单一判据是 seekable（live == !seekable），不去问 duration。
        facts.live = !status.seekable;
        ShowMusicScreen(action,
                        BuildMusicNowPlaying(facts, Lang::Strings::MUSIC_NOW_PLAYING),
                        /*owns=*/true, &facts);
    }
}

void Application::RepaintOrClearMusicScreen(std::function<void()>&& clear_fn) {
    // 归属权判断与交还只有这一处实现（ADR-0015 决策 4）。此前这段形状在四条
    // 路径上各抄一遍，结果其中一处漏了交还（StopNotification）——散抄的典型
    // 症状，也正是这个 helper 要消掉的东西。
    //
    // 为什么要判 IsMusicBusy() 而不只看标记：标记说的是「上次写屏留了曲目」，
    // 忙碌说的是「那个会话还活着」。会话没了却还重画，就会拿空闲快照去写
    // 「正在播放：」——那是新的谎话。两个合起来才是「该保住曲目」的完整判据。
    if (music_screen_owns_content_ && IsMusicBusy()) {
        WriteMusicNowPlaying("repaint");
    } else {
        clear_fn();
        music_screen_owns_content_ = false;
    }
}

void Application::ScheduleMusicNowPlaying() {
    // 起播/续播发生在应用层与 music_start 任务上，而写屏必须落在主循环、且
    // 在 pending 的 STATE_CHANGED **之后**：音乐恰恰在「说话态 → 空闲态」这条
    // 路径上起播，idle 分支就在那条路径上重画/清空消息区。经 Schedule 排到
    // 下一轮，那一轮先处理 STATE_CHANGED 再处理 SCHEDULE（与 ADR-0014 决策 4
    // 同一机制、同一理由）。
    Schedule([this]() { WriteMusicNowPlaying("now-playing"); });
}

void Application::HandleMusicFinished(const MusicPlayer::FinishedResult& result) {
    // 收场分类是本次会话自己产出的（回调与结果同线程一起交上来），不靠共享
    // 状态反推——「用户按停」与「链路中断」从前都是同一种 Bool 组合，那次混作
    // 一谈就是 issue #7 要修的东西。
    const MusicEnding ending = result.ending;

    // 换歌：旧会话的收场归**新**会话，这里什么都不做——不出声、不改屏幕、
    // 不碰省电与唤醒词（新会话正拿着音箱与 TCP 路径）。陈旧的收尾回调
    // （旧 worker 稍后才跑完）也会撞到这里：忙碌为真就不插手，与 IsMusicBusy()
    // 同一判据、同一处理的跳过分支。
    if (ending == MusicEnding::kReplaced || IsMusicBusy()) {
        char pos_field[32];
        WritePositionField(result.position_s, result.live, pos_field, sizeof(pos_field));
        ESP_LOGI(TAG, "Music feedback: reason=%s pos=%s skipped=new_session",
                 MusicEndingName(ending), pos_field);
        return;
    }

    // 会话已经结束：撤掉自动续播与暂停记账。
    CancelPauseAutoResume();
    Board::GetInstance().SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
    // Music over: bring wake-word detection back (it was paused while
    // streaming to keep the TCP receive path responsive).
    audio_service_.EnableWakeWordDetection(true);

    // ── 反馈（issue #7）────────────────────────────────────────
    // 音效分支：自然播完 / 链路中断（含续播失败）各一个不同的音；用户主动
    // 停止、换歌、起流失败都不出声。屏幕文案同理：用户主动停止要写在屏幕上
    // 报出来，别让屏幕留着歌名（那正是读不出来的错）。
    const char* sound = "none";
    const char* screen_text = "none";
    const char* message = nullptr;
    // 提示音**不在这里播**：进 Listening 的路上可能 ResetDecoder（Realtime
    // 聆听模式直接 StartListeningAudio → EnableVoiceProcessing(true)），会把
    // 刚进队列的音一起清掉。所以实际出声放在下面 Schedule 的尾巴——那时代
    // 解码器已经重设过，音一定留得住。（AutoStop 模式下转态会等
    // IsPlaybackIdle()，提示音先播完才开聆听——正是想要的顺序。）
    const std::string_view* sound_asset = nullptr;
    switch (MusicEndingCue(ending)) {
        case MusicCue::kSuccess:
            // 自然播完：提示音 +「播放结束」。
            sound_asset = &Lang::Sounds::OGG_SUCCESS;
            sound = "success";
            message = Lang::Strings::MUSIC_ENDED;
            screen_text = "ended";
            break;
        case MusicCue::kWarning:
            // 链路中断（含续播接不上）：提示音 +「播放中断」——用户不该把
            // 「断了」听成「歌放完了」。
            sound_asset = &Lang::Sounds::OGG_EXCLAMATION;
            sound = "alert";
            message = Lang::Strings::MUSIC_INTERRUPTED;
            screen_text = "interrupted";
            break;
        case MusicCue::kNone:
            // 用户主动停止：不出声（用户自己按的，报故障音是打扰），但屏幕上
            // 要报「已停止」——那时没有新会话接手屏幕。
            if (ending == MusicEnding::kStopped) {
                message = Lang::Strings::MUSIC_STOPPED;
                screen_text = "stopped";
            }
            // 换歌/起流失败：屏幕归新会话 / 起播那一刻已报过错，什么都不做。
            break;
    }

    // 回到可交互（回归守卫）：唤醒词已在上面恢复；能说话时再回 Listening，
    // 否则留在待机（那里唤醒词也是开着的）——“在聆听但没人听”的半死状态
    // 不发。三个字段都如实报（唤醒词可能因没配/引擎起不来而真没恢复，
    // 那时报 on 就是在撒谎，断言也白设）。
    const DeviceState state = GetDeviceState();
    const bool can_listen = state == kDeviceStateIdle && protocol_ != nullptr &&
                            protocol_->IsAudioChannelOpened();
    const bool wake_word = audio_service_.IsWakeWordRunning();
    const char* interactive = can_listen                       ? "scheduled"
                              : state == kDeviceStateListening    ? "already"
                              : wake_word                        ? "wake_word_only"
                                                                 : "none";

    // 发声与写屏都放在这里，**不能**在上面直接做：
    //   1) 进 Listening 时 idle 分支会 ClearChatMessages（主循环同一轮先处理
    //      STATE_CHANGED 再处理 SCHEDULE），直接写会被抹掉；
    //   2) 进 Listening 时可能 ResetDecoder 清掉 decode 队列（见上面 sound_asset
    //      处注释）——先转态再出声，音才留得住。
    Schedule([this, message, can_listen, sound_asset]() {
        if (can_listen) {
            SetDeviceState(kDeviceStateListening);
        }
        if (sound_asset != nullptr) {
            audio_service_.PlaySound(*sound_asset);
        }
        // 屏幕出口（issue #8）与 #7 的结束态共用同一个 helper：结束态就是
        // 「写一次但不再保留所有权」——写完之后陈旧的 idle 重画照旧生效，屏幕
        // 不会抱着一个已经过时的曲目名（「结束时不留陈旧曲目」的那一半）。
        if (message != nullptr) {
            ShowMusicScreen("end-state", message, /*owns=*/false, nullptr);
        } else if (music_screen_owns_content_) {
            // 无文案的收场（换歌/起流失败）：只交还所有权不给新文本。换歌时
            // 新会话的曲目就在主循环的下一拍，起流失败则由起播那一刻的报错
            // 接手屏幕；这里只需保证「没在放歌就不写着歌」。
            ShowMusicScreen("skip", "", /*owns=*/false, nullptr);
        }
    });

    // 反馈锚点（issue #7 的串口断言）：原因 → 提示音/屏幕 + 是否回到可交互。
    // 位点一位小数（与 pause/resume/ended 锚点同口径）；直播流报 live。
    char pos_field[32];
    WritePositionField(result.position_s, result.live, pos_field, sizeof(pos_field));
    ESP_LOGI(TAG,
             "Music feedback: reason=%s pos=%s sound=%s screen=%s wake_word=%s "
             "interactive=%s",
             MusicEndingName(ending), pos_field, sound, screen_text,
             wake_word ? "on" : "off", interactive);
}

void Application::StopNotification() {
    notify_player_.Stop();
    audio_service_.ResetDecoder();
    auto& board = Board::GetInstance();
    // 通知放完了，但音乐可能还在（在播或暂停）：它接着通知占着同一条扬声器
    // 与同一块消息区，通知那条文案不该留在屏幕上、曲目也不该因此消失
    // （issue #8）。音乐在忙就重画曲目，否则照旧清掉。
    RepaintOrClearMusicScreen([&board]() {
        board.GetDisplay()->SetChatMessage("assistant", "");
    });
    board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
    if (GetDeviceState() == kDeviceStateNotifying) {
        SetDeviceState(kDeviceStateIdle);
    }
}

void Application::HandleNotificationFinished(uint32_t playback_id, bool success) {
    if (GetDeviceState() != kDeviceStateNotifying || notification_playback_id_ != playback_id) {
        return;
    }
    ESP_LOGI(TAG, "Notification playback %lu %s", static_cast<unsigned long>(playback_id),
             success ? "completed" : "failed");
    StopNotification();
}

void Application::Schedule(std::function<void()>&& callback) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        main_tasks_.push_back(std::move(callback));
    }
    xEventGroupSetBits(event_group_, MAIN_EVENT_SCHEDULE);
}

void Application::AbortSpeaking(AbortReason reason) {
    ESP_LOGI(TAG, "Abort speaking");
    aborted_ = true;
    ResetTtsBuffer();
    // 打断这句话就一并撤掉「延迟到这句话之后」的登记：留下的 URL/续播标记
    // 会在下一轮意外起播。注意**只**清登记，不动暂停位点——位点是 issue #5
    // 要保住的东西。
    Schedule([this]() {
        pending_music_url_.clear();
        pending_music_resume_ = false;
    });
    if (protocol_) {
        protocol_->SendAbortSpeaking(reason);
    }
}

void Application::ResetTtsBuffer() {
    std::lock_guard<std::mutex> lock(tts_buffer_mutex_);
    tts_buffering_ = false;
    if (!tts_buffer_.empty()) {
        ESP_LOGI(TAG, "Discarding %u buffered TTS packets", (unsigned)tts_buffer_.size());
    }
    tts_buffer_.clear();
    tts_buffer_bytes_ = 0;
}

void Application::FlushTtsBuffer() {
    std::deque<std::unique_ptr<AudioStreamPacket>> packets;
    {
        std::lock_guard<std::mutex> lock(tts_buffer_mutex_);
        tts_buffering_ = false;
        packets.swap(tts_buffer_);
        tts_buffer_bytes_ = 0;
    }
    if (packets.empty()) {
        return;
    }
    size_t bytes = 0;
    uint32_t duration_ms = 0;
    for (const auto& packet : packets) {
        bytes += packet->payload.size();
        duration_ms += packet->frame_duration > 0 ? packet->frame_duration : OPUS_FRAME_DURATION_MS;
    }
    const size_t total = packets.size();
    size_t pushed = audio_service_.PushPacketsToDecodeQueue(packets);
    ESP_LOGI(TAG, "Playing buffered TTS: %u/%u packets (%u bytes, ~%u ms)",
             (unsigned)pushed, (unsigned)total, (unsigned)bytes, (unsigned)duration_ms);
}

void Application::SetListeningMode(ListeningMode mode) {
    listening_mode_ = mode;
    SetDeviceState(kDeviceStateListening);
}

ListeningMode Application::GetDefaultListeningMode() const {
    return aec_mode_ == kAecOff ? kListeningModeAutoStop : kListeningModeRealtime;
}

void Application::Reboot() {
    ESP_LOGI(TAG, "Rebooting...");
    if (GetDeviceState() == kDeviceStateNotifying) {
        StopNotification();
    }
    // Disconnect the audio channel
    if (protocol_ && protocol_->IsAudioChannelOpened()) {
        protocol_->CloseAudioChannel();
    }
    protocol_.reset();
    audio_service_.Stop();

    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_restart();
}

bool Application::UpgradeFirmware(const std::string& url, const std::string& version) {
    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();

    std::string upgrade_url = url;
    std::string version_info = version.empty() ? "(Manual upgrade)" : version;

    if (GetDeviceState() == kDeviceStateNotifying) {
        StopNotification();
    }

    // Close audio channel if it's open
    if (protocol_ && protocol_->IsAudioChannelOpened()) {
        ESP_LOGI(TAG, "Closing audio channel before firmware upgrade");
        protocol_->CloseAudioChannel();
    }
    ESP_LOGI(TAG, "Starting firmware upgrade from URL: %s", upgrade_url.c_str());

    Alert(Lang::Strings::OTA_UPGRADE, Lang::Strings::UPGRADING, "download",
          Lang::Sounds::OGG_UPGRADE);
    vTaskDelay(pdMS_TO_TICKS(3000));

    SetDeviceState(kDeviceStateUpgrading);

    std::string message = std::string(Lang::Strings::NEW_VERSION) + version_info;
    display->SetChatMessage("system", message.c_str());

    board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
    audio_service_.Stop();
    vTaskDelay(pdMS_TO_TICKS(1000));

    bool upgrade_success = Ota::Upgrade(upgrade_url, [this, display](int progress, size_t speed) {
        char buffer[32];
        snprintf(buffer, sizeof(buffer), "%d%% %uKB/s", progress, speed / 1024);
        Schedule([display, message = std::string(buffer)]() {
            display->SetChatMessage("system", message.c_str());
        });
    });

    if (!upgrade_success) {
        // Upgrade failed, restart audio service and continue running
        ESP_LOGE(TAG,
                 "Firmware upgrade failed, restarting audio service and continuing operation...");
        audio_service_.Start();                              // Restart audio service
        board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);  // Restore power save level
        Alert(Lang::Strings::ERROR, Lang::Strings::UPGRADE_FAILED, "cancel",
              Lang::Sounds::OGG_EXCLAMATION);
        vTaskDelay(pdMS_TO_TICKS(3000));
        return false;
    } else {
        // Upgrade success, reboot immediately
        ESP_LOGI(TAG, "Firmware upgrade successful, rebooting...");
        display->SetChatMessage("system", "Upgrade successful, rebooting...");
        vTaskDelay(pdMS_TO_TICKS(1000));  // Brief pause to show message
        Reboot();
        return true;
    }
}

void Application::WakeWordInvoke(const std::string& wake_word) {
    if (!protocol_) {
        return;
    }

    auto state = GetDeviceState();

    if (state == kDeviceStateIdle) {
        // May be called from outside the main task (e.g. board button
        // callbacks), so schedule the invocation instead of running it here
        Schedule([this, wake_word]() {
            if (GetDeviceState() == kDeviceStateIdle) {
                BeginWakeWordInvoke(wake_word);
            }
        });
    } else if (state == kDeviceStateNotifying) {
        Schedule([this, wake_word]() {
            if (GetDeviceState() == kDeviceStateNotifying) {
                StopNotification();
                BeginWakeWordInvoke(wake_word);
            }
        });
    } else if (state == kDeviceStateSpeaking) {
        Schedule([this]() { AbortSpeaking(kAbortReasonNone); });
    } else if (state == kDeviceStateListening) {
        Schedule([this]() {
            if (protocol_) {
                protocol_->CloseAudioChannel();
            }
        });
    }
}

bool Application::CanEnterSleepMode() {
    if (GetDeviceState() != kDeviceStateIdle) {
        return false;
    }

    if (protocol_ && protocol_->IsAudioChannelOpened()) {
        return false;
    }

    if (!audio_service_.IsIdle()) {
        return false;
    }

    // Now it is safe to enter sleep mode
    return true;
}

void Application::RegisterMcpBroadcastCallback(std::function<void(const std::string&)> callback) {
    mcp_broadcast_callback_ = std::move(callback);
}

void Application::SendMcpMessage(const std::string& payload) {
    // Always schedule to run in main task for thread safety
    Schedule([this, payload]() {
        if (protocol_) {
            protocol_->SendMcpMessage(payload);
        }
        if (mcp_broadcast_callback_) {
            mcp_broadcast_callback_(payload);
        }
    });
}

void Application::SetAecMode(AecMode mode) {
    aec_mode_ = mode;
    Schedule([this]() {
        auto& board = Board::GetInstance();
        auto display = board.GetDisplay();
        switch (aec_mode_) {
            case kAecOff:
                audio_service_.EnableDeviceAec(false);
                display->ShowNotification(Lang::Strings::RTC_MODE_OFF);
                break;
            case kAecOnServerSide:
                audio_service_.EnableDeviceAec(false);
                display->ShowNotification(Lang::Strings::RTC_MODE_ON);
                break;
            case kAecOnDeviceSide:
                audio_service_.EnableDeviceAec(true);
                display->ShowNotification(Lang::Strings::RTC_MODE_ON);
                break;
        }

        // If the AEC mode is changed, close the audio channel
        if (protocol_ && protocol_->IsAudioChannelOpened()) {
            protocol_->CloseAudioChannel();
        }
    });
}

void Application::PlaySound(const std::string_view& sound) { audio_service_.PlaySound(sound); }

void Application::ResetProtocol() {
    Schedule([this]() {
        if (GetDeviceState() == kDeviceStateNotifying) {
            StopNotification();
        }
        // Close audio channel if opened
        if (protocol_ && protocol_->IsAudioChannelOpened()) {
            protocol_->CloseAudioChannel();
        }
        // Reset protocol
        protocol_.reset();
    });
}
