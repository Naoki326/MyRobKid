#include "music_player.h"

#include <esp_log.h>

#include <algorithm>
#include <cmath>
#include <cstring>

#include "board.h"
#include "http.h"
#include <esp_audio_simple_dec.h>
#include <esp_audio_dec_default.h>
#include <esp_audio_simple_dec_default.h>
#include <esp_ae_rate_cvt.h>
#include <esp_heap_caps.h>
#include <mutex>
#include <new>

// Large music buffers live in PSRAM: with everything in internal RAM the
// heap dropped below 20KB while streaming (watchdog + websocket drops).
template <class T>
struct PsramAllocator {
    using value_type = T;
    PsramAllocator() = default;
    template <class U> PsramAllocator(const PsramAllocator<U>&) noexcept {}
    T* allocate(std::size_t n) {
        if (n > std::size_t(-1) / sizeof(T)) throw std::bad_alloc();
        void* p = n ? heap_caps_malloc(n * sizeof(T), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)
                    : nullptr;
        if (n && !p) throw std::bad_alloc();
        return static_cast<T*>(p);
    }
    void deallocate(T* p, std::size_t) noexcept { heap_caps_free(p); }
};
template <class T, class U>
bool operator==(const PsramAllocator<T>&, const PsramAllocator<U>&) noexcept { return true; }
template <class T, class U>
bool operator!=(const PsramAllocator<T>&, const PsramAllocator<U>&) noexcept { return false; }
using PsramBytes = std::vector<uint8_t, PsramAllocator<uint8_t>>;
using PsramPcm = std::vector<int16_t, PsramAllocator<int16_t>>;

namespace {
constexpr uint32_t kMusicTaskStackSize = 8192;
constexpr UBaseType_t kMusicTaskPriority = 2;
constexpr int kHttpTimeoutMs = 8000;
// 恢复第一段的试读窗口（issue #4）：暂停时上游被反压堵着，连接若健康，
// 恢复读取数据会立刻到达；窗口内一个字节都没到即判死。
constexpr int kResumeProbeTimeoutMs = 2500;
// 重起流的安全余量（ADR-0009）：宁可重复一小段，也不要跳词。
constexpr int kResumeSafetyMarginSeconds = 2;
constexpr size_t kHttpReadChunk = 4096;
// PCM pre-buffer kept by the worker, in decoded frames (~26 ms each).
// The playback queue itself only holds a couple of frames; this ring is
// what absorbs network jitter so the music does not stutter.
constexpr size_t kRingHighWatermark = 32;  // ~0.8 s
constexpr size_t kRingStartThreshold = 12; // ~0.3 s before first push
// Cap on compressed (undecoded) data. Without it the fill loop over-reads
// (4KB per iteration to decode ~200B of audio) and in_buf grows unbounded —
// with a head-erase per iteration this becomes an O(n) PSRAM memmove storm.
constexpr size_t kMaxCompressedBuffer = 64 * 1024;
constexpr size_t kPcmFrameMaxSamples = 1152 * 2;
constexpr int kStartWaitTimeoutMs = 10000;

const char* TAG = "MusicPlayer";

bool IsSupportedUrl(const std::string& url) {
    return url.compare(0, 7, "http://") == 0 || url.compare(0, 8, "https://") == 0;
}

// 位点折算的单一定义（ADR-0010）：起始偏移 + 已推入播放队列的 PCM 折算秒数。
// 状态上报与 pipe: 遥测都经此，同一口径不写两遍——两处分叉会让「串口断言
// 通过」与「模型答出的位点」各说各话。
// 整秒口径（pipe: 周期行 / 状态上报）与一位小数口径（锚点行，spec 的
// ±0.5s 验收缝要一位小数才够得着）共用同一个折算，不会分叉。
double PositionSecondsF(int start_offset_s, uint64_t pushed_samples, int sample_rate) {
    if (sample_rate <= 0) {
        sample_rate = 1;
    }
    return static_cast<double>(start_offset_s) +
           static_cast<double>(pushed_samples) / static_cast<double>(sample_rate);
}

int PositionSeconds(int start_offset_s, uint64_t pushed_samples, int sample_rate) {
    return static_cast<int>(PositionSecondsF(start_offset_s, pushed_samples, sample_rate));
}
}  // namespace

MusicPlayer::MusicPlayer(AudioService& audio_service) : audio_service_(audio_service) {}

MusicPlayer::~MusicPlayer() { Stop(); }

bool MusicPlayer::Start(std::string url, FinishedCallback finished_callback) {
    if (!IsSupportedUrl(url)) {
        ESP_LOGE(TAG, "Unsupported URL: %s", url.c_str());
        return false;
    }

    bool replacing = false;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        replacing = worker_running_.load();
    }
    if (replacing) {
        // Already playing: replace the current stream. 收场分类要区分「换歌」
        // 与「用户按停」——旧会话的收场归新会话，不出声（kReplaced）。
        // 注：不能在持 mutex_ 时调 CancelWith（它自己取那把锁）。
        CancelWith(CancelCause::kReplace);
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
        cancel_cause_.store(CancelCause::kNone);
        // 新会话：上一首的暂停态与待恢复标记一并清掉（换歌是替换语义，
        // 不是恢复语义——被暂停的旧会话没有资格再被接回来）。
        paused_ = false;
        resume_pending_ = false;
        // 内容属性与起点从播放地址读回（起点由 play_music 工具以 ss= 追加），
        // 位点记账清零。
        meta_ = ParseMusicContentMeta(url_);
        start_s_ = ParseMusicStartSeconds(url_);
        pushed_samples_.store(0);
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
        // 自我拆掉（从未出声）：收场分类因此是 start_failed，不是
        // 「用户停」也不是「链路中断」——起播那一刻已经报过错，不再出声。
        CancelWith(CancelCause::kStartAbort);
        Stop();
        return false;
    }
    return true;
}

void MusicPlayer::CancelWith(CancelCause cause) {
    // 先到者胜（无锁 CAS）：先换歌、后来了个停止指令时，收场原因应当是那个
    // 先发生的真原因，而不是后到的噪声（旧会话本来就在为换歌退场）。用 CAS
    // 而不是取 mutex_：取消可能发生在持有 mutex_ 的路径里，取锁会自锁。
    CancelCause expected = CancelCause::kNone;
    cancel_cause_.compare_exchange_strong(expected, cause);
    {
        // Wake Start() up if it is still waiting on the first frame.
        std::lock_guard<std::mutex> start_lock(start_mutex_);
        start_failed_ = true;
        start_cv_.notify_all();
    }
}

void MusicPlayer::Cancel() { CancelWith(CancelCause::kUserStop); }

void MusicPlayer::Stop() {
    Cancel();
    while (worker_running_.load()) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

bool MusicPlayer::Pause(PauseKind kind) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!worker_running_.load()) {
        return false;
    }
    // 用户暂停不被会话性让位降级：用户说「暂停」之后又来一次唤醒，音乐
    // 仍然不许自动回来（否则「说暂停后随便聊一句音乐自己响了」）。
    if (paused_ && pause_kind_ == PauseKind::kUser && kind == PauseKind::kConversation) {
        return true;
    }
    // 反过来可以升级：会话性暂停期间用户改口说「暂停」，从此不再自动续。
    paused_ = true;
    pause_kind_ = kind;
    return true;
}

void MusicPlayer::CancelPause() {
    // 换歌/真停止的清理：撤掉待恢复标记（会话不该被接回来）。
    // 只撤「会话该不该恢复」这层语义，**不会**让暂停中的 worker 立刻继续读
    // ——那由 Start() 的替换路径或 Cancel() 的退出路径负责。否则一次「登记
    // 新歌」就会让旧歌在回答还没说完时自己响起来。
    // 暂停标志本身不在这里清：它决定 worker 是继续等还是往下走，得由
    // Start()（换歌，进不去暂停分支了）或 Cancel()（worker 退出）来收。
    std::lock_guard<std::mutex> lock(mutex_);
    resume_pending_ = false;
}

bool MusicPlayer::Resume() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!worker_running_.load() || !paused_) {
        return false;
    }
    // 只清标志：试读与「必要时重起流」都由 worker 自己完成（探读窗口
    // 2.5s，重起流还要更久，不能堵住调用方——它跑在主循环上）。
    paused_ = false;
    resume_pending_ = true;
    return true;
}

bool MusicPlayer::IsBusy() const { return worker_running_.load(); }

bool MusicPlayer::IsPlaying() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return worker_running_.load() && !paused_;
}

bool MusicPlayer::IsPaused() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return worker_running_.load() && paused_;
}

PauseKind MusicPlayer::GetPauseState() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return pause_kind_;
}

MusicPlaybackStatus MusicPlayer::GetPlaybackStatus() const {
    MusicPlaybackStatus status;
    std::lock_guard<std::mutex> lock(mutex_);
    if (!worker_running_.load()) {
        // 空闲：不携带上一首的陈旧位点。
        status.state = MusicPlaybackStatus::State::kIdle;
        return status;
    }
    // 暂停态报**冻结**位点：暂停后不再推帧，PositionSeconds 天然钉住不动，
    // 不需要额外记账。
    if (!paused_) {
        status.state = MusicPlaybackStatus::State::kPlaying;
    } else {
        // 暂停种类（两态）→ 上报状态（四态）的映射。字符串口径不变：
        // paused_conversation / paused_user（ADR-0010）。
        status.state = pause_kind_ == PauseKind::kUser
                           ? MusicPlaybackStatus::State::kPausedUser
                           : MusicPlaybackStatus::State::kPausedConversation;
    }
    status.title = meta_.title;
    status.author = meta_.author;
    status.duration_s = meta_.duration_s;
    status.seekable = !meta_.live;
    if (status.seekable) {
        // 直播流不记位点：恢复是重连，不是定位。
        int rate = audio_service_.GetOutputSampleRate();
        status.position_s = PositionSeconds(
            start_s_, pushed_samples_.load(std::memory_order_relaxed), rate);
    }
    return status;
}

void MusicPlayer::WorkerEntry(void* arg) {
    auto* player = static_cast<MusicPlayer*>(arg);
    // The callback belongs to *this* session: WorkerTask snapshotted it when
    // the session began and hands it back here, together with this session's
    // result. Reading shared members now would race with a Start() that
    // replaces the stream (swap songs) — the old worker would fire the *new*
    // session's callback, or the new worker's result, with the old one's.
    SessionOutcome outcome = player->WorkerTask();
    player->task_handle_ = nullptr;
    // 先清 worker_running_，再发回调：回调里 IsBusy()/IsPlaying() 必须说
    // 真话（HandleMusicFinished 用它们决定归还省电与唤醒词）。
    player->worker_running_ = false;
    player->NotifyFinished(std::move(outcome));
    vTaskDelete(nullptr);
}

bool MusicPlayer::TakeResumePending() {
    std::lock_guard<std::mutex> lock(mutex_);
    bool pending = resume_pending_;
    resume_pending_ = false;
    return pending;
}

std::optional<RestartSeam> MusicPlayer::PrepareRestart() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (url_.empty()) {
        return std::nullopt;
    }
    const int rate = audio_service_.GetOutputSampleRate();
    RestartSeam seam;
    seam.from_s = PositionSecondsF(start_s_, pushed_samples_.load(std::memory_order_relaxed),
                                   rate);
    seam.margin_s = kResumeSafetyMarginSeconds;
    // 请求的重起位点 = 当前位点回退安全余量（clamp ≥0），一位小数打进锚点
    // （at= 与 from= 的差就是回退量）。写进地址的 ss= 只能是整数秒：向下取整，
    // 多退不到 1s——方向与 ADR-0009 一致（宁可重复一小段，不跳词）。
    seam.at_s = std::max(0.0, seam.from_s - static_cast<double>(seam.margin_s));
    if (meta_.live) {
        // 直播流位点无意义：重连到现场（ADR-0009「位点必须丢弃，而非拒绝」）。
        seam.from_s = 0.0;
        seam.at_s = 0.0;
        seam.margin_s = 0;
    }
    const int restart_s = static_cast<int>(std::floor(seam.at_s));
    // 替换语义：先摘掉旧的 ss= 再追加新的。AppendMusicStart 是**追加**，
    // 直接重拼会让地址里出现两个 ss=（谁生效取决于上游解析顺序）。
    std::string next = AppendMusicStart(RemoveMusicStart(url_), restart_s);
    url_ = std::move(next);
    start_s_ = restart_s;
    // 位点仍报绝对值且连续：新流从 restart_s 起算，已推帧重新记账。
    pushed_samples_.store(0);
    return seam;
}

MusicPlayer::SessionOutcome MusicPlayer::WorkerTask() {
    // Snapshot the callback at session start: it is *this* session's callback
    // even if a later Start() swaps songs and installs a new one.
    FinishedCallback finished_callback;
    std::string url;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        url = url_;
        finished_callback = finished_callback_;
    }

    StreamEnd end = StreamEnd::kFailed;
    bool attempted_restart = false;
    for (;;) {
        end = StreamOnce();
        if (end != StreamEnd::kRestart) {
            break;
        }
        if (Cancelled()) {
            // 取消优先于恢复：用户要的是停止，不是换个连接接着放。
            end = StreamEnd::kCancelled;
            break;
        }
        auto seam = PrepareRestart();
        if (!seam.has_value()) {
            ESP_LOGE(TAG, "Music resume failed: stream URL unusable");
            end = StreamEnd::kFailed;
            attempted_restart = true;
            break;
        }
        MusicContentMeta meta;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            meta = meta_;
        }
        // 续播锚点（串口断言用）：重启式续播的「从哪来、请求从哪起」。位点带
        // 一位小数（spec 的 ±0.5s 接缝验收），回退安全余量（宁可重复一小段，
        // 不跳词）在这条行里看得见。
        if (meta.live) {
            ESP_LOGI(TAG, "Music resume: mode=restart at=live from=live margin=%ds",
                     seam->margin_s);
        } else {
            ESP_LOGI(TAG, "Music resume: mode=restart at=%.1fs from=%.1fs margin=%ds",
                     seam->at_s, seam->from_s, seam->margin_s);
        }
        attempted_restart = true;
    }

    if (!first_frame_decoded_) {
        {
            std::lock_guard<std::mutex> start_lock(start_mutex_);
            start_failed_ = true;
            start_cv_.notify_all();
        }
    }
    bool played = false;
    {
        std::lock_guard<std::mutex> start_lock(start_mutex_);
        played = first_frame_decoded_;
        // Make sure a concurrent Start() is not stuck waiting either.
        start_failed_ = true;
        start_cv_.notify_all();
    }

    // ── 收场分类（issue #7）─────────────────────────────────────
    // 在这里、会话状态被下一次 Start() 覆盖**之前**把五个事实位读齐：
    //   played —— 本次会话是否真出过声（start_mutex_ 保护）。这是「中断」
    //             与「起流失败」的分水岭：没听见声音就不存在「断了」。
    //   drained —— 缓冲真播空了（EOF 且队列排干）。
    //   cancelled/replaced —— 为什么被停（cancel_cause_，先到者胜）。
    //   attempted_restart —— 走过续播重连。
    // 「用户停」与「链路中断」从前都是 success=false，分不开；现在由原因
    // 驱动，用户按下停止键不会再听见警报音。
    MusicEndingFacts facts;
    facts.played = played;
    facts.drained = (end == StreamEnd::kDrained);
    const CancelCause cancel_cause = cancel_cause_.load();
    facts.cancelled = cancel_cause != CancelCause::kNone;
    facts.replaced = cancel_cause == CancelCause::kReplace;
    facts.attempted_restart = attempted_restart;
    const MusicEnding ending = DeriveMusicEnding(facts);

    // 收场时点与内容形态：位点仍由本次会话记账（worker_running_ 要到
    // WorkerEntry 才清），直播流不报数字位点。
    MusicContentMeta meta;
    int start_s = 0;
    uint64_t pushed = 0;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        meta = meta_;
        start_s = start_s_;
        pushed = pushed_samples_.load(std::memory_order_relaxed);
    }
    const double position_s =
        meta.live ? 0.0
                  : PositionSecondsF(start_s, pushed,
                                     audio_service_.GetOutputSampleRate());

    // 收场锚点（串口断言用，取代旧的 Music worker finished）：原因 + 是否出过声
    // + 位点。真故障用 LOGE，用户主动停止/换歌/自然播完用 LOGI——「安静地正常
    // 结束」与「出了事」在日志里也不该长得一样。
    char pos_field[32];
    // 一位小数：与 pause/resume 锚点同口径，收场断在哪里要比整秒清楚。
    WritePositionField(position_s, meta.live, pos_field, sizeof(pos_field));
    if (MusicEndingIsFailure(ending)) {
        ESP_LOGE(TAG, "Music ended: reason=%s played=%d pos=%s url=%s",
                 MusicEndingName(ending), facts.played ? 1 : 0, pos_field, url.c_str());
    } else {
        ESP_LOGI(TAG, "Music ended: reason=%s played=%d pos=%s url=%s",
                 MusicEndingName(ending), facts.played ? 1 : 0, pos_field, url.c_str());
    }

    // 结果与回调一起交给 WorkerEntry（它清完 worker_running_ 再发出）：调用方
    // 在回调里问「播放器还在忙吗」才是准的（省电归还、唤醒词恢复都按这个判断走）。
    // 结果不走共享成员——快速失败的新会话可能已经把共享结果覆盖了。
    SessionOutcome outcome;
    outcome.callback = std::move(finished_callback);
    outcome.result.ending = ending;
    outcome.result.position_s = position_s;
    outcome.result.live = meta.live;
    return outcome;
}

void MusicPlayer::NotifyFinished(SessionOutcome outcome) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        // 会话已死：暂停/待恢复标记一并清掉，别让下一次 Start() 读到残影。
        paused_ = false;
        resume_pending_ = false;
    }
    if (outcome.callback) {
        outcome.callback(outcome.result);
    }
}

MusicPlayer::StreamEnd MusicPlayer::StreamOnce() {
    std::string url;
    MusicContentMeta meta;
    int start_s = 0;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        url = url_;
        meta = meta_;
        start_s = start_s_;
    }
    if (url.empty()) {
        ESP_LOGE(TAG, "Music stream URL is empty");
        return StreamEnd::kFailed;
    }
    // 位点折算基准：推入播放队列的 PCM 已是输出采样率（含重定向后的单声道）。
    const uint64_t output_rate =
        static_cast<uint64_t>(std::max(1, audio_service_.GetOutputSampleRate()));

    // 缓冲与句柄全归 worker 所有：恢复要走重起流时由 worker 自己拆掉重建，
    // 别的任务不许伸手进来（ring/in_buf/解码器三者必须同时换代）。
    auto http = Board::GetInstance().GetNetwork()->CreateHttp(0);
    esp_audio_simple_dec_handle_t dec = nullptr;
    esp_ae_rate_cvt_handle_t rate_cvt = nullptr;
    std::deque<PsramPcm> ring;
    PsramBytes in_buf;
    std::vector<uint8_t> pcm;
    int src_channels = 0;
    bool pause_logged = false;
    StreamEnd result = StreamEnd::kFailed;

    auto close_stream = [&]() {
        if (dec != nullptr) {
            esp_audio_simple_dec_close(dec);
            dec = nullptr;
        }
        if (rate_cvt != nullptr) {
            esp_ae_rate_cvt_close(rate_cvt);
            rate_cvt = nullptr;
        }
        if (http != nullptr) {
            http->Close();
        }
    };
    // 暂停态一次读全（种类与标志必须同一把锁下取，否则可能读到「暂停了但
    // 种类还是上一轮残留」的错配）。
    auto paused_snapshot = [&](PauseKind* kind) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (kind != nullptr) {
            *kind = pause_kind_;
        }
        return paused_;
    };
    // pipe: 周期行用整秒（2 秒一拍的遥测不该伪造精度）。
    auto write_pos_int = [&](int seconds, char* out, size_t out_size) {
        if (meta.live) {
            snprintf(out, out_size, "live");
        } else {
            snprintf(out, out_size, "%ds", seconds);
        }
    };
    // 锚点行（pause / resume / restart）用一位小数：spec 的验收缝是
    // 「恢复后位点从原处继续（±0.5s）」，整秒量化够不着。与 pipe: 行同走
    // 位点折算（整秒口径是 PositionSeconds，一位小数是 PositionSecondsF），
    // 两处数字不会分叉。
    auto write_pos_tenths = [&](double seconds, char* out, size_t out_size) {
        if (meta.live) {
            snprintf(out, out_size, "live");
        } else {
            snprintf(out, out_size, "%.1fs", seconds);
        }
    };
    auto current_position_s = [&]() {
        return PositionSeconds(start_s_, pushed_samples_.load(std::memory_order_relaxed),
                               static_cast<int>(output_rate));
    };
    // 锚点用：未量化的帧级位点（整秒记账 + 不足一秒的余数）。
    auto current_position_f = [&]() {
        return PositionSecondsF(start_s_, pushed_samples_.load(std::memory_order_relaxed),
                                static_cast<int>(output_rate));
    };

    do {
        // Codecs (MP3, ...) must be registered into the runtime decoder
        // registry before esp_audio_simple_dec_open can find them. Without
        // this, opening the MP3 decoder fails with "not registered"
        // (AUD_SDEC ret -7) and music playback never starts.
        static std::once_flag dec_register_once;
        std::call_once(dec_register_once, []() {
            esp_audio_dec_register_default();
            esp_audio_simple_dec_register_default();
        });
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
        bool stream_drained = false;
        bool restart = false;
        bool streaming_started = false;
        // Pipeline telemetry: where the stream stalls (network read, decode
        // or playback queue) is otherwise invisible.
        size_t stat_read_bytes = 0;
        size_t stat_pushed = 0;
        size_t stat_push_fail = 0;
        TickType_t stat_last = xTaskGetTickCount();
        // EOF 收尾观测（issue #3）：流结束后剩余的解不动尾巴（标签/垃圾帧）
        // 不能让会话永远挂在「在播」——唤醒词不恢复、位点停在旧值。
        int eof_stall_passes = 0;

        // 位点遥测：绝对位点（起点 + 已推帧）；直播流标 live（不可定位）。
        // 与状态上报同走 PositionSeconds，两处数字不会分叉。暂停态带标记
        // （PAUSED_CONV / PAUSED_USER）：串口断言据此跳过暂停跨度的推进率与
        // 单调性检查，并核对「两种暂停态可区分」。
        auto log_pipe = [&]() {
            if (xTaskGetTickCount() - stat_last < pdMS_TO_TICKS(2000)) {
                return;
            }
            PauseKind paused_kind = PauseKind::kConversation;
            const bool is_paused = paused_snapshot(&paused_kind);
            char pos_field[32];
            write_pos_int(current_position_s(), pos_field, sizeof(pos_field));
            const char* pause_flag =
                !is_paused ? ""
                           : (paused_kind == PauseKind::kUser ? " PAUSED_USER"
                                                              : " PAUSED_CONV");
            ESP_LOGI(TAG,
                     "pipe: ring=%u/%u in_buf=%uB read=%uB/2s pushed=%u fail=%u pos=%s%s%s%s",
                     (unsigned)ring.size(), (unsigned)kRingHighWatermark,
                     (unsigned)in_buf.size(), (unsigned)stat_read_bytes,
                     (unsigned)stat_pushed, (unsigned)stat_push_fail, pos_field, pause_flag,
                     http_eof ? " EOF" : "", decode_error ? " DECERR" : "");
            stat_read_bytes = stat_pushed = stat_push_fail = 0;
            stat_last = xTaskGetTickCount();
        };

        // 暂停：不 Read、不解码、不推帧，但**不退出、不关连接**。
        // worker 一停止取数，http_client 的 8KB 关卡就堵住它自己的 TCP
        // 回调线程（http_client.cc OnTcpData），接收窗口随即关闭，Mac 侧
        // ffmpeg 自己停在写阻塞上（实测 0 CPU / 0 下载）。纯复用既有反压，
        // 不新增音频管线开关；ring（~0.8s）与 in_buf 故意留着，那是快路径
        // 「零间隙」的来源。
        auto wait_while_paused = [&]() {
            PauseKind kind = PauseKind::kConversation;
            if (!paused_snapshot(&kind)) {
                return false;
            }
            if (!pause_logged) {
                char pos_field[32];
                write_pos_tenths(current_position_f(), pos_field, sizeof(pos_field));
                ESP_LOGI(TAG, "Music pause: kind=%s pos=%s",
                         kind == PauseKind::kUser ? "user" : "conversation",
                         pos_field);
                pause_logged = true;
            }
            log_pipe();
            vTaskDelay(1);
            return true;
        };

        while (!Cancelled() && !decode_error) {
            // 1) 暂停判定必须在 HTTP 填充循环**之外**（在循环里判＝暂停期间
            // 仍把 in_buf 填满），此处先卡住整个一轮。
            if (wait_while_paused()) {
                continue;
            }
            // 2) 恢复：两段式。先试原连接（短超时探读），一个字节都没到即
            // 判死 → 返回 kRestart，由 WorkerTask 按位点重拼地址重起流。
            // 坑：http_client 把「读超时」与「连接硬错误」都折叠成 -1（干净
            // EOF 是 0），所以这里必须自己开分支，不能沿用下面 size<0 即
            // decode_error 的那条路；探完无论如何都要把超时还原成 8s——
            // 实例级超时留成 2.5s 会把后续一次真实卡顿误判成死连接。
            if (TakeResumePending()) {
                // 锚点行带一位小数（spec 的 ±0.5s 验收缝）；探针行只是诊断，
                // 另用整秒口径。
                char pos_field[32];
                write_pos_tenths(current_position_f(), pos_field, sizeof(pos_field));
                if (http_eof) {
                    // 暂停时流其实已经读完了：没有「连接还活着吗」要探，把
                    // 手里这点残余播完就正常收尾（探读会返回 0 → 误判成
                    // 「连接死了」→ 白重起一次流，把结尾又播一遍）。
                    pause_logged = false;
                    ESP_LOGI(TAG, "Music resume: mode=continue at=%s (stream ended)",
                             pos_field);
                } else {
                    http->SetTimeout(kResumeProbeTimeoutMs);
                    std::vector<uint8_t> probe(kHttpReadChunk);
                    int size = http->Read(reinterpret_cast<char*>(probe.data()),
                                          probe.size());
                    http->SetTimeout(kHttpTimeoutMs);
                    if (size > 0) {
                        // 连接还活着：把探到的字节接进压缩缓冲，原连接继续用。
                        in_buf.insert(in_buf.end(), probe.begin(), probe.begin() + size);
                        stat_read_bytes += size;
                        pause_logged = false;
                        // 续播锚点（串口断言用）：mode=continue 表示原连接接着读，
                        // at= 是接缝处的位点（暂停时冻结的那个）。
                        ESP_LOGI(TAG, "Music resume: mode=continue at=%s", pos_field);
                    } else {
                        // -1 = 探读超时/连接硬错误；0 = 已 EOF。暂停期间上游若把
                        // socket 收了（超过 proxy_read_timeout）就会走到这里——
                        // 判死的结论正是期望：位点还在，换连接接着放。
                        // 这一行**不是**续播锚点（锚点由 WorkerTask 在重起流前
                        // 打一条，带 from=/margin=）；这里只留探针结果，供定位
                        // 「为什么走了 restart」。用整秒：它不进断言。
                        char probe_pos[32];
                        write_pos_int(current_position_s(), probe_pos, sizeof(probe_pos));
                        ESP_LOGI(TAG, "Music resume probe: at=%s result=%d", probe_pos,
                                 size);
                        restart = true;
                        break;
                    }
                }
            }

            size_t iter_consumed = 0;
            size_t iter_decoded = 0;
            // 停滞判据用会话内单调的推帧总数，不用 stat_pushed——后者每 2 秒
            // 被遥测块清零，会把「本轮推过帧」误读成「本轮没推」。
            const uint64_t pushed_before = pushed_samples_.load(std::memory_order_relaxed);
            // 1. Keep the ring above the high watermark — and keep the
            // compressed backlog filled even when the ring is full.
            // The compressed-buffer cap gates only *reading*: decoding must
            // keep consuming in_buf regardless, otherwise the loop stalls
            // with a full in_buf and an empty ring. After EOF, keep decoding
            // whatever is still buffered — skipping decode at EOF would let
            // a backlog larger than the ring never drain.
            //
            // Pre-fill (2026-09-12, music stutter): the ring alone absorbs
            // only ~0.8 s of an upstream stall. The 64KB compressed backlog
            // (~8 s at 64kbps) is what actually rides out CDN hiccups and
            // ffmpeg reconnects (up to 5 s, -reconnect_delay_max). It lives
            // in PSRAM, so filling it in steady state costs no internal
            // SRAM. Gate: read while the ring has room OR the compressed
            // backlog is not yet full; the inner decode loop still honours
            // the ring watermark, so PCM never overflows.
            while ((ring.size() < kRingHighWatermark ||
                    in_buf.size() < kMaxCompressedBuffer) &&
                   (in_buf.size() > 0 || !http_eof)) {
                // 填充循环里也要能立刻停下：暂停标志可能刚刚在同一次填充中
                // 被置位，继续读完这一轮就把内存填成「暂停前的样子」了。
                PauseKind pause_kind = PauseKind::kConversation;
                if (paused_snapshot(&pause_kind)) {
                    break;
                }
                size_t old_size = in_buf.size();
                if (!http_eof && old_size < kMaxCompressedBuffer) {
                    in_buf.resize(old_size + kHttpReadChunk);
                    int size = http->Read(reinterpret_cast<char*>(in_buf.data() + old_size),
                                          kHttpReadChunk);
                    if (size < 0) {
                        ESP_LOGE(TAG, "Music HTTP read failed: %d", http->GetLastError());
                        if (paused_snapshot(nullptr)) {
                            // 暂停期间读挂了不是「会话结束」：用户还在暂停态里。
                            // 不推任何帧，交回暂停分支等着；恢复时探读会判死并
                            // 走重起流（位点还在，重取即可）。
                            in_buf.resize(old_size);
                            break;
                        }
                        http_eof = true;  // treat as end of stream, play what we have
                        decode_error = true;
                        break;
                    }
                    stat_read_bytes += size > 0 ? size : 0;
                    in_buf.resize(old_size + (size > 0 ? size : 0));
                    if (size == 0) {
                        http_eof = true;
                        break;
                    }
                }

                // Decode everything currently buffered.
                size_t consumed_total = 0;
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
                        iter_decoded += out.decoded_size;
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
                            PsramPcm mono(frames);
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
                                PsramPcm converted(max_out);
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
                        consumed_total += raw.consumed;
                    } else if (out.decoded_size == 0) {
                        break;  // need more input data
                    }
                }
                iter_consumed += consumed_total;
                // No reading happened and decoding consumed nothing: make
                // this iteration always exit to avoid a livelock. At EOF
                // this is the undecodable tail — leave it to the stall
                // counter in the finish checks below.
                if ((old_size >= kMaxCompressedBuffer || http_eof) &&
                    consumed_total == 0 && !in_buf.empty()) {
                    break;
                }
                if (decode_error) {
                    break;
                }
            }

            // 填充循环中途被暂停：这一轮不推帧（推出去就把「暂停」变成了
            // 几十毫秒后才静音），回到暂停分支原地等恢复。
            if (wait_while_paused()) {
                continue;
            }

            // 2. Feed the playback queue once the pre-buffer is filled.
            //    (The queue API takes a plain vector; copy the single frame
            //    out of the PSRAM ring — the long-lived buffers stay in PSRAM.)
            if (!ring.empty() &&
                (streaming_started || ring.size() >= kRingStartThreshold || http_eof)) {
                std::vector<int16_t> frame(ring.front().begin(), ring.front().end());
                // 位点记账先取帧样本数：TryPush 会 move 走 frame，之后 size()==0。
                const size_t frame_samples = frame.size();
                if (audio_service_.TryPushPcmToPlaybackQueue(frame)) {
                    ring.pop_front();
                    streaming_started = true;
                    stat_pushed++;
                    // 只算真正推出的一帧。ring 里尚未推出的预读帧不计——
                    // 那部分还没播，算进去位点就偏大。
                    pushed_samples_.fetch_add(frame_samples,
                                              std::memory_order_relaxed);

                    if (!first_frame_decoded_) {
                        std::lock_guard<std::mutex> start_lock(start_mutex_);
                        first_frame_decoded_ = true;
                        start_cv_.notify_all();
                        // 起流锚点（issue #3 串口断言用）：内容属性 + 起点。
                        ESP_LOGI(TAG,
                                 "Music stream started: title='%s' author='%s' "
                                 "duration=%ds form=%s start=%ds",
                                 meta.title.c_str(), meta.author.c_str(),
                                 meta.duration_s, meta.live ? "live" : "finite",
                                 start_s);
                    }
                } else {
                    stat_push_fail++;
                }
            }

            log_pipe();

            // 3. Finished when everything has been played out.
            if (http_eof && in_buf.empty() && ring.empty()) {
                stream_drained = true;
                break;
            }
            // EOF 后若解码器对剩余字节既不消费也不产出、队列也推不进
            // （可听内容其实已经播完，剩下的只是标签/垃圾尾巴），按正常
            // 播完收尾：位点停在最后的值，会话转入空闲，唤醒词恢复。
            // 阈值取 10 轮（~100ms）：先让队列里最后两帧播完，再判停滞。
            if (http_eof && ring.empty() && iter_consumed == 0 &&
                iter_decoded == 0 &&
                pushed_samples_.load(std::memory_order_relaxed) == pushed_before) {
                if (++eof_stall_passes >= 10) {
                    ESP_LOGI(TAG,
                             "Music tail drained: %uB undecodable at EOF, "
                             "pos=%us",
                             (unsigned)in_buf.size(),
                             (unsigned)PositionSeconds(
                                 start_s,
                                 pushed_samples_.load(std::memory_order_relaxed),
                                 static_cast<int>(output_rate)));
                    stream_drained = true;
                    break;
                }
            } else {
                eof_stall_passes = 0;
            }

            // NOTE: CONFIG_FREERTOS_HZ=100, so pdMS_TO_TICKS(5) rounds to 0
            // (vTaskDelay(0) yields without blocking) — the loop would spin
            // tens of thousands of times per second re-copying frames into
            // failed pushes, starving the audio DMA. One tick (10 ms) keeps
            // the ~24 ms/frame pace with margin.
            vTaskDelay(1);
        }

        if (restart) {
            result = StreamEnd::kRestart;
        } else if (stream_drained) {
            result = StreamEnd::kDrained;
        } else if (Cancelled()) {
            result = StreamEnd::kCancelled;
        } else {
            result = StreamEnd::kFailed;
        }
    } while (false);

    close_stream();
    return result;
}
