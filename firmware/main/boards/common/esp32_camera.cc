#include "sdkconfig.h"

#include <esp_heap_caps.h>
#include <cstdio>
#include <cstring>
#include <esp_log.h>
#include <img_converters.h>

#include "esp32_camera.h"
#include "board.h"
#include "display.h"
#include "lvgl_display.h"
#include "mcp_server.h"
#include "system_info.h"
#include "jpg/image_to_jpeg.h"
#include "esp_timer.h"

#define TAG "Esp32Camera"

// 顺时针旋转 90°（RGB565，w×h → h×w），返回新缓冲（PSRAM，调用者释放）
static uint16_t* RotateRgb56590Cw(const uint16_t* src, int w, int h) {
    uint16_t* dst = (uint16_t*)heap_caps_malloc((size_t)w * h * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (dst == nullptr) {
        ESP_LOGE(TAG, "Rotate: no memory");
        return nullptr;
    }
    // dst[x*h + (h-1-y)] = src[y*w + x]
    for (int y = 0; y < h; y++) {
        const uint16_t* row = src + (size_t)y * w;
        const int dstCol = h - 1 - y;
        for (int x = 0; x < w; x++) {
            dst[(size_t)x * h + dstCol] = row[x];
        }
    }
    return dst;
}

#if CONFIG_XIAOZHI_CAMERA_MIRROR_CONFIGURED
#if CONFIG_XIAOZHI_CAMERA_HMIRROR
static constexpr bool kConfiguredHMirror = true;
#else
static constexpr bool kConfiguredHMirror = false;
#endif
#if CONFIG_XIAOZHI_CAMERA_VFLIP
static constexpr bool kConfiguredVFlip = true;
#else
static constexpr bool kConfiguredVFlip = false;
#endif
#endif

Esp32Camera::Esp32Camera(const camera_config_t &config) {
    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_camera_init failed with error 0x%x", err);
        return;
    }

    sensor_t *s = esp_camera_sensor_get();
    if (s) {
        if (s->id.PID == GC0308_PID) {
            s->set_hmirror(s, 0); // Control camera mirror: 1 for mirror, 0 for normal
        }
#if CONFIG_XIAOZHI_CAMERA_MIRROR_CONFIGURED
        s->set_hmirror(s, kConfiguredHMirror ? 1 : 0);
        s->set_vflip(s, kConfiguredVFlip ? 1 : 0);
#endif
        ESP_LOGI(TAG, "Camera initialized: format=%d", config.pixel_format);
    }

    streaming_on_ = true;
}

Esp32Camera::~Esp32Camera() {
    if (streaming_on_) {
        if (current_fb_) {
            esp_camera_fb_return(current_fb_);
            current_fb_ = nullptr;
        }
        if (encode_buf_) {
            heap_caps_free(encode_buf_);
            encode_buf_ = nullptr;
            encode_buf_size_ = 0;
        }
        esp_camera_deinit();
        streaming_on_ = false;
    }
}

void Esp32Camera::SetExplainUrl(const std::string &url, const std::string &token) {
    explain_url_ = url;
    explain_token_ = token;
}

bool Esp32Camera::Capture() {
    std::lock_guard<std::mutex> lock(camera_mutex_);
    if (encoder_thread_.joinable()) {
        encoder_thread_.join();
    }

    if (!streaming_on_) {
        return false;
    }

    // Get the latest frame, discard old frames for real-time performance
    for (int i = 0; i < 2; i++) {
        if (current_fb_) {
            esp_camera_fb_return(current_fb_);
        }
        current_fb_ = esp_camera_fb_get();
        if (!current_fb_) {
            ESP_LOGE(TAG, "Camera capture failed");
            return false;
        }
    }

    // Prepare encode buffer for RGB565 format (with optional byte swapping)
    if (current_fb_->format == PIXFORMAT_RGB565) {
        size_t pixel_count = current_fb_->width * current_fb_->height;
        size_t data_size = pixel_count * 2;

        // Allocate or reallocate encode buffer if needed
        if (encode_buf_size_ < data_size) {
            if (encode_buf_) {
                heap_caps_free(encode_buf_);
            }
            encode_buf_ = (uint8_t *)heap_caps_malloc(data_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
            if (encode_buf_ == nullptr) {
                ESP_LOGE(TAG, "Failed to allocate memory for encode buffer");
                encode_buf_size_ = 0;
                return false;
            }
            encode_buf_size_ = data_size;
        }

        // Copy data to encode buffer with optional byte swapping
        uint16_t *src = (uint16_t *)current_fb_->buf;
        uint16_t *dst = (uint16_t *)encode_buf_;
        if (swap_bytes_enabled_) {
            for (size_t i = 0; i < pixel_count; i++) {
                dst[i] = __builtin_bswap16(src[i]);
            }
        } else {
            memcpy(encode_buf_, current_fb_->buf, data_size);
        }

        // Allocate separate buffer for preview display
        uint8_t *preview_data = (uint8_t *)heap_caps_malloc(data_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (preview_data != nullptr) {
            memcpy(preview_data, encode_buf_, data_size);
            auto display = dynamic_cast<LvglDisplay *>(Board::GetInstance().GetDisplay());
            if (display != nullptr) {
                display->SetPreviewImage(std::make_unique<LvglAllocatedImage>(preview_data, data_size, current_fb_->width, current_fb_->height, current_fb_->width * 2, LV_COLOR_FORMAT_RGB565));
            } else {
                heap_caps_free(preview_data);
            }
        }
    } else if (current_fb_->format == PIXFORMAT_JPEG) {
        // JPEG format preview usually requires decoding, skip preview display for now, just log
        ESP_LOGW(TAG, "JPEG capture success, len=%zu, but not supported for preview", current_fb_->len);
    }

    ESP_LOGI(TAG, "Captured frame: %dx%d, len=%zu, format=%d",
             current_fb_->width, current_fb_->height, current_fb_->len, current_fb_->format);

    return true;
}

bool Esp32Camera::SetHMirror(bool enabled) {
    sensor_t *s = esp_camera_sensor_get();
    if (!s) {
        return false;
    }
    s->set_hmirror(s, enabled ? 1 : 0);
    return true;
}

bool Esp32Camera::SetVFlip(bool enabled) {
    sensor_t *s = esp_camera_sensor_get();
    if (!s) {
        return false;
    }
    s->set_vflip(s, enabled ? 1 : 0);
    return true;
}

bool Esp32Camera::SetSwapBytes(bool enabled) {
    swap_bytes_enabled_ = enabled;
    return true;
}

std::string Esp32Camera::Explain(const std::string &question) {
    std::lock_guard<std::mutex> lock(camera_mutex_);
    if (explain_url_.empty()) {
        throw std::runtime_error("Image explain URL or token is not set");
    }

    if (current_fb_ == nullptr) {
        throw std::runtime_error("No camera frame captured");
    }

    // Create local JPEG queue
    QueueHandle_t jpeg_queue = xQueueCreate(40, sizeof(JpegChunk));
    if (jpeg_queue == nullptr) {
        ESP_LOGE(TAG, "Failed to create JPEG queue");
        throw std::runtime_error("Failed to create JPEG queue");
    }

    // Start encoding thread
    encoder_thread_ = std::thread([this, jpeg_queue]() {
        int64_t start_time = esp_timer_get_time();
        uint16_t w = current_fb_->width;
        uint16_t h = current_fb_->height;
        v4l2_pix_fmt_t enc_fmt;
        switch (current_fb_->format) {
            case PIXFORMAT_RGB565:
                enc_fmt = V4L2_PIX_FMT_RGB565;
                break;
            case PIXFORMAT_YUV422:
                enc_fmt = V4L2_PIX_FMT_YUYV;  // YUV422 is actually YUYV format
                break;
            case PIXFORMAT_YUV420:
                enc_fmt = V4L2_PIX_FMT_YUV420;
                break;
            case PIXFORMAT_GRAYSCALE:
                enc_fmt = V4L2_PIX_FMT_GREY;
                break;
            case PIXFORMAT_JPEG:
                enc_fmt = V4L2_PIX_FMT_JPEG;
                break;
            case PIXFORMAT_RGB888:
                enc_fmt = V4L2_PIX_FMT_RGB24;
                break;
            default:
                ESP_LOGE(TAG, "Unsupported pixel format: %d", current_fb_->format);
                return;
        }

        // Use encode buffer for RGB565, otherwise use original frame buffer
        uint8_t *jpeg_src_buf = current_fb_->buf;
        size_t jpeg_src_len = current_fb_->len;
        uint16_t enc_w = w, enc_h = h;
        uint16_t* rotated = nullptr;
        if (current_fb_->format == PIXFORMAT_RGB565 && encode_buf_ != nullptr) {
            jpeg_src_buf = encode_buf_;
            jpeg_src_len = encode_buf_size_;
            // 顺时针旋转 90°：设备横屏使用时照片为正方向
            rotated = RotateRgb56590Cw((const uint16_t*)encode_buf_, w, h);
            if (rotated != nullptr) {
                jpeg_src_buf = (uint8_t*)rotated;
                enc_w = h;
                enc_h = w;
            }
        }

        bool ok = image_to_jpeg_cb(jpeg_src_buf, jpeg_src_len, enc_w, enc_h, enc_fmt, 80,
            [](void* arg, size_t index, const void* data, size_t len) -> size_t {
                auto jpeg_queue = static_cast<QueueHandle_t>(arg);
                JpegChunk chunk = {.data = nullptr, .len = len};
                if (index == 0 && data != nullptr && len > 0) {
                    chunk.data = (uint8_t*)heap_caps_aligned_alloc(16, len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                    if (chunk.data == nullptr) {
                        ESP_LOGE(TAG, "Failed to allocate %zu bytes for JPEG chunk", len);
                        chunk.len = 0;
                    } else {
                        memcpy(chunk.data, data, len);
                    }
                } else {
                    chunk.len = 0;  // Sentinel or error
                }
                xQueueSend(jpeg_queue, &chunk, portMAX_DELAY);
                return len;
            }, jpeg_queue);

        if (!ok) {
            JpegChunk chunk = {.data = nullptr, .len = 0};
            xQueueSend(jpeg_queue, &chunk, portMAX_DELAY);
        }
        if (rotated != nullptr) {
            heap_caps_free(rotated);
            rotated = nullptr;
        }
        int64_t end_time = esp_timer_get_time();
        ESP_LOGI(TAG, "JPEG encoding time: %ld ms", int((end_time - start_time) / 1000));
    });

    auto network = Board::GetInstance().GetNetwork();
    auto http = network->CreateHttp(3);
    std::string boundary = "----ESP32_CAMERA_BOUNDARY";

    http->SetHeader("Device-Id", SystemInfo::GetMacAddress().c_str());
    http->SetHeader("Client-Id", Board::GetInstance().GetUuid().c_str());
    if (!explain_token_.empty()) {
        http->SetHeader("Authorization", "Bearer " + explain_token_);
    }
    http->SetHeader("Content-Type", "multipart/form-data; boundary=" + boundary);
    http->SetHeader("Transfer-Encoding", "chunked");
    if (!http->Open("POST", explain_url_)) {
        ESP_LOGE(TAG, "Failed to connect to explain URL");
        encoder_thread_.join();
        JpegChunk chunk;
        while (xQueueReceive(jpeg_queue, &chunk, portMAX_DELAY) == pdPASS) {
            if (chunk.data != nullptr) {
                heap_caps_free(chunk.data);
            } else {
                break;
            }
        }
        vQueueDelete(jpeg_queue);
        throw std::runtime_error("Failed to connect to explain URL");
    }

    {
        std::string question_field;
        question_field += "--" + boundary + "\r\n";
        question_field += "Content-Disposition: form-data; name=\"question\"\r\n";
        question_field += "\r\n";
        question_field += question + "\r\n";
        http->Write(question_field.c_str(), question_field.size());
    }
    {
        std::string file_header;
        file_header += "--" + boundary + "\r\n";
        file_header += "Content-Disposition: form-data; name=\"file\"; filename=\"camera.jpg\"\r\n";
        file_header += "Content-Type: image/jpeg\r\n";
        file_header += "\r\n";
        http->Write(file_header.c_str(), file_header.size());
    }

    size_t total_sent = 0;
    bool saw_terminator = false;
    while (true) {
        JpegChunk chunk;
        if (xQueueReceive(jpeg_queue, &chunk, portMAX_DELAY) != pdPASS) {
            ESP_LOGE(TAG, "Failed to receive JPEG chunk");
            break;
        }
        if (chunk.data == nullptr) {
            saw_terminator = true;
            break;
        }
        http->Write((const char *)chunk.data, chunk.len);
        total_sent += chunk.len;
        heap_caps_free(chunk.data);
    }
    encoder_thread_.join();
    vQueueDelete(jpeg_queue);

    if (!saw_terminator || total_sent == 0) {
        ESP_LOGE(TAG, "JPEG encoder failed or produced empty output");
        throw std::runtime_error("Failed to encode image to JPEG");
    }

    {
        std::string multipart_footer;
        multipart_footer += "\r\n--" + boundary + "--\r\n";
        http->Write(multipart_footer.c_str(), multipart_footer.size());
    }
    http->Write("", 0);

    if (http->GetStatusCode() != 200) {
        ESP_LOGE(TAG, "Failed to upload photo, status code: %d", http->GetStatusCode());
        throw std::runtime_error("Failed to upload photo");
    }

    std::string result = http->ReadAll();
    http->Close();

    size_t remain_stack_size = uxTaskGetStackHighWaterMark(nullptr);
    ESP_LOGI(TAG, "Explain image size=%dx%d, compressed size=%d, remain stack size=%d, question=%s\n%s",
             current_fb_->width, current_fb_->height, (int)total_sent, (int)remain_stack_size, question.c_str(), result.c_str());
    return result;
}

bool Esp32Camera::GetSnapshotJpeg(std::vector<uint8_t> &out) {
    std::lock_guard<std::mutex> lock(camera_mutex_);
    if (encoder_thread_.joinable()) {
        encoder_thread_.join();
    }
    if (!streaming_on_) {
        return false;
    }

    // 取最新帧（丢弃旧帧）
    camera_fb_t *fb = nullptr;
    for (int i = 0; i < 2; i++) {
        if (fb) {
            esp_camera_fb_return(fb);
        }
        fb = esp_camera_fb_get();
        if (!fb) {
            ESP_LOGE(TAG, "Snapshot capture failed");
            return false;
        }
    }

    bool ok = false;
    out.clear();
    if (fb->format == PIXFORMAT_JPEG) {
        out.assign(fb->buf, fb->buf + fb->len);
        ok = !out.empty();
    } else {
        // 与 Explain 一致：RGB565 需字节交换后编码
        v4l2_pix_fmt_t enc_fmt;
        switch (fb->format) {
            case PIXFORMAT_RGB565: enc_fmt = V4L2_PIX_FMT_RGB565; break;
            case PIXFORMAT_YUV422: enc_fmt = V4L2_PIX_FMT_YUYV; break;
            case PIXFORMAT_YUV420: enc_fmt = V4L2_PIX_FMT_YUV420; break;
            case PIXFORMAT_GRAYSCALE: enc_fmt = V4L2_PIX_FMT_GREY; break;
            case PIXFORMAT_RGB888: enc_fmt = V4L2_PIX_FMT_RGB24; break;
            default:
                ESP_LOGE(TAG, "Snapshot unsupported format: %d", fb->format);
                esp_camera_fb_return(fb);
                return false;
        }

        size_t pixel_count = fb->width * fb->height;
        size_t data_size = pixel_count * 2;
        uint8_t *src = fb->buf;
        uint8_t *swap_buf = nullptr;
        uint16_t *rotated = nullptr;
        uint16_t enc_w = fb->width, enc_h = fb->height;
        if (fb->format == PIXFORMAT_RGB565) {
            swap_buf = (uint8_t *)heap_caps_malloc(data_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
            if (swap_buf == nullptr) {
                ESP_LOGE(TAG, "Snapshot: no memory for swap buffer");
                esp_camera_fb_return(fb);
                return false;
            }
            uint16_t *s = (uint16_t *)fb->buf;
            uint16_t *d = (uint16_t *)swap_buf;
            if (swap_bytes_enabled_) {
                for (size_t i = 0; i < pixel_count; i++) d[i] = __builtin_bswap16(s[i]);
            } else {
                memcpy(swap_buf, fb->buf, data_size);
            }
            src = swap_buf;
            // 顺时针旋转 90°：设备横屏使用时照片为正方向
            rotated = RotateRgb56590Cw((const uint16_t*)swap_buf, fb->width, fb->height);
            if (rotated != nullptr) {
                src = (uint8_t*)rotated;
                enc_w = fb->height;
                enc_h = fb->width;
            }
        }

        ok = image_to_jpeg_cb(src, fb->len, enc_w, enc_h, enc_fmt, 80,
            [](void* arg, size_t index, const void* data, size_t len) -> size_t {
                auto out = static_cast<std::vector<uint8_t>*>(arg);
                if (data != nullptr && len > 0) {
                    const uint8_t* p = static_cast<const uint8_t*>(data);
                    out->insert(out->end(), p, p + len);
                }
                return len;
            }, &out);

        if (swap_buf) {
            heap_caps_free(swap_buf);
        }
        if (rotated) {
            heap_caps_free(rotated);
        }
    }

    uint16_t fb_w = fb->width, fb_h = fb->height;
    esp_camera_fb_return(fb);

    if (!ok || out.empty()) {
        ESP_LOGE(TAG, "Snapshot JPEG encode failed");
        out.clear();
        return false;
    }
    ESP_LOGI(TAG, "Snapshot: %dx%d jpeg=%zu bytes", fb_w, fb_h, out.size());
    return true;
}
