#ifndef MUSIC_SESSION_EVENT_H
#define MUSIC_SESSION_EVENT_H

#include <string>

/*
 * 音乐会话状态变更的推送载荷（issue #9）。
 *
 * 为什么单独一个文件：这是**线协议**——设备说「现在在放什么」，服务端据此维护
 * 会话并在每次调用模型前注入系统提示。它错了不会崩，只会让机器人答错「这歌谁
 * 唱的」「暂停了吗」；而这类错只在真机上、隔着一次模型调用才看得见。载荷本身是
 * **纯逻辑**（几个字段 → 一段 JSON-RPC 通知），所以它放在这里，用宿主 C++ 编译器
 * 就能测（见 firmware/scripts/tests/test_music_session_event.py），不必起设备、
 * 不必连服务端。
 *
 * 与服务端 `server/core/utils/music_session.py` 的线协议严格对齐::
 *
 *     {"jsonrpc":"2.0","method":"music.session","params":{
 *         "event":"started","state":"playing","title":"晴天","author":"周杰伦",
 *         "form":"finite","position_s":83.4,"duration_s":269}}
 *
 * 三条硬约束（对应 spec 的验收条款）:
 *   1. **无 id** = JSON-RPC 通知，服务端不回响应（与请求/响应消息区分开）；
 *   2. 直播流（live）**不带** position_s / duration_s——位点对它无意义，带上
 *      就是撒谎，服务端也会主动丢（两边都挡才不会有「电台报出一个位点」）；
 *   3. 事件在**真的出声之后**才发（判据是播放器已验证的完成条件，不是工具
 *      返回值）——那是调用方（application.cc）的责任，本模块只管把已成立的
 *      事实编成载荷。
 *
 * 这里只负责「编成什么」；发送时机与通道（Application::SendMcpMessage）归
 * application.cc——它依赖设备资源，硬拉进来就没法宿主编译了。
 */

/*
 * 事件的语义（服务端只把 event 记日志/后续写历史；state 才是权威字段）：
 *   started     开始播放 / 换歌（换歌也是 started——旧会话的收场归新会话）
 *   paused      暂停（两种暂停由 state 区分）
 *   resumed     续播成功
 *   completed   自然播完
 *   interrupted 链路中断
 *   resume_failed 续播失败
 *   stopped     用户主动停止
 *   start_failed 起流就没成功（从未出声）
 *
 * state 的取值（与 Python 侧 STATE_* 一字不差）不在这里用常量声明：它只有
 * 两个生产者——``MusicPlaybackStatus::state_name()``（在播/两种暂停）与
 * ``MusicEndingName()``（收场五态：completed/interrupted/resume_failed/stopped/start_failed），各自已是单一事实源；再存一份常量表
 * 只会多一处需要同步的地方，而它一个字也挡不住写错（那两个函数的测试才是
 * 真的防线）。
 */
namespace music_session_event {
constexpr char kStarted[] = "started";
constexpr char kPaused[] = "paused";
constexpr char kResumed[] = "resumed";
constexpr char kCompleted[] = "completed";
constexpr char kInterrupted[] = "interrupted";
constexpr char kResumeFailed[] = "resume_failed";
constexpr char kStopped[] = "stopped";
constexpr char kStartFailed[] = "start_failed";
}  // namespace music_session_event

/*
 * 一次推送的全部事实。刻意不从 MusicPlaybackStatus 取：那个结构在 music_player.h
 * 里，而那份头文件引了 FreeRTOS / AudioService（宿主编译不了）。调用方在播放器
 * 快照上取字段填进来即可。
 *
 * position_s 用 double：与播放器位点同精度（一位小数，spec 的 ±0.5s 接缝）。
 * have_position 显式区分「位点未知」与「位点就是 0.0」——后者会答出「已播放
 * 0:00」，那是撒谎。duration_s <= 0 视为总量未知（不写进载荷）。
 */
struct MusicSessionEventFacts {
    std::string state;     // 权威字段：在播/两种暂停/收场五态（见上）
    std::string event;     // music_session_event::*
    std::string title;     // 曲目；缺失为空
    std::string author;    // 作者；缺失为空
    bool live = false;     // 内容形态：true = 直播流（不带位点/总量）
    bool have_position = false;
    double position_s = 0.0;
    int duration_s = 0;    // 总时长（秒）；<=0 = 未知
};

/*
 * 编出 JSON-RPC 通知载荷（"params" 值之外还带 jsonrpc / method 外壳，直接交给
 * `Application::SendMcpMessage` 即可——它再套上 {"type":"mcp","payload":…}）。
 *
 * 字段顺序固定（便于串口/日志断言）：jsonrpc, method, params{event, state,
 * title, author, form[, position_s][, duration_s]}。直播流里 position_s 与
 * duration_s **一个都不出现**（硬约束 2）。
 *
 * 字符串做最小 JSON 转义（曲目里可能出现 `"` 或 `\`）：不转义就会编出非法
 * JSON，服务端解析失败、状态整条丢掉——那是「静默失效」，最难查。
 */
std::string BuildMusicSessionNotification(const MusicSessionEventFacts& facts);

#endif  // MUSIC_SESSION_EVENT_H
