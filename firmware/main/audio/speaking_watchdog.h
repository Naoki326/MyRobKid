#ifndef SPEAKING_WATCHDOG_H
#define SPEAKING_WATCHDOG_H

/*
 * `speaking` 态的兜底看门狗（issue #33）。
 *
 * 为什么需要它：设备进入 `speaking` 后**只在收到服务端的 `tts stop` 时才退出**。
 * 那条消息丢了（网络写阻塞/丢包/服务端卡顿）设备就停在 `speaking`——不再聆听、
 * 唤醒词只认 `idle`，用户侧就是「它说完了但没反应」。实测有一轮卡了 47 秒才
 * 自恢复。无论 tts stop 迟到的真凶是什么（#32 仍在追），一个「等对端发结束
 * 信号才能退出」的状态在对端不发时就会停住，所以这里加一道**兜底**（**不是**
 * #32 的修复）。
 *
 * 判据是**「放完了却还没人喊停」，不是「speaking 持续太久」**。这个区别是
 * 本模块存在的全部理由：正常一轮 TTS 3–20 秒，长回答（讲故事）实测连续 20+ 秒
 * 的 TTS，用「时长」当判据必然打断正常长文。改用「TTS 音频已全放完」后，只要还
 * 在出声就永不计数——长回答天然豁免，只有真放空了却仍停在 speaking 才触发。
 *
 * “放完”由调用方算好传入（application.cc 的 TtsPlaybackDrained）：播放队列
 * 已空 **且** 没有还没推下去的 TTS 音频。为什么是两半、各半在什么模式下才重要、
 * 以及整段预缓冲模式下什么样的缺口**不被**覆盖——那三件事的正文在
 * application.h 的 kSpeakingWatchdogTicks 与 TtsPlaybackDrained 注释里，
 * 此处不复制（复制就会各自漂移）。
 *
 * 为什么单独一个文件：判据是**纯逻辑**（两个事实位 → 一个动作），错了就是两种
 * 真机故障（判早了打断长回答、判晚了永久沉默）。放在这里用宿主 C++ 编译器就能
 * 测（见 firmware/scripts/tests/test_speaking_watchdog.py），不必上真机。
 *
 * 这里只管「数到第几拍、该不该触发」；真正的状态转移、日志与阈值常量归
 * application.cc——它们依赖设备资源，硬拉进来就没法宿主编译了。
 */

enum class SpeakingWatchdogAction {
    kNone,             // 继续等
    kDrainedUnstopped, // 已连续 threshold 拍「放完了却没人喊停」：现在该退出 speaking
};

class SpeakingWatchdog {
public:
    /*
     * threshold 是「连续多少拍满足条件才触发」，单位是**调用方的拍**
     * （本票里是 1Hz 的 CLOCK_TICK，所以等于秒）。必须为正；非正会被钳成 1
     * ——否则在 threshold=0 时首个满足条件的拍即触发，失去「连续」的意义。
     */
    explicit SpeakingWatchdog(int threshold);

    /*
     * 消费一拍。两个事实由调用方现取（不缓存副本，免得与事实分叉）：
     *   speaking         —— 设备当前是否在 kDeviceStateSpeaking
     *   playback_drained —— TTS 音频是否已全放完（播放队列空且无待推缓冲）
     *
     * 只有两者**同时**为真才计数；任一为假即清零（含「已退出 speaking」）
     * ——句间间隙与状态转移都不会累积成误触。
     *
     * 数满 threshold 拍的那一次返回 kDrainedUnstopped 并把计数清零，所以触发
     * **恰好一次**：之后要重新数满才会再触发（不会每拍刷一条日志）。调用方据此
     * 只做一次状态转移即可。
     */
    SpeakingWatchdogAction Tick(bool speaking, bool playback_drained);

    /*
     * 显式归零。正常退出路径（真收到 tts stop、或看门狗自己触发后收口）都要调，
     * 否则下一轮进入 speaking 时会带着上一轮残留的拍数——`Tick` 只在「事实不成立」
     * 时清零，而 `ResolveSpeakingExit()` 走完可能从未消费一拍。
     */
    void Reset();

    // 已连续满足条件的拍数（遥测/测试用）。
    int ElapsedTicks() const { return ticks_; }

private:
    int threshold_;
    int ticks_ = 0;
};

#endif  // SPEAKING_WATCHDOG_H
