# 对话期 WiFi 全速、空闲保持省电

机器人在对话期间（PERFORMANCE 档）强制 `esp_wifi_set_ps(WIFI_PS_NONE)` 彻底关闭 WiFi 省电，空闲时（LOW_POWER 档）保持 `WIFI_PS_MIN_MODEM` 省电。实现位于 `firmware/main/boards/common/wifi_board.cc` 的 `WifiBoard::SetPowerSaveLevel`。

## Context

实测：空闲省电（modem sleep）下设备网络 RTT 呈周期性锯齿（2500ms 峰值 → 几毫秒，约秒级周期），而音频流按 60ms/帧匀速推送——每帧都撞上唤醒延迟，听感为"一字一顿"，且该症状与传输丢帧、采样率、服务端节奏均无关（loop 实测 259 帧零丢失仍断续）。上游组件 esp-wifi-connect 的 PERFORMANCE 档仅映射轻度省电（MIN_MODEM），不足以消除锯齿，故在板级直接设置。

## Consequences

- 待机功耗不变（空闲档保留省电，用户明确要求）。
- 若上游组件未来修正其 PERFORMANCE 映射，板级强制设置仍是无害的冗余。
