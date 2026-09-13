#include "speaking_watchdog.h"

SpeakingWatchdog::SpeakingWatchdog(int threshold)
    : threshold_(threshold > 0 ? threshold : 1) {}

SpeakingWatchdogAction SpeakingWatchdog::Tick(bool speaking, bool playback_drained) {
    if (!speaking || !playback_drained) {
        ticks_ = 0;
        return SpeakingWatchdogAction::kNone;
    }
    if (++ticks_ < threshold_) {
        return SpeakingWatchdogAction::kNone;
    }
    // 到点：清零后再报，保证只触发一次（见头文件）。
    ticks_ = 0;
    return SpeakingWatchdogAction::kDrainedUnstopped;
}

void SpeakingWatchdog::Reset() {
    ticks_ = 0;
}
