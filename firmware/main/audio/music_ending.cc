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
 * 一个结局的三件属性（名字 / 提示音 / 是否真故障）写在同一行。
 *
 * 为什么合成一张表：这三件事是同一份知识的三个面——「放完了」既是遥测里的
 * `completed`、又是 success.ogg、又不算故障。此前拆成三个 switch 时，加一个
 * 新结局要改三处、漏一处编译器不吭声（三个 switch 都没有 default，全靠枚举
 * 值覆盖；漏掉的新值会静默落进函数尾的 fallback，其中一处 fallback 还是
 * "start_failed" 这个名字——报错都报到别的结局上）。一张表里，漏了一行就是
 * 漏了整个结局，测试的「名字两两不同」当场抓住。
 */
struct EndingTraits {
    MusicEnding ending;
    const char* name;
    MusicCue cue;
    bool failure;
};

constexpr EndingTraits kEndingTraits[] = {
    // 名字是遥测契约（`Music ended: reason=…`）；改动等于改遥测格式。
    {MusicEnding::kCompleted, "completed", MusicCue::kSuccess, false},
    {MusicEnding::kInterrupted, "interrupted", MusicCue::kWarning, true},
    {MusicEnding::kResumeFailed, "resume_failed", MusicCue::kWarning, true},
    // 用户自己按停的：不出声、不算故障。
    {MusicEnding::kStopped, "stopped", MusicCue::kNone, false},
    // 换歌：旧会话的收场归新会话，这里不出声。
    {MusicEnding::kReplaced, "replaced", MusicCue::kNone, false},
    // 起流失败：起播那一刻已经报过错了，别再叠一声故障音。
    {MusicEnding::kStartFailed, "start_failed", MusicCue::kNone, true},
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

bool MusicEndingIsFailure(MusicEnding ending) {
    const EndingTraits* traits = TraitsFor(ending);
    return traits != nullptr && traits->failure;
}

void WritePositionField(double position_s, bool live, char* out, unsigned out_size) {
    if (live) {
        snprintf(out, out_size, "live");
    } else {
        snprintf(out, out_size, "%.1fs", position_s);
    }
}
