#ifndef MUSIC_URL_H
#define MUSIC_URL_H

#include <string>

/*
 * play_url 的内容属性（issue #2）。
 *
 * play_url 保持纯内容语义：服务端把「这份内容经代理可取」的属性
 * （源地址、来源页、标题、作者、时长、内容形态）编进 URL 查询串。
 * 起点是播放会话状态、由设备持有——不编进 play_url，起流时以 ss= 追加。
 * 内容形态遵循 CONTEXT.md：finite（有限内容，有时长可定位）/
 * live（直播流，无时长、定位无意义）。
 */
struct MusicContentMeta {
    std::string title;    // 标题（percent 解码后的 UTF-8）；缺失为空
    std::string author;   // 作者/来源；缺失为空
    int duration_s = 0;   // 有限内容的总时长（秒）；缺失或直播流为 0
    bool live = false;    // 内容形态：true = 直播流（电台）
};

// 从播放地址的查询串解析内容属性（title/author/duration/form）。
// 只认已知键，其余（src/referer/ss…）忽略；URL 无查询串时返回空元数据。
MusicContentMeta ParseMusicContentMeta(const std::string& url);

// 把起点（秒，整数）以 ss= 参数追加到播放地址，供转码代理输入定位。
// start <= 0 时原样返回——不带起点的点播地址与改动前逐字节一致；
// 直播流请传 0（定位无意义，由调用方先判断 MusicContentMeta::live）。
std::string AppendMusicStart(const std::string& url, int start_seconds);

#endif  // MUSIC_URL_H
