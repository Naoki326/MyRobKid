#ifndef MUSIC_PLAYER_H
#define MUSIC_PLAYER_H

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "audio_service.h"
#include "music_url.h"

/*
 * MusicPlayer streams an HTTP(S) MP3 URL and feeds decoded PCM into
 * AudioService's playback queue, reusing the shared speaker path (volume
 * control, output power management, sample-rate conversion).
 *
 * The speaker is exclusive with conversation audio: callers are expected to
 * drop in-flight TTS first (AudioService::ResetDecoder()) before Start(),
 * and to stop playback when the device is woken up again.
 *
 * Start() blocks until the first audio frame has been decoded (bounded by
 * the HTTP timeouts), so an MCP tool call can synchronously report whether
 * the URL is actually playable instead of returning a hollow "accepted".
 */

/*
 * 音乐会话快照（issue #3）：状态上报「播到哪了」的唯一事实源。
 * 四态与 CONTEXT.md 的口径一致：空闲 / 在播 / 会话性暂停 / 用户暂停，
 * 互不混淆；后两态由暂停票置入，本票先立口径（ADR-0010）。
 */
struct MusicPlaybackStatus {
    enum class State { kIdle, kPlaying, kPausedConversation, kPausedUser };

    State state = State::kIdle;
    bool seekable = false;  // 直播流=false：位点无意义，恢复将是重连而非定位
    int position_s = 0;     // 绝对位点：起点 + 已推帧折算；直播流/空闲时不报
    int duration_s = 0;     // 内容总时长（play_url 元数据）；未知/直播为 0
    std::string title;
    std::string author;

    // 状态上报用的稳定字符串口径（ADR-0010：四态互斥，暂停态不报陈旧位点）。
    const char* state_name() const {
        switch (state) {
            case State::kPlaying:
                return "playing";
            case State::kPausedConversation:
                return "paused_conversation";
            case State::kPausedUser:
                return "paused_user";
            case State::kIdle:
                break;
        }
        return "idle";
    }
};

class MusicPlayer {
public:
    using FinishedCallback = std::function<void(bool success)>;

    explicit MusicPlayer(AudioService& audio_service);
    ~MusicPlayer();

    // Opens the URL and waits (bounded) for the first decoded frame.
    // Returns false when the URL cannot be played; on success a worker
    // task keeps feeding PCM in the background.
    bool Start(std::string url, FinishedCallback finished_callback);
    // Ask the worker to stop without waiting for it (safe from the main
    // loop: the worker may be blocked inside an HTTP read for a while).
    void Cancel();
    // Cancel() + wait until the worker has fully drained out.
    void Stop();
    bool IsBusy() const;

    void NotifyStartLocked(bool failed);

    // 音乐会话快照：状态 + 绝对位点（起点 + 已推入播放队列的 PCM 量）。
    // 位点口径（issue #3 的关键决定）：只算真正推出的帧——ring 预缓冲
    // （约 0.8s）与解码预读都不计入，否则位点系统性偏大，续播必跳词。
    // 空闲/暂停态不报陈旧位点；直播流（live）不记位点。
    MusicPlaybackStatus GetPlaybackStatus() const;

private:
    static void WorkerEntry(void* arg);
    void WorkerTask();

    AudioService& audio_service_;
    mutable std::mutex mutex_;
    std::string url_;
    FinishedCallback finished_callback_;
    TaskHandle_t task_handle_ = nullptr;
    std::atomic<bool> cancelled_{false};
    // True while the worker task is alive; Start() waits for it to clear
    // before reusing the player.
    std::atomic<bool> worker_running_{false};

    // 位点记账（issue #3）：本次会话已成功推入播放队列的 PCM 样本数
    // （输出采样率口径）。只有 TryPushPcmToPlaybackQueue 成功才累加，
    // ring 里的预读帧不算。
    std::atomic<uint64_t> pushed_samples_{0};
    // 当前会话的内容属性与起点，Start() 时从播放地址解析（mutex_ 保护）。
    MusicContentMeta meta_;
    int start_s_ = 0;

    // Start() waits on these until the worker proves the stream decodes.
    std::mutex start_mutex_;
    std::condition_variable start_cv_;
    bool first_frame_decoded_ = false;
    bool start_failed_ = false;
};

#endif  // MUSIC_PLAYER_H
