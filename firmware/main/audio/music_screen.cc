#include "music_screen.h"

#include <cstdio>

namespace {
/* 字段之间的分隔：曲目 · 作者 · 总量。中圆点两侧各一个半角空格。 */
constexpr const char* kSeparator = " · ";
}  // namespace

std::string FormatMusicClock(int seconds) {
    // 只有负数是坏数据（给空串让路）；0 就是 0:00——它是合法时长，只是
    // 「未知」由 MusicScreenShowsTotal 挡在调用点，不该在这里混为一谈。
    if (seconds < 0) {
        return std::string();
    }
    char buf[32];
    if (seconds >= 3600) {
        snprintf(buf, sizeof(buf), "%d:%02d:%02d", seconds / 3600,
                 (seconds % 3600) / 60, seconds % 60);
    } else {
        snprintf(buf, sizeof(buf), "%d:%02d", seconds / 60, seconds % 60);
    }
    return std::string(buf);
}

std::string BuildMusicNowPlaying(const MusicScreenFacts& facts,
                                 const std::string& prefix) {
    // 逐块拼、只在块与块之间补分隔符：这样作者缺失时不会留下一个孤零零的
    // 「 · 」（那看起来像「还在加载」）。prefix 不算一块——它是 Lang 自带的
    // 标签，与正文之间没有分隔符（zh 的「：」就在它里面）。
    std::string body;
    auto append = [&body](const std::string& part) {
        if (part.empty()) {
            return;
        }
        if (!body.empty()) {
            body += kSeparator;
        }
        body += part;
    };
    append(facts.title);
    append(facts.author);
    // 位点与总量互斥，且**按态定**：暂停只报「放到哪」（位点未知就什么都不
    // 报，不退回总量——那是播放态的数字，混进来会让两态看着一样）；播放只报
    // 「还有多久」。直播流两个 Shows* 各自把 live 挡掉，一次都不出。
    if (facts.state == MusicScreenState::kPlaying) {
        if (MusicScreenShowsTotal(facts)) {
            append(FormatMusicClock(facts.duration_s));
        }
    } else if (MusicScreenShowsPosition(facts)) {
        append(FormatMusicClock(facts.position_s));
    }
    return prefix + body;
}

const char* MusicScreenStateName(MusicScreenState state) {
    switch (state) {
        case MusicScreenState::kPausedConversation:
            return "paused_conversation";
        case MusicScreenState::kPausedUser:
            return "paused_user";
        case MusicScreenState::kPlaying:
            break;
    }
    return "playing";
}
