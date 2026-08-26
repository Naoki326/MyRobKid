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

    // Start() waits on these until the worker proves the stream decodes.
    std::mutex start_mutex_;
    std::condition_variable start_cv_;
    bool first_frame_decoded_ = false;
    bool start_failed_ = false;
};

#endif  // MUSIC_PLAYER_H
