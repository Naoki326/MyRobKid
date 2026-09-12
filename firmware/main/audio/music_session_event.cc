#include "audio/music_session_event.h"

#include <cstdio>
#include <cstdint>

namespace {

/*
 * 最小 JSON 字符串转义。曲目名来自内容元数据（播放地址），可能带 `"`、`\`
 * 或控制字符——不转义就编出非法 JSON，服务端解析整条失败、状态静默丢掉。
 * 只处理 JSON 必需的这几个；其余按原样（UTF-8 字节本就合法，中文曲目不受影响）。
 */
std::string EscapeJson(const std::string& value) {
    std::string out;
    out.reserve(value.size() + 2);
    for (const char ch : value) {
        switch (ch) {
            case '"':
                out += "\\\"";
                break;
            case '\\':
                out += "\\\\";
                break;
            case '\n':
                out += "\\n";
                break;
            case '\r':
                out += "\\r";
                break;
            case '\t':
                out += "\\t";
                break;
            default:
                if (static_cast<unsigned char>(ch) < 0x20) {
                    char buf[8];
                    snprintf(buf, sizeof(buf), "\\u%04x",
                             static_cast<unsigned char>(ch));
                    out += buf;
                } else {
                    out += ch;
                }
                break;
        }
    }
    return out;
}

}  // namespace

std::string BuildMusicSessionNotification(const MusicSessionEventFacts& facts) {
    std::string params = "\"params\":{";
    params += "\"event\":\"" + EscapeJson(facts.event) + "\"";
    params += ",\"state\":\"" + EscapeJson(facts.state) + "\"";
    params += ",\"title\":\"" + EscapeJson(facts.title) + "\"";
    params += ",\"author\":\"" + EscapeJson(facts.author) + "\"";
    params += ",\"form\":\"";
    params += facts.live ? "live" : "finite";
    params += "\"";
    // 硬约束 2：直播流不带位点与总量。位点未知（have_position=false）也不写
    // ——「不知道放到哪」不是「放到 0:00」。
    if (!facts.live) {
        if (facts.have_position) {
            char buf[32];
            snprintf(buf, sizeof(buf), "%.1f", facts.position_s);
            params += ",\"position_s\":";
            params += buf;
        }
        if (facts.duration_s > 0) {
            params += ",\"duration_s\":";
            params += std::to_string(facts.duration_s);
        }
    }
    params += "}";

    std::string payload = "{\"jsonrpc\":\"2.0\",\"method\":\"music.session\",";
    payload += params;
    payload += "}";
    return payload;
}
