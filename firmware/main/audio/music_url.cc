#include "music_url.h"

namespace {

// 查询串的值按 percent 编码解码：%XX 还原字节（UTF-8 中文原样透传）。
// 服务端用 urllib.parse.quote(safe='') 编码：空格编为 %20、'+' 编为
// %2B，从不产生裸 '+'——因此不把 '+' 还原为空格，字面 '+' 才不丢。
int HexValue(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

std::string UrlDecode(const std::string& value) {
    std::string out;
    out.reserve(value.size());
    for (size_t i = 0; i < value.size(); ++i) {
        char c = value[i];
        if (c == '%' && i + 2 < value.size()) {
            int hi = HexValue(value[i + 1]);
            int lo = HexValue(value[i + 2]);
            if (hi >= 0 && lo >= 0) {
                out.push_back(static_cast<char>((hi << 4) | lo));
                i += 2;
                continue;
            }
        }
        out.push_back(c);
    }
    return out;
}

// 时长是纯十进制秒数；带任何非数字字符即视为缺失（0）。
// 上限 8640000（100 天）防溢出，正常内容远达不到。
int ParseDurationSeconds(const std::string& s) {
    if (s.empty()) {
        return 0;
    }
    int value = 0;
    for (char c : s) {
        if (c < '0' || c > '9') {
            return 0;
        }
        value = value * 10 + (c - '0');
        if (value > 8640000) {
            return 0;
        }
    }
    return value;
}

}  // namespace

MusicContentMeta ParseMusicContentMeta(const std::string& url) {
    MusicContentMeta meta;
    size_t query_start = url.find('?');
    if (query_start == std::string::npos) {
        return meta;
    }
    size_t query_end = url.find('#', query_start);
    size_t query_len = (query_end == std::string::npos)
                           ? std::string::npos
                           : query_end - query_start - 1;
    std::string query = url.substr(query_start + 1, query_len);

    size_t pos = 0;
    while (pos < query.size()) {
        size_t amp = query.find('&', pos);
        if (amp == std::string::npos) {
            amp = query.size();
        }
        std::string pair = query.substr(pos, amp - pos);
        pos = amp + 1;
        if (pair.empty()) {
            continue;
        }
        // src 的值整体经 percent 编码，其中的 '&' 已是 %26，按 '&' 切分安全。
        size_t eq = pair.find('=');
        std::string key =
            pair.substr(0, eq == std::string::npos ? std::string::npos : eq);
        std::string value =
            (eq == std::string::npos) ? "" : UrlDecode(pair.substr(eq + 1));
        if (key == "title") {
            meta.title = std::move(value);
        } else if (key == "author") {
            meta.author = std::move(value);
        } else if (key == "duration") {
            meta.duration_s = ParseDurationSeconds(value);
        } else if (key == "form") {
            meta.live = (value == "live");
        }
    }
    return meta;
}

std::string AppendMusicStart(const std::string& url, int start_seconds) {
    if (start_seconds <= 0) {
        return url;
    }
    std::string out(url);
    out += (out.find('?') == std::string::npos) ? '?' : '&';
    out += "ss=";
    out += std::to_string(start_seconds);
    return out;
}
