#include "music_player.h"

#include <esp_log.h>

#include <algorithm>
#include <cstring>

#include "board.h"
#include "http.h"
#include <esp_audio_simple_dec.h>
#include <esp_ae_rate_cvt.h>

namespace {
constexpr uint32_t kMusicTaskStackSize = 8192;
constexpr UBaseType_t kMusicTaskPriority = 2;
constexpr int kHttpTimeoutMs = 8000;
constexpr size_t kHttpReadChunk = 4096;
// PCM pre-buffer kept by the worker, in decoded frames (~26 ms each).
// The playback queue itself only holds a couple of frames; this ring is
// what absorbs network jitter so the music does not stutter.
constexpr size_t kRingHighWatermark = 32;  // ~0.8 s
constexpr size_t kRingStartThreshold = 12; // ~0.3 s before first push
constexpr size_t kPcmFrameMaxSamples = 1152 * 2;
constexpr int kStartWaitTimeoutMs = 10000;

const char* TAG = "MusicPlayer";

bool IsSupportedUrl(const std::string& url) {
    return url.compare(0, 7, "http://") == 0 || url.compare(0, 8, "https://") == 0;
}
}  // namespace

MusicPlayer::MusicPlayer(AudioService& audio_service) : audio_service_(audio_service) {}

MusicPlayer::~MusicPlayer() { Stop(); }

bool MusicPlayer::Start(std::string url, FinishedCallback finished_callback) {
    if (!IsSupportedUrl(url)) {
        ESP_LOGE(TAG, "Unsupported URL: %s", url.c_str());
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (worker_running_.load()) {
            // Already playing: replace the current stream.
            cancelled_ = true;
        }
    }
    // Let the previous worker drain out before reusing the player.
    while (worker_running_.load()) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    {
        std::lock_guard<std::mutex> start_lock(start_mutex_);
        first_frame_decoded_ = false;
        start_failed_ = false;
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        url_ = std::move(url);
        finished_callback_ = std::move(finished_callback);
        cancelled_ = false;
    }

    // Set before creating the task: a worker that fails immediately must
    // not leave the flag stuck at true (or, if it exits before we set it,
    // at false while the task never runs).
    worker_running_ = true;
    if (xTaskCreate(WorkerEntry, "music_http", kMusicTaskStackSize, this, kMusicTaskPriority,
                    &task_handle_) != pdPASS) {
        ESP_LOGE(TAG, "Failed to create music worker task");
        task_handle_ = nullptr;
        worker_running_ = false;
        return false;
    }
    // Wait until the first frame decodes so callers can report failure.
    bool ok = false;
    {
        std::unique_lock<std::mutex> start_lock(start_mutex_);
        ok = start_cv_.wait_for(start_lock, std::chrono::milliseconds(kStartWaitTimeoutMs),
                                [this]() { return first_frame_decoded_ || start_failed_; });
        ok = ok && first_frame_decoded_;
    }
    if (!ok) {
        ESP_LOGE(TAG, "Music stream did not start in time: %s", url_.c_str());
        Stop();
        return false;
    }
    return true;
}

void MusicPlayer::Cancel() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        cancelled_ = true;
    }
    {
        // Wake Start() up if it is still waiting on the first frame.
        std::lock_guard<std::mutex> start_lock(start_mutex_);
        start_failed_ = true;
        start_cv_.notify_all();
    }
}

void MusicPlayer::Stop() {
    Cancel();
    while (worker_running_.load()) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

bool MusicPlayer::IsBusy() const { return worker_running_.load(); }

void MusicPlayer::WorkerEntry(void* arg) {
    auto* player = static_cast<MusicPlayer*>(arg);
    player->WorkerTask();
    player->task_handle_ = nullptr;
    player->worker_running_ = false;
    vTaskDelete(nullptr);
}

void MusicPlayer::WorkerTask() {
    bool success = false;
    std::string url;
    FinishedCallback finished_callback;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        url = url_;
        finished_callback = finished_callback_;
    }

    auto http = Board::GetInstance().GetNetwork()->CreateHttp(0);
    esp_audio_simple_dec_handle_t dec = nullptr;
    esp_ae_rate_cvt_handle_t rate_cvt = nullptr;
    std::deque<std::vector<int16_t>> ring;
    std::vector<uint8_t> in_buf;
    std::vector<uint8_t> pcm;
    int src_channels = 0;

    auto finish = [&](bool result) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            finished_callback_ = nullptr;
        }
        if (finished_callback) {
            finished_callback(result);
        }
    };
    auto fail_start = [&]() {
        {
            std::lock_guard<std::mutex> start_lock(start_mutex_);
            start_failed_ = true;
            start_cv_.notify_all();
        }
    };

    do {
        if (!http) {
            ESP_LOGE(TAG, "No network/HTTP available");
            break;
        }
        http->SetTimeout(kHttpTimeoutMs);
        http->SetHeader("Accept", "audio/mpeg, audio/mp3, application/octet-stream, */*");
        http->SetHeader("Accept-Encoding", "identity");
        http->SetHeader("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) ESP32 Xiaozhi");
        if (!http->Open("GET", url.c_str())) {
            ESP_LOGE(TAG, "Music HTTP open failed: %d", http->GetLastError());
            break;
        }
        int status = http->GetStatusCode();
        if (status < 200 || status >= 300) {
            ESP_LOGE(TAG, "Music HTTP status %d for %s", status, url.c_str());
            break;
        }

        esp_audio_simple_dec_cfg_t dec_cfg = {};
        dec_cfg.dec_type = ESP_AUDIO_SIMPLE_DEC_TYPE_MP3;
        dec_cfg.use_frame_dec = false;
        if (esp_audio_simple_dec_open(&dec_cfg, &dec) != ESP_AUDIO_ERR_OK) {
            ESP_LOGE(TAG, "Failed to open MP3 decoder");
            break;
        }
        pcm.resize(kPcmFrameMaxSamples * 2 * sizeof(int16_t));

        bool http_eof = false;
        bool decode_error = false;
        bool streaming_started = false;
        while (!cancelled_.load() && !decode_error) {
            // 1. Keep the ring above the high watermark.
            while (!http_eof && ring.size() < kRingHighWatermark) {
                size_t old_size = in_buf.size();
                in_buf.resize(old_size + kHttpReadChunk);
                int size = http->Read(reinterpret_cast<char*>(in_buf.data() + old_size),
                                      kHttpReadChunk);
                if (size < 0) {
                    ESP_LOGE(TAG, "Music HTTP read failed: %d", http->GetLastError());
                    http_eof = true;  // treat as end of stream, play what we have
                    decode_error = true;
                    break;
                }
                in_buf.resize(old_size + (size > 0 ? size : 0));
                if (size == 0) {
                    http_eof = true;
                    break;
                }

                // Decode everything currently buffered.
                while (!in_buf.empty() && ring.size() < kRingHighWatermark) {
                    esp_audio_simple_dec_raw_t raw = {};
                    raw.buffer = in_buf.data();
                    raw.len = in_buf.size();
                    raw.eos = http_eof;
                    esp_audio_simple_dec_out_t out = {};
                    out.buffer = pcm.data();
                    out.len = pcm.size();
                    auto ret = esp_audio_simple_dec_process(dec, &raw, &out);
                    if (ret != ESP_AUDIO_ERR_OK && ret != ESP_AUDIO_ERR_BUFF_NOT_ENOUGH) {
                        ESP_LOGE(TAG, "MP3 decode error %d", ret);
                        decode_error = true;
                        break;
                    }
                    if (out.decoded_size > 0) {
                        esp_audio_simple_dec_info_t info = {};
                        if (esp_audio_simple_dec_get_info(dec, &info) == ESP_AUDIO_ERR_OK) {
                            if (rate_cvt == nullptr) {
                                src_channels = info.channel;
                                if (info.sample_rate !=
                                    audio_service_.GetOutputSampleRate()) {
                                    esp_ae_rate_cvt_cfg_t cvt_cfg = {};
                                    cvt_cfg.src_rate = info.sample_rate;
                                    cvt_cfg.dest_rate =
                                        audio_service_.GetOutputSampleRate();
                                    cvt_cfg.channel = 1;  // mix down before converting
                                    cvt_cfg.bits_per_sample = 16;
                                    cvt_cfg.complexity = 2;
                                    if (esp_ae_rate_cvt_open(&cvt_cfg, &rate_cvt) !=
                                        ESP_AE_ERR_OK) {
                                        ESP_LOGE(TAG, "Failed to open rate converter");
                                        decode_error = true;
                                        break;
                                    }
                                }
                            }

                            // Down-mix to mono.
                            size_t frames = out.decoded_size / sizeof(int16_t) /
                                            src_channels;
                            std::vector<int16_t> mono(frames);
                            const int16_t* src =
                                reinterpret_cast<const int16_t*>(pcm.data());
                            if (src_channels == 1) {
                                memcpy(mono.data(), src, frames * sizeof(int16_t));
                            } else {
                                for (size_t i = 0; i < frames; i++) {
                                    int32_t mix = 0;
                                    for (int ch = 0; ch < src_channels; ch++) {
                                        mix += src[i * src_channels + ch];
                                    }
                                    mono[i] = static_cast<int16_t>(mix / src_channels);
                                }
                            }

                            // Convert to the codec rate (or pass through).
                            if (rate_cvt != nullptr) {
                                uint32_t max_out = 0;
                                esp_ae_rate_cvt_get_max_out_sample_num(
                                    rate_cvt, mono.size(), &max_out);
                                std::vector<int16_t> converted(max_out);
                                uint32_t actual_out = max_out;
                                if (esp_ae_rate_cvt_process(
                                        rate_cvt,
                                        reinterpret_cast<esp_ae_sample_t*>(mono.data()),
                                        mono.size(),
                                        reinterpret_cast<esp_ae_sample_t*>(
                                            converted.data()),
                                        &actual_out) == ESP_AE_ERR_OK &&
                                    actual_out > 0) {
                                    converted.resize(actual_out);
                                    ring.emplace_back(std::move(converted));
                                }
                            } else {
                                ring.emplace_back(std::move(mono));
                            }
                        }
                    }
                    if (raw.consumed > 0) {
                        in_buf.erase(in_buf.begin(),
                                     in_buf.begin() + raw.consumed);
                    } else if (out.decoded_size == 0) {
                        break;  // need more input data
                    }
                }
                if (decode_error) {
                    break;
                }
            }

            // 2. Feed the playback queue once the pre-buffer is filled.
            if (!ring.empty() &&
                (streaming_started || ring.size() >= kRingStartThreshold || http_eof) &&
                audio_service_.TryPushPcmToPlaybackQueue(ring.front())) {
                ring.pop_front();
                streaming_started = true;

                if (!first_frame_decoded_) {
                    std::lock_guard<std::mutex> start_lock(start_mutex_);
                    first_frame_decoded_ = true;
                    start_cv_.notify_all();
                    ESP_LOGI(TAG, "Music stream started: %s", url.c_str());
                }
            }

            // 3. Finished when everything has been played out.
            if (http_eof && in_buf.empty() && ring.empty()) {
                success = true;
                break;
            }

            vTaskDelay(pdMS_TO_TICKS(5));
        }
    } while (false);

    if (dec != nullptr) {
        esp_audio_simple_dec_close(dec);
    }
    if (rate_cvt != nullptr) {
        esp_ae_rate_cvt_close(rate_cvt);
    }
    if (http) {
        http->Close();
    }

    if (!first_frame_decoded_) {
        fail_start();
    }
    {
        std::lock_guard<std::mutex> start_lock(start_mutex_);
        // Make sure a concurrent Start() is not stuck waiting either.
        start_failed_ = true;
        start_cv_.notify_all();
    }
    ESP_LOGI(TAG, "Music worker finished (%s): %s",
             success ? "completed" : "aborted", url.c_str());
    finish(success);
}
