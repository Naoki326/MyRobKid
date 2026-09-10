#ifndef _WEBSOCKET_PROTOCOL_H_
#define _WEBSOCKET_PROTOCOL_H_


#include "protocol.h"

#include <web_socket.h>
#include <freertos/FreeRTOS.h>
#include <freertos/event_groups.h>

#include <atomic>
#include <memory>
#include <mutex>
#include <string>

#define WEBSOCKET_PROTOCOL_SERVER_HELLO_EVENT (1 << 0)

class WebsocketProtocol : public Protocol {
public:
    WebsocketProtocol();
    ~WebsocketProtocol();

    bool Start() override;
    bool SendAudio(std::unique_ptr<AudioStreamPacket> packet) override;
    bool OpenAudioChannel() override;
    void CloseAudioChannel(bool send_goodbye = true) override;
    bool IsAudioChannelOpened() const override;

private:
    // 主/备地址的运行时状态：只活在 RAM，绝不落 NVS——内网主地址永远是首选，
    // 备用只是它暂时不可达时的通道，后台探测到主地址恢复即切回。
    struct ProbeState {
        std::atomic<bool> prefer_backup{false};  // 主地址上次失败，本轮先走备用
        std::atomic<bool> probe_running{false};  // 后台探测任务是否在跑
        std::mutex mutex;
        std::string primary_url;  // 探测目标
    };

    EventGroupHandle_t event_group_handle_;
    std::unique_ptr<WebSocket> websocket_;
    int version_ = 1;
    std::shared_ptr<ProbeState> probe_state_;

    bool ConnectTo(const std::string& url);
    void NoteBackupInUse(const std::string& primary_url);
    static void StartPrimaryProbe(const std::shared_ptr<ProbeState>& state);
    static void PrimaryProbeTask(void* arg);

    void ParseServerHello(const cJSON* root);
    bool SendText(const std::string& text) override;
    std::string GetHelloMessage();
};

#endif
