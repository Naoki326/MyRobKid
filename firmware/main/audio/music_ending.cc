#include "audio/music_ending.h"

#include <cstdio>

MusicEnding DeriveMusicEnding(const MusicEndingFacts& facts) {
    if (facts.replaced) return MusicEnding::kReplaced;
    if (!facts.played) return MusicEnding::kStartFailed;
    if (facts.drained) return MusicEnding::kCompleted;
    if (facts.cancelled) return MusicEnding::kStopped;
    if (facts.attempted_restart) return MusicEnding::kResumeFailed;
    return MusicEnding::kInterrupted;
}

namespace {

/*
 * 一个结局的四件属性（名字 / 提示音 / 屏幕 / 是否真故障）写在同一行。
 *
 * 为什么合成一张表：这四件事是同一份知识的四个面——「放完了」既是遥测里的
 * `completed`、又是 success.ogg、又是屏幕上的「播放结束」、又不算故障。此前拆成
 * 几个 switch 时，加一个新结局要改多处、漏一处编译器不吭声（switch 都没有
 * default，全靠枚举值覆盖；漏掉的新值会静默落进函数尾的 fallback，其中一处还是
 * "start_failed" 这个名字——报错都报到别的结局上）。一张表里，漏了一行就是
 * 漏了整个结局，测试的「名字两两不同」当场抓住。
 */
struct EndingTraits {
    MusicEnding ending;
    const char* name;
    MusicCue cue;
    MusicEndingScreen screen;
    bool failure;
};

constexpr EndingTraits kEndingTraits[] = {
    // 名字是遥测契约（`Music ended: reason=…`）；改动等于改遥测格式。
    // 屏幕文案是第二个分辨轴：告警音只能告诉用户「出事了」，由屏幕分出是哪一种。
    {MusicEnding::kCompleted, "completed", MusicCue::kSuccess,
     MusicEndingScreen::kEnded, false},
    {MusicEnding::kInterrupted, "interrupted", MusicCue::kWarning,
     MusicEndingScreen::kInterrupted, true},
    {MusicEnding::kResumeFailed, "resume_failed", MusicCue::kWarning,
     MusicEndingScreen::kResumeFailed, true},
    // 用户自己按停的：不出声，但屏幕上要报「已停止」（没有新会话接手屏幕）。
    {MusicEnding::kStopped, "stopped", MusicCue::kNone,
     MusicEndingScreen::kStopped, false},
    // 换歌：旧会话的收场归新会话，不出声也不写屏。
    {MusicEnding::kReplaced, "replaced", MusicCue::kNone,
     MusicEndingScreen::kNone, false},
    // 起流失败：起播那一刻已经报过错了，别再叠一声故障音或一句屏。
    {MusicEnding::kStartFailed, "start_failed", MusicCue::kNone,
     MusicEndingScreen::kNone, true},
};

const EndingTraits* TraitsFor(MusicEnding ending) {
    for (const EndingTraits& traits : kEndingTraits) {
        if (traits.ending == ending) return &traits;
    }
    return nullptr;
}

}  // namespace

const char* MusicEndingName(MusicEnding ending) {
    const EndingTraits* traits = TraitsFor(ending);
    return traits != nullptr ? traits->name : "unknown";
}

MusicCue MusicEndingCue(MusicEnding ending) {
    const EndingTraits* traits = TraitsFor(ending);
    return traits != nullptr ? traits->cue : MusicCue::kNone;
}

MusicEndingScreen MusicEndingScreenOf(MusicEnding ending) {
    const EndingTraits* traits = TraitsFor(ending);
    return traits != nullptr ? traits->screen : MusicEndingScreen::kNone;
}

namespace {

/*
 * 屏幕文案值 → 字面量，下标即枚举值。一张表而不是 switch：值是遥测契约
 * （串口断言拿它分组比对「两种告警收场的屏幕文案不得相同」），加一个漏一个
 * 必须编译器说了算。
 */
constexpr const char* kScreenNames[] = {
    "none",           // kNone
    "ended",          // kEnded
    "interrupted",    // kInterrupted
    "resume_failed",  // kResumeFailed
    "stopped",        // kStopped
};

constexpr unsigned kScreenNameCount =
    sizeof(kScreenNames) / sizeof(kScreenNames[0]);

}  // namespace

const char* MusicEndingScreenName(MusicEndingScreen screen) {
    const unsigned index = static_cast<unsigned>(screen);
    return index < kScreenNameCount ? kScreenNames[index] : "unknown";
}

bool MusicEndingIsFailure(MusicEnding ending) {
    const EndingTraits* traits = TraitsFor(ending);
    return traits != nullptr && traits->failure;
}

void WritePositionField(double position_s, bool live, bool have_position,
                        char* out, unsigned out_size) {
    if (live) {
        snprintf(out, out_size, "live");
    } else if (!have_position) {
        snprintf(out, out_size, "none");
    } else {
        snprintf(out, out_size, "%.1fs", position_s);
    }
}
