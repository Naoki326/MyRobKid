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
#include <optional>
#include <string>
#include <vector>

#include "audio_service.h"
#include "music_ending.h"
#include "music_url.h"

/*
 * MusicPlayer streams an HTTP(S) MP3 URL and feeds decoded PCM into
 * AudioService's playback queue, reusing the shared speaker path (volume
 * control, output power management, sample-rate conversion).
 *
 * The speaker is exclusive with conversation audio: callers are expected to
 * drop in-flight TTS first (AudioService::ResetDecoder()) before Start(),
 * and to pause or stop playback when the device is woken up again.
 *
 * Start() blocks until the first audio frame has been decoded (bounded by
 * the HTTP timeouts), so an MCP tool call can synchronously report whether
 * the URL is actually playable instead of returning a hollow "accepted".
 */

/*
 * 音乐会话快照（issue #3）：状态上报「播到哪了」的唯一事实源。
 * 四态与 CONTEXT.md 的口径一致：空闲 / 在播 / 会话性暂停 / 用户暂停，
 * 互不混淆；后两态由暂停票（issue #4）置入。
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

/*
 * 暂停语义只有两种（issue #4）：会话性让位（唤醒/新对话，答完静默数秒自动续）
 * 与用户暂停（必须说「继续」）。传参必须是这个两态枚举——若拿四态的
 * MusicPlaybackStatus::State 代替，kIdle/kPlaying 会被静默当成会话性暂停。
 */
enum class PauseKind { kConversation, kUser };

// 续播走的是重启路径时，重拼地址用的三个同族数字：暂停那一刻的位点、请求的
// 重起位点、回退的安全余量（秒）。三者恒同进同出，故收成一个结构而不是三个出参。
// 位点带一位小数（spec 的 ±0.5s 接缝验收要这精度）。at_s 是**请求**的重起位点
// （from_s - margin_s，锚点 at= 报它）；写进地址的 ss= 只能是整数秒，另取
// floor(at_s)，多退不到 1s——方向是 pre-roll，不与「不跳词」冲突。
struct RestartSeam {
    double from_s = 0.0;  // 暂停那一刻的绝对位点（锚点 from=）
    double at_s = 0.0;    // 请求的重起位点 = from_s - margin_s（锚点 at=）
    int margin_s = 0;     // 安全余量（宁可重复一小段，不跳词）；直播为 0
};

class MusicPlayer {
public:
    /*
     * 会话结束回调，在 worker 任务里调用（调用方负责转到自己的线程）。
     *
     * 从前只报两个 bool（success / resume_failed），装不下「用户自己停的」
     * ——它与「链路中断」都是 success=false 且 resume_failed=false，用户那边
     * 却是一个不用管、一个要检查网络（issue #7）。现在报的是**收场分类**
     * （music_ending.h）+ 收场时的位点，判定与反馈不再靠猜。
     *
     * 各字段与回调**同一条线程**产出（WorkerTask 一次返回），不落任何共享
     * 成员：换歌时旧 worker 的结果绝不能喂给新会话的回调，反之亦然。
     */
    struct FinishedResult {
        // 自然播完 / 链路中断 / 续播失败 / 用户主动停止 / 换歌被替换 / 起流失败
        MusicEnding ending = MusicEnding::kStartFailed;
        // 收场那一刻的绝对位点（一位小数口径，与锚点行同一折算）；直播流为
        // 0 且 live=true。用户停止与链路中断都靠它报出「断在哪」。
        double position_s = 0.0;
        bool live = false;
    };
    using FinishedCallback = std::function<void(const FinishedResult& result)>;

    explicit MusicPlayer(AudioService& audio_service);
    ~MusicPlayer();

    // Opens the URL and waits (bounded) for the first decoded frame.
    // Returns false when the URL cannot be played; on success a worker
    // task keeps feeding PCM in the background.
    bool Start(std::string url, FinishedCallback finished_callback);
    // Ask the worker to stop without waiting for it (safe from the main
    // loop: the worker may be blocked inside an HTTP read for a while).
    // This is a *stop*: the session ends and the position is dropped.
    // 收场分类是 kStopped（不是故障）——用户自己停的不该听见警报音。
    void Cancel();
    // Cancel() + wait until the worker has fully drained out.
    void Stop();

    /*
     * 谓词分家（issue #4）：「播放器在忙」在多个调用点语义不同（省电判定、
     * 延迟起播、停止路径、按钮打断、唤醒词恢复），一个 bool 装不下，于是
     * 拆成显式谓词：
     *   IsBusy()    worker 存活 = 会话仍持有连接与扬声器，对象不可复用
     *               （省电判定用它：暂停也占着资源，不能判成已空闲）
     *   IsPlaying() worker 存活且未暂停 = 真在出声（按钮打断/让位用它）
     *   IsPaused()  两种暂停之一
     *   GetPauseState() 暂停种类（仅 IsPaused() 为真时有意义；未暂停/空闲
     *                   的返回值不作保证，判断前先问 IsPaused()）
     */
    bool IsBusy() const;
    bool IsPlaying() const;
    bool IsPaused() const;
    PauseKind GetPauseState() const;

    /*
     * 暂停（非阻塞）：只置位，立即返回。真正的「停」发生在 worker 主循环
     * 入口——它不再从流里取数（不 Read / 不解码 / 不推帧），但 worker 与
     * 连接都留着：http_client 的 8KB 关卡随即堵住自己的 TCP 回调线程，
     * 接收窗口关闭，上游 ffmpeg 自己停在写阻塞上。复用既有反压，
     * 不新增音频管线开关。
     * 返回 false = 当前没有活着的音乐会话（调用方据此如实回话，别假成功）。
     */
    bool Pause(PauseKind kind);
    /*
     * 撤掉暂停态与待恢复标记（换歌 / 真停止时用）。用户暂停不会被会话性
     * 让位降级——那由 Pause() 内部的升级规则处理；这里是「这次会话不再
     * 需要恢复」的外部指令。
     */
    void CancelPause();

    /*
     * 恢复（非阻塞）：两段式。置位后立即返回；「先试原连接、必要时按位点
     * 重新起流」都由 worker 自己完成（探读要 2.5s，重起流还要更久，不能
     * 堵着主循环）。返回 false = 当前并不处于暂停态（重复说「继续」无副作用）。
     */
    bool Resume();

    void NotifyStartLocked(bool failed);

    // 音乐会话快照：状态 + 绝对位点（起点 + 已推入播放队列的 PCM 量）。
    // 位点口径（issue #3 的关键决定）：只算真正推出的帧——ring 预缓冲
    // （约 0.8s）与解码预读都不计入，否则位点系统性偏大，续播必跳词。
    // 空闲态不报陈旧位点；暂停态报**冻结**位点（暂停后不再推帧，天然钉住）。
    MusicPlaybackStatus GetPlaybackStatus() const;

private:
    /*
     * 一次流会话的结束方式。kRestart 不是失败：它是恢复时「原连接已死」的
     * 结论——换个连接按位点接着放，由 worker 自己拆掉旧缓冲、重拼地址后
     * 再进来一次（缓冲全归 worker 所有，别的任务不许伸手进来重建）。
     */
    enum class StreamEnd { kDrained, kCancelled, kRestart, kFailed };

    /*
     * 会话为什么被要求停止（issue #7）。取消从前只是一个 bool，于是
     * 「用户按停」与「换歌」在收场处彻底分不开——两者都得出「不是自然播完」，
     * 但一个要报「已停止」、一个不该吭声。停止**原因**是收场分类的输入，
     * 所以它得跟着取消一起记。kStartAbort 是起流失败后的自我拆掉
     * （从未出声，收场仍是 start_failed）。
     */
    enum class CancelCause { kNone, kUserStop, kReplace, kStartAbort };

    // 记下停止原因：先到者胜（已经是别的原因就保留先到的）。不取 mutex_
    // ——取消会从已持锁的路径上调用（见 .cc 的注释）。
    void CancelWith(CancelCause cause);
    bool Cancelled() const { return cancel_cause_.load() != CancelCause::kNone; }

    static void WorkerEntry(void* arg);
    // Returns this session's callback **and its result** (both snapshotted at
    // session start / produced at its end) so that WorkerEntry can fire them
    // after clearing worker_running_ without touching any shared member — by
    // then a Start() replacing the stream may already have installed a new
    // callback, and a fast-failing new session may already have overwritten a
    // shared result.
    struct SessionOutcome {
        FinishedCallback callback;
        FinishedResult result;
    };
    SessionOutcome WorkerTask();
    // 一次流会话：建连接、解码、推帧，直到播完/取消/判死（要重起流）。
    // 每次进入都自带全套缓冲与解码器句柄——重起流时由 worker 自己换代，
    // ring/in_buf/rate_cvt 三者必须同时重建（跨连接的旧缓冲是 stale 字节）。
    StreamEnd StreamOnce();
    bool TakeResumePending();
    // 会话真正结束后才回调（WorkerEntry 在 worker_running_ 清零之后调用，
    // 且传的是**本次会话**的回调与其结果）：调用方在回调里问「播放器还在
    // 忙吗」得到的是真答案。
    void NotifyFinished(SessionOutcome outcome);
    /*
     * 按位点重拼播放地址：起点 = 当前绝对位点回退 kResumeSafetyMarginSeconds
     * （宁可重复一小段，也不要跳词），并以替换语义写回 ss=（地址里不许出现
     * 两个 ss=）。直播流位点无意义：起点归零 = 重连到现场。
     * 返回的 from_s/at_s 供遥测区分「暂停时的位点」与「请求的重起位点」；
     * nullopt 表示地址不可用（不能重起流）。
     */
    std::optional<RestartSeam> PrepareRestart();

    AudioService& audio_service_;
    mutable std::mutex mutex_;
    std::string url_;
    FinishedCallback finished_callback_;
    TaskHandle_t task_handle_ = nullptr;
    // 取消**原因**（不是 bool）：收场分类要它才能把「用户停」「换歌」与
    // 「链路中断」分开（issue #7）。kNone = 没被取消过。
    std::atomic<CancelCause> cancel_cause_{CancelCause::kNone};
    // True while the worker task is alive; Start() waits for it to clear
    // before reusing the player. Cleared *before* the finished callback runs
    // so that "IsBusy() == false" inside that callback means what it says.
    std::atomic<bool> worker_running_{false};

    // 暂停（issue #4）：两种语义分开记。paused_ 与 pause_kind_ 总是一起改
    // （mutex_ 保护，读的一方也要取种类，索性不做无锁读）——用户暂停绝不
    // 自动续、必须说「继续」，单个 bool 表达不了这件事。
    bool paused_ = false;
    PauseKind pause_kind_ = PauseKind::kConversation;
    // Resume() 只置这个标记（非阻塞）；worker 在循环里取走并执行两段式恢复。
    bool resume_pending_ = false;

    // 位点记账（issue #3）：本次会话已成功推入播放队列的 PCM 样本数
    // （输出采样率口径）。只有 TryPushPcmToPlaybackQueue 成功才累加，
    // ring 里的预读帧不算。重起流时按新起点清零（位点仍绝对且连续）。
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
