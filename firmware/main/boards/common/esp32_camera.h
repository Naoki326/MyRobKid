#pragma once
#include "sdkconfig.h"

#include <lvgl.h>
#include <thread>
#include <memory>
#include <mutex>
#include <vector>

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include "camera.h"
#include "esp_camera.h"
#include "jpg/image_to_jpeg.h"

struct JpegChunk
{
    uint8_t *data;
    size_t len;
};

class Esp32Camera : public Camera
{
private:
    bool streaming_on_ = false;
    bool swap_bytes_enabled_ = true;  // Swap pixel byte order for RGB565, enabled by default
    std::string explain_url_;
    std::string explain_token_;
    std::thread encoder_thread_;
    camera_fb_t *current_fb_ = nullptr;
    uint8_t *encode_buf_ = nullptr;  // Buffer for JPEG encoding (with optional byte swap)
    size_t encode_buf_size_ = 0;
    // 保护摄像头帧缓冲并发访问（对话拍照 / HTTP 快照）
    std::mutex camera_mutex_;

public:
    Esp32Camera(const camera_config_t &config);
    ~Esp32Camera();

    virtual void SetExplainUrl(const std::string &url, const std::string &token) override;
    virtual bool Capture() override;
    virtual bool SetHMirror(bool enabled) override;
    virtual bool SetVFlip(bool enabled) override;
    virtual bool SetSwapBytes(bool enabled) override;
    virtual std::string Explain(const std::string &question) override;

    /**
     * 拍一帧并编码为 JPEG（线程安全，供 HTTP 快照服务使用）
     * @param out 输出 JPEG 数据
     * @return 成功与否
     */
    bool GetSnapshotJpeg(std::vector<uint8_t> &out);
};
