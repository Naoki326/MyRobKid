#ifndef _APPLICATION_H_
#define _APPLICATION_H_

#include <freertos/FreeRTOS.h>
#include <freertos/event_groups.h>
#include <freertos/task.h>
#include <esp_timer.h>

#include <string>
#include <mutex>
#include <deque>
#include <memory>
#include <functional>
#include <cstdint>
#include <vector>

#include "protocol.h"
#include "ota.h"
#include "audio_service.h"
#include "device_state.h"
#include "device_state_machine.h"
#include "notify/notify_player.h"
#include "audio/music_player.h"
#include "audio/music_screen.h"

// Main event bits
#define MAIN_EVENT_SCHEDULE             (1 << 0)
#define MAIN_EVENT_SEND_AUDIO           (1 << 1)
#define MAIN_EVENT_WAKE_WORD_DETECTED   (1 << 2)
#define MAIN_EVENT_VAD_CHANGE           (1 << 3)
#define MAIN_EVENT_ERROR                (1 << 4)
#define MAIN_EVENT_ACTIVATION_DONE      (1 << 5)
#define MAIN_EVENT_CLOCK_TICK           (1 << 6)
#define MAIN_EVENT_NETWORK_CONNECTED    (1 << 7)
#define MAIN_EVENT_NETWORK_DISCONNECTED (1 << 8)
#define MAIN_EVENT_TOGGLE_CHAT          (1 << 9)
#define MAIN_EVENT_START_LISTENING      (1 << 10)
#define MAIN_EVENT_STOP_LISTENING       (1 << 11)
#define MAIN_EVENT_STATE_CHANGED        (1 << 12)
#define MAIN_EVENT_PLAYBACK_DRAINED     (1 << 13)


enum AecMode {
    kAecOff,
    kAecOnDeviceSide,
    kAecOnServerSide,
};

class Application {
public:
    static Application& GetInstance() {
        static Application instance;
        return instance;
    }
    // Delete copy constructor and assignment operator
    Application(const Application&) = delete;
    Application& operator=(const Application&) = delete;

    /**
     * Initialize the application
     * This sets up display, audio, network callbacks, etc.
     * Network connection starts asynchronously.
     */
    void Initialize();

    /**
     * Run the main event loop
     * This function runs in the main task and never returns.
     * It handles all events including network, state changes, and user interactions.
     */
    void Run();

    DeviceState GetDeviceState() const { return state_machine_.GetState(); }
    bool IsVoiceDetected() const { return audio_service_.IsVoiceDetected(); }
    
    /**
     * Request state transition
     * Returns true if transition was successful
     */
    bool SetDeviceState(DeviceState state);

    /**
     * Schedule a callback to be executed in the main task
     */
    void Schedule(std::function<void()>&& callback);

    /**
     * Alert with status, message, emotion and optional sound
     */
    void Alert(const char* status, const char* message, const char* emotion = "", const std::string_view& sound = "");
    void DismissAlert();

    void AbortSpeaking(AbortReason reason);

    /**
     * Toggle chat state (event-based, thread-safe)
     * Sends MAIN_EVENT_TOGGLE_CHAT to be handled in Run()
     */
    void ToggleChatState();

    /**
     * Start listening (event-based, thread-safe)
     * Sends MAIN_EVENT_START_LISTENING to be handled in Run()
     */
    void StartListening();

    /**
     * Stop listening (event-based, thread-safe)
     * Sends MAIN_EVENT_STOP_LISTENING to be handled in Run()
     */
    void StopListening();

    void Reboot();
    void WakeWordInvoke(const std::string& wake_word);
    bool UpgradeFirmware(const std::string& url, const std::string& version = "");
    bool CanEnterSleepMode();
    void SendMcpMessage(const std::string& payload);
    void RegisterMcpBroadcastCallback(std::function<void(const std::string&)> callback);
    void SetAecMode(AecMode mode);
    AecMode GetAecMode() const { return aec_mode_; }
    void PlaySound(const std::string_view& sound);
    AudioService& GetAudioService() { return audio_service_; }

    /*
     * 「音乐是否在出声」的单一谓词（issue #4）：省电判定、延迟起播分支、
     * 停止路径、按钮打断、唤醒词恢复（HandleStateChangedEvent 的 idle 分支）
     * 五处都经它，语义不再各说各话。
     * 注意 IsBusy() 不含暂停态——暂停同样占着 worker、连接与 PERFORMANCE
     * 省电等级，判「已空闲」会把 Wi-Fi 打回省电档、撕裂恢复后的音频流。
     */
    bool IsMusicPlaying() const { return music_player_.IsPlaying(); }
    bool IsMusicBusy() const { return music_player_.IsBusy(); }
    bool IsMusicPaused() const { return music_player_.IsPaused(); }
    /*
     * 暂停种类**只有播放器持有**（ADR-0010/0013：状态是播放器的事实），
     * 会话层按需现读、不缓存副本——缓存会与播放器分叉（用户暂停是降不
     * 下来的，抄一份入参就会把用户暂停误记成会话性暂停）。
     */
    bool IsMusicPauseConversational() const {
        return music_player_.IsPaused() &&
               music_player_.GetPauseState() == PauseKind::kConversation;
    }

    /*
     * ResumeMusic 的三态结果：resumed = 已真的开始续播；deferred = 已在说话
     * 途中登记，答复说完即续（**不是**已经出声）；nothing = 没有可续的暂停。
     * 调用方（MCP 工具）据此如实回话，别把 deferred 说成「已继续」。
     */
    enum class ResumeOutcome { kResumed, kDeferred, kNothing };

    /**
     * Start streaming an MP3 URL on the speaker (thread-safe).
     * Drops any in-flight conversation audio, blocks until the first frame
     * decodes and returns false when the URL is not playable. The music
     * keeps playing after the conversation ends; waking the device pauses it.
     */
    bool StartMusic(const std::string& url);
    /*
     * 真停止（`stop_music` 工具触发）：清空会话与位点，不再有「接着放」。
     * 与 PauseMusic 的分工是 issue #4 的核心——唤醒/新对话是让位（暂停），
     * 只有用户明确说「停止」才是停止。
     */
    void StopMusic();

    /*
     * 让位/用户暂停（issue #4）。两种语义必须分开记：
     *   PauseKind::kConversation — 唤醒或新对话触发，答完静默数秒自动续；
     *   PauseKind::kUser         — 用户说「暂停」触发，必须说「继续」才续。
     * 暂停期间唤醒词/聆听照常（暂停不是「半死」状态），省电等级保持
     * PERFORMANCE（worker 与连接都还在）。
     * 返回 false = 当前没有活着的音乐会话（调用方如实回话，别假成功）。
     */
    bool PauseMusic(PauseKind kind);
    /*
     * 从暂停处接着放（非阻塞）：试原连接 / 按位点重起流由播放器两段式完成。
     * 说话途中调用会被延后到这句答完（"说完话再播"，与换歌同一条路）——
     * 那时返回 kDeferred，调用方须如实说「已在排队」而不是「已继续」。
     */
    ResumeOutcome ResumeMusic();
    // 暂停态下不做静默自动续播（用户暂停、会话结束）。
    void CancelPauseAutoResume();

    // 音乐会话快照（issue #3）：状态上报经此取「播到哪了」。
    // 直接转述播放器的记账，线程安全。
    MusicPlaybackStatus GetMusicStatus() const { return music_player_.GetPlaybackStatus(); }

    /*
     * Starts the stream immediately (idle case). Must not be called while a
     * conversation is active. Runs on any task; blocks up to 10 s waiting for
     * the first decoded frame.
     */
    bool StartMusicNow(const std::string& url);
    // 立即续播（无对话在说话时走这条）：见 ResumeMusic 的注释。
    bool ResumeMusicNow();

    /*
     * URL deferred until the current conversation reply finishes playing
     * ("finish speaking before playing music"). Accessed only on the main
     * loop (Schedule) context.
     */
    std::string pending_music_url_;
    // 「继续」登记（同为 main loop 独占）：说「继续」时若还在说话，延后到
    // tts stop 再续播——与 pending_music_url_ 同一条路、同一个时刻执行。
    bool pending_music_resume_ = false;
    
    /**
     * Reset protocol resources (thread-safe)
     * Can be called from any task to release resources allocated after network connected
     * This includes closing audio channel, resetting protocol and ota objects
     */
    void ResetProtocol();

private:
    Application();
    ~Application();

    std::mutex mutex_;
    std::deque<std::function<void()>> main_tasks_;
    std::unique_ptr<Protocol> protocol_;
    EventGroupHandle_t event_group_ = nullptr;
    esp_timer_handle_t clock_timer_handle_ = nullptr;
    DeviceStateMachine state_machine_;
    ListeningMode listening_mode_ = kListeningModeAutoStop;
    AecMode aec_mode_ = kAecOff;
    std::string last_error_message_;
    AudioService audio_service_;
    NotifyPlayer notify_player_;
    MusicPlayer music_player_;
    uint32_t notification_playback_id_ = 0;
    std::unique_ptr<Ota> ota_;

    std::function<void(const std::string&)> mcp_broadcast_callback_;

    bool has_server_time_ = false;
    bool aborted_ = false;
    bool assets_version_checked_ = false;
    bool play_popup_on_listening_ = false;  // Flag to play popup sound after state changes to listening
    bool pending_listening_start_ = false;  // Waiting for playback to drain before starting listening (auto mode)
    int clock_ticks_ = 0;
    TaskHandle_t activation_task_handle_ = nullptr;

    /*
     * 会话性暂停的静默计时（issue #4）。必须**另起**计数器：clock_ticks_
     * 同时在驱动「每 10 秒打一次堆统计」，共用会被无关抖动清零。
     * 语义分离落在 Application 而不是播放器：播放器只认「暂停了没、哪种」，
     * 「什么时候该自己接上」是会话层的事。
     * 只有**会话性**暂停才 arm 计数器——用户暂停绝不 arm，这是「说暂停后
     * 随便聊一句音乐不自动响」的唯一实现手段。种类本身不在这里缓存：每次
     * 现读播放器（IsMusicPauseConversational），免得抄本与事实分叉。
     */
    int pause_quiet_ticks_ = 0;
    bool auto_resume_armed_ = false;
    // 本轮聆听里用户说过话（VAD 起过一次）。一旦说话，这一轮的自动续播就
    // 不再触发——他显然还有话要说，音乐不该插进来。
    bool pause_user_spoke_ = false;

    // TTS pre-buffering: collect incoming TTS audio packets and only start
    // playing after the server finishes the whole response (tts stop). This
    // avoids choppy playback when the server generates or delivers audio
    // slower than real time. Longer replies fall back to streaming once the
    // buffer reaches kTtsPreloadMaxBytes.
    static constexpr size_t kTtsPreloadMaxBytes = 64 * 1024;
    std::mutex tts_buffer_mutex_;
    std::deque<std::unique_ptr<AudioStreamPacket>> tts_buffer_;
    size_t tts_buffer_bytes_ = 0;
    bool tts_buffering_ = false;


    // Event handlers
    void HandleStateChangedEvent();
    void HandleToggleChatEvent();
    void HandleStartListeningEvent();
    void HandleStopListeningEvent();
    void HandleNetworkConnectedEvent();
    void HandleNetworkDisconnectedEvent();
    void HandleActivationDoneEvent();
    void HandleWakeWordDetectedEvent();
    void ContinueOpenAudioChannel(ListeningMode mode);
    void BeginWakeWordInvoke(const std::string& wake_word);
    void ContinueWakeWordInvoke(const std::string& wake_word);
    void StartListeningAudio();
    void ConfigureWakeWordForListening();
    void StartNotification(std::string audio_url, std::vector<NotifySubtitle> subtitles);
    // 音乐会话收场（issue #7）：按**收场分类**给用户可辨反馈（音效 + 屏幕），
    // 并把设备交回可交互态。分类由播放器产出（music_ending.h）——用户主动停止
    // 与换歌都不出声，自然播完与链路中断各给一个不同的音。
    void HandleMusicFinished(const MusicPlayer::FinishedResult& result);
    /*
     * 屏幕出口（issue #8）：音乐会话期间消息区显示曲目与作者，取代「待机」。
     * 所有音乐写屏都经这一个 helper：写屏时机（转态之后）、所有权标记与遥测
     * 锚点只在这里写一份，别在四个调用点各抄一遍。
     *
     * 空文本 = 清空消息区并交还所有权（此后别人清屏照旧生效，旧曲目不会
     * 复活）。调用方负责判「现在该不该清」（例如只在音乐握着消息区时才清
     * ——否则会擦掉别人的告警文案）。
     */
    void ShowMusicScreen(const char* action, const std::string& text, bool owns,
                         const MusicScreenFacts* facts = nullptr);
    /*
     * 用当前播放会话快照写「正在播放」（仅主循环线程调用）。
     * 会话已不在（快照为 kIdle）时清空并交还所有权。
     */
    void WriteMusicNowPlaying(const char* action);
    /*
     * 「别人的清屏/覆写」动作的统一入口（issue #8 决策 3/4）。
     *
     * 为什么必须有这一处：音乐在忙且消息区归音乐所有时，那些动作应当**重画**
     * 曲目而不是抹掉它——但**清屏 API 本身因显示变体而异**（LCD 的
     * `SetChatMessage("", "")` 在气泡变体里会留下残影、`ClearChatMessages()`
     * 才是对的；而 OLED/Emote 根本没重写 `ClearChatMessages()`，只有
     * `SetChatMessage("", "")` 有效）。所以清屏动作由调用方以回调传入，
     * 这个 helper 只负责**归属权判断与交还**（决策 4 说的「这两件事只有一处
     * 实现」）——散抄归属权判断正是 ADR-0015 决策 4 要消掉的东西。
     *
     * clear_fn 只在「音乐没握着消息区」时被调用；握着就重画（经
     * WriteMusicNowPlaying，快照现取，换歌后重画出来就是新曲目）。
     */
    void RepaintOrClearMusicScreen(std::function<void()>&& clear_fn);
    // 同上，但可从任意任务调用（排到主循环的下轮，保证晚于 pending 的转态）。
    void ScheduleMusicNowPlaying();
    /*
     * 消息区当前是不是归音乐所有（issue #8）。
     * 为什么需要它：不只 idle 分支会清消息区（音频通道关闭、通知结束、告警
     * 撤销、起播失败回落…）；而音乐**恰恰**在「说话态 → 空闲态」这条路径上
     * 起播。清屏的是状态转移、写曲目的是状态转移之后的 Schedule 回调，于是
     * 「不显示待机」要成立，就必须有一个显式标记告诉那些清屏点「这块区域现在
     * 的内容是曲目，重画而不是清掉」。
     * 只在主循环线程读写（写屏一律经 Schedule）。
     */
    bool music_screen_owns_content_ = false;
    /*
     * 消息区被重画的代数（issue #8）：idle 分支每走一次重画分支递增。`repaint`
     * 锚点报出它、`now-playing` 锚点报出当时的值——两条合起来就是「写屏接住了
     * 那次重画」的直接证据（序号只增不减）。
     */
    unsigned idle_repaint_gen_ = 0;
    /*
     * 写屏序号（issue #8 的「曲目在状态转移之后设置」佐证）：主循环每写一次屏
     * 加一，锚点行里报出来，抓取脚本据此与 `State: … -> idle` 行比对先后。
     */
    unsigned music_screen_seq_ = 0;
    void LaunchPendingMusic();
    void UpdatePauseAutoResume();
    static void MusicStartTaskEntry(void* arg);
    void StopNotification();
    void HandleNotificationFinished(uint32_t playback_id, bool success);

    // Activation task (runs in background)
    void ActivationTask();

    // Helper methods
    void CheckAssetsVersion();
    void CheckNewVersion();
    void InitializeProtocol();
    void ShowActivationCode(const std::string& code, const std::string& message);
    void SetListeningMode(ListeningMode mode);
    ListeningMode GetDefaultListeningMode() const;

    // TTS pre-buffering helpers (tts_buffer_mutex_ guarded)
    void ResetTtsBuffer();  // Drop buffered audio and disable buffering
    void FlushTtsBuffer();  // Disable buffering and enqueue held audio for playback
    
    // State change handler called by state machine
    void OnStateChanged(DeviceState old_state, DeviceState new_state);
};


class TaskPriorityReset {
public:
    TaskPriorityReset(BaseType_t priority) {
        original_priority_ = uxTaskPriorityGet(NULL);
        vTaskPrioritySet(NULL, priority);
    }
    ~TaskPriorityReset() {
        vTaskPrioritySet(NULL, original_priority_);
    }

private:
    BaseType_t original_priority_;
};

#endif // _APPLICATION_H_
