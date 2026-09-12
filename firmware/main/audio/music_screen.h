#ifndef MUSIC_SCREEN_H
#define MUSIC_SCREEN_H

#include <string>

/*
 * 音乐会话的消息区文案（issue #8）。
 *
 * 为什么单独一个文件：音乐在设备的空闲态下播放（ADR-0008 的音乐模式），而
 * 空闲态把消息区清空、状态写成「待机」——于是放着歌、屏幕写着待机，用户既
 * 看不到曲目，也判断不出是不是卡住了。文案本身是**纯逻辑**（几个字段 → 一行
 * 文本），所以它放在这里，用宿主 C++ 编译器就能测（见
 * firmware/scripts/tests/test_music_screen.py），不必上真机看屏幕。
 *
 * 这里只负责「拼成什么」；Lang 前缀、遥测锚点与写屏的时机归 application.cc
 * ——它们依赖设备资源，硬拉进来就没法宿主编译了。
 */

/*
 * 一次音乐会话里「跟文案有关」的字段。刻意不从 MusicPlaybackStatus 取：
 * 那个结构在 music_player.h 里，而那份头文件引了 FreeRTOS / AudioService
 * （宿主编译不了）。调用方在播放器快照上取这四个字段填进来即可。
 *
 * 扩展位（issue #11，已落地）：暂停态呈现的 state（两种暂停可区分）与
 * position_s 就加在下面的 MusicScreenFacts 里，分支加在 BuildMusicNowPlaying
 * ——本模块不持有状态、不做判断，只是「字段 → 一行文本」的映射。实测这次扩展
 * 确实没波及调用点：所有权机制、写屏 helper 与四个 action 都没动（只多了个
 * `paused` action 标签）。
 */

/*
 * 屏幕该按哪一态呈现（issue #11）。与播放器的四态快照同口径，但**只取与
 * 文案有关的三态**：空闲没有会话可写（那是清屏，不是这里的事），所以在
 * 这里刻意不设 kIdle——让「空闲」无法被传进来，就不必在拼装里再防一次。
 *
 *   kPlaying           播放中：曲目 · 作者 · 总量，**不显示位点**（总量才是
 *                      这一态关心的数字；位点每 2 秒在动，写上去只会闪）。
 *   kPausedConversation 会话性暂停（因说话而让位，答完自动续）——用户该**等**。
 *   kPausedUser        用户暂停（明确说「暂停」，必须说「继续」）——用户该**说继续**。
 *
 * 后两态在文案上的差别只有前缀（Lang 提供），正文相同（曲目 · 作者 · 位点）；
 * 这个枚举是 BuildMusicNowPlaying 决定「显示不显示位点」的依据，也是调用方
 * 挑前缀的依据——一个枚举撑起两件事，避免调用方与模块各判一次。
 */
enum class MusicScreenState { kPlaying, kPausedConversation, kPausedUser };

struct MusicScreenFacts {
    std::string title;   // 曲目；缺失为空
    std::string author;  // 作者/来源；缺失为空
    int duration_s = 0;  // 总量（秒）；<=0 或直播流视为未知
    bool live = false;   // 内容形态：true = 直播流（没有终点，不显示总量）
    // 呈现态（issue #11）：暂停态显示暂停前缀与已播位点，播放态不显示位点。
    MusicScreenState state = MusicScreenState::kPlaying;
    // 已播位点（秒，绝对值）。只有暂停态会用它；负数视为未知（不显示）。
    // 直播流的位点无意义——MusicScreenShowsPosition 会先挡掉。
    int position_s = 0;
};

/*
 * 总量文案的格式（`4:29` / `1:01:01`）。seconds <= 0 返回空串——那是「不知道
 * 放多久」，不是「0 秒」：屏幕上写 0:00 会被读成「这就完了」。
 *
 * 口径与遥测里的位点字段不同（那是 `269.0s`）：这里面向人眼，那里面向脚本。
 * 唯一相同的判断是直播流——`FormatMusicClock` 不管形态（那是调用方的事），
 * 所以直播流绝不要拿它的结果去拼总量，用 MusicScreenShowsTotal 先问。
 */
std::string FormatMusicClock(int seconds);

/*
 * 该不该显示总量：有限内容且时长已知才显示。直播流一律不显示——即便上游
 * 没把时长剥干净（那是个不该信的数字），内容形态才是唯一依据。
 */
inline bool MusicScreenShowsTotal(const MusicScreenFacts& facts) {
    return !facts.live && facts.duration_s > 0;
}

/*
 * 该不该显示已播位点（issue #11）：只有**暂停**态才显示。
 *
 * 为什么播放中不显示：位点每 2 秒推一拍，写上去就是一行一直在变的数字，
 * 而用户在播放中关心的是「还有多久」（总量），不是「已经放了多久」。暂停
 * 了才反过来——时间停住了，「放到哪」成为唯一能回答「卡住还是暂停」的数字。
 *
 * 直播流一律不显示：它没有位点可言（位点的「恢复」是重连而非定位），显示
 * 一个数字就是撒谎——与 MusicScreenShowsTotal 同一条理由。
 */
inline bool MusicScreenShowsPosition(const MusicScreenFacts& facts) {
    return facts.state != MusicScreenState::kPlaying && !facts.live &&
           facts.position_s >= 0;
}

/*
 * 呈现态 → 遥测锚点里的 `state=` 串（`Music screen:` 行）。
 *
 * 为什么收在这里（而不是散在 application.cc 的 switch 里）：与
 * `MusicEndingName()` 同一先例——它们是**遥测契约**，改动等于改遥测格式，
 * 而串口断言拿这个串分组比对「两种暂停文本不得相同」。收进这个宿主编译可测
 * 的纯逻辑头文件后，改错/漏一个分支会当场被测试拉住，不必上真机看屏幕。
 *
 * 名字与设备侧 `MusicPlaybackStatus::state_name()` 一字不差（同一对术语，
 * CONTEXT.md 的「会话性暂停 / 用户暂停」），也对应服务端 STATE_* 的两态。
 * 注意本枚举**没有 kIdle**：空闲是清屏，不该拿一个文案态去表达。
 */
const char* MusicScreenStateName(MusicScreenState state);

/*
 * 拼出消息区文本：`<prefix>曲目 · 作者 · [位点 | 总量]`。
 * Lang 的字符串自带前缀与它的分隔（zh 是「正在播放：」与「已暂停（…）：」，
 * en 是「Now playing: 」与「Paused (…): 」），所以 prefix 原样前置、不补空格。
 *
 * **前缀由调用方按 facts.state 挑**（三种态三个 Lang key）：前缀是语言资源，
 * 本模块不引 Lang；而「显示不显示位点」是拼装的结构，判断在这里（见
 * MusicScreenShowsPosition）。一个枚举撑起这两件事，两边不会各判一次。
 *
 * 暂停态显示位点、不显示总量；播放态显示总量、不显示位点。缺哪块就不出哪块：
 * 作者空了不留悬空的「 · 」，曲目空了作者顶到前面（作者单独出现也好过空一
 * 行）。所有正文块都空时返回的**只有 prefix**——调用方据「返回值是否等于
 * prefix」判空，别把空白当曲目写到屏幕上。
 */
std::string BuildMusicNowPlaying(const MusicScreenFacts& facts,
                                 const std::string& prefix);

#endif  // MUSIC_SCREEN_H
