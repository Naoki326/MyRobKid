#ifndef MUSIC_ENDING_H
#define MUSIC_ENDING_H

/*
 * 一次音乐会话的收场分类（issue #7）。
 *
 * 为什么单独一个文件：设备分不出「放完了」和「断了」——两种都是音频流结束，用户
 * 那边却是一个想再点一首、一个想检查网络。收场原因是**纯逻辑**（五个事实位 →
 * 一个结局 → 一个提示音），所以它放在这里，用宿主 C++ 编译器就能测（见
 * firmware/scripts/tests/test_music_ending.py），不必上真机听。
 *
 * 这里只负责「怎么算」和「该出声吗」；Lang 字符串与屏幕输出归 application.cc
 * ——它们依赖设备资源，硬拉进来就没法宿主编译了。
 */

enum class MusicEnding {
    kCompleted,     // 自然播完：出过声，且缓冲确实播空
    kInterrupted,   // 链路中断：出过声，但流没播空就结束了（读/解码失败）
    kResumeFailed,  // 续播失败：出过声，续播重连也没接上
    kStopped,       // 用户主动停止：按钮/语音/停止指令（不是故障）
    kReplaced,      // 旧会话被新播放地址替换（收场归新会话，这里不出声）
    kStartFailed,   // 从未出声：起流失败/超时/空洞的流
};

enum class MusicCue {
    kNone,     // 安静：用户自己停的，或者根本没听见声音
    kSuccess,  // 放完了（success.ogg）
    kWarning,  // 出故障了（exclamation.ogg）
};

// 会话结束时能观察到的事实。由播放器在 worker 退出前填好——那时会话状态还未
// 被下一次 Start() 覆盖。
struct MusicEndingFacts {
    bool played = false;             // 本次会话至少解出过一帧（用户真可能听见了）
    bool drained = false;            // 缓冲播空（EOF 且队列排干）
    bool cancelled = false;          // 被要求停止（原因见 CancelCause 注释）
    bool replaced = false;           // 停止原因是被新的播放地址替换
    bool attempted_restart = false;  // 走过一次续播重连
};

// 事实位 → 结局。优先级：replaced > start_failed > completed > stopped >
// resume_failed > interrupted。
//   - replaced 最高：旧会话的收场由新会话负责，报什么都不该打扰用户；
//   - 没出过声就没有「中断/停止/续播失败」可言，一律 start_failed；
//   - drained 压过 cancelled：播空与按键的赛跑里，真放完了就别说断了；
//   - cancelled 压过 attempted_restart：用户按停后恰好又重连失败，那是用户停的。
MusicEnding DeriveMusicEnding(const MusicEndingFacts& facts);

// 遥测契约（`Music ended: reason=…` / `Music feedback: reason=…`）。
// 名字两两不同、非空、无空格；改动等于改遥测格式。
const char* MusicEndingName(MusicEnding ending);

// 该不该出声、出哪个声。
MusicCue MusicEndingCue(MusicEnding ending);

// 把位点写进 `pos=` 字段。三种取值，缺一不可：
//   live=true            → "live"（直播流没有「位点」这个概念）
//   have_position=false  → "none"（有限内容但位点真不知道；写 0.0 就是撒谎
//                           —— 会被读成「刚开始放」）
//   其余                  → 一位小数秒数
//
// 为什么收在这里：这段形状原本在三个地方各写一遍——播放器的收场锚点、应用
// 层的跳过分支与反馈锚点。三处必须同口径（串口断言拿它们互相比对），而其中
// 一处改成整秒就会让断言悄悄失去判别力。收进这个纯逻辑头文件后，宿主编译的
// 测试能顺带钉住格式，不必上真机。
//
// 参数用 unsigned 而非 size_t：本文件刻意不引任何头文件（见文件头注释），
// 而 size_t 需要 <cstddef>。调用处 snprintf 接受隐式转换，不损失什么。
//
// 注意与 pipe: 周期行的区别：那种 2 秒一拍的遥测用整秒（不伪造精度），
// 收场/pause/resume/session 这类锚点行才用一位小数（spec 的 ±0.5s 验收缝要得）。
void WritePositionField(double position_s, bool live, bool have_position,
                        char* out, unsigned out_size);

// true = 真故障（落日志时用 ESP_LOGE）。用户主动停止与换歌为 false。
bool MusicEndingIsFailure(MusicEnding ending);

#endif  // MUSIC_ENDING_H
