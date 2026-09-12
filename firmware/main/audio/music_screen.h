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
 * 扩展位（issue #11）：暂停态呈现要加 state（两种暂停可区分）与 position_s，
 * 在这里加字段、在 BuildMusicNowPlaying 里加分支即可——本模块不持有状态、
 * 不做判断，只是「字段 → 一行文本」的映射，扩展不会波及调用点。
 */
struct MusicScreenFacts {
    std::string title;   // 曲目；缺失为空
    std::string author;  // 作者/来源；缺失为空
    int duration_s = 0;  // 总量（秒）；<=0 或直播流视为未知
    bool live = false;   // 内容形态：true = 直播流（没有终点，不显示总量）
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
 * 拼出播放中的消息区文本：`<prefix>曲目 · 作者 · 总量`。
 * Lang 的字符串自带前缀与它的分隔（zh 是「正在播放：」，en 是
 * 「Now playing: 」），所以 prefix 原样前置、不补空格。
 *
 * 缺哪块就不出哪块：作者空了不留悬空的「 · 」，曲目空了作者顶到前面（作者
 * 单独出现也好过空一行）。四个字段全空时返回的**只有 prefix**——调用方据
 * 「返回值是否等于 prefix」判空，别把空白当曲目写到屏幕上。
 */
std::string BuildMusicNowPlaying(const MusicScreenFacts& facts,
                                 const std::string& prefix);

#endif  // MUSIC_SCREEN_H
