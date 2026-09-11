# 音乐播放链路修复与设备资源分时复用

QQ 音乐/播客播放从未真正成功过（旧固件 `Play()` 不验证即返回 true 的假成功掩盖了这一点）。本轮从服务端到固件全线修复，并确立"设备资源分时复用"策略：ESP32-S3 双核同时跑不醒着听+看着+说着+播着，必须按场景切换。

## Context（取证链）

2026-08-28 排查，串口日志（USB Serial/JTAG）是决定性证据源：

1. **播放从未成功**：上游与 2.4.7 重写版都缺少 `esp_audio_dec_register_default()`，`AUD_SDEC: Fail to open decoder MP3 ret -7`——MP3 解码器从未注册，任何 URL 都报 "URL unreachable or not a decodable audio stream"。8 月 26 日"成功"是假象（工具返回 true 但无声）。
2. **播客双杀**：wavpub/喜马拉雅链接是 302 跳转，固件手写 HttpClient（78__esp-ml307）无重定向跟随；且设备外网连通性从未被验证（其余流量全走局域网 IP）。服务端把 play_url 统一收口到局域网转码代理（nginx 8080 → ffmpeg 8777）后一并解决。
3. **"越播越卡"三层叠加**（管线遥测 `pipe:` 日志定位）：
   - 缓冲全在内部 RAM，free sram 跌破 17KB → WiFi 断连（2.4.10 起 PSRAM 化解决）；
   - 唤醒词 AFE（prio 8）占满 CPU0，`tcp_receive`（prio 1）抢不到 CPU，下载吞吐 7KB/s < 消耗 8KB/s（2.4.12 音乐期间停唤醒词解决）；
   - `vTaskDelay(pdMS_TO_TICKS(5))` 在 100Hz tick 下取整为 0 不阻塞，主循环每秒 2.8 万次空转重拷贝，PSRAM 带宽被吃光、I2S 饿死（2.4.15 改 `vTaskDelay(1)` 解决，`fail` 从 55593/2s 降到 ~116/2s）。
4. **in_buf 无界预取**：4KB 读粒度 vs ~200B/帧消费粒度失配，压缩缓冲膨胀至 946KB，头部 erase 变 O(n) PSRAM memmove 风暴（2.4.16 首修把上限挡在填环入口，误挡解码致僵死；2.4.17 改为**只挡读取、不挡解码**）。

## Consequences

- **服务端（plugins/music-mcp，与旧工程 xiaozhi-music-mcp 保持同步拷贝）**：
  - `_ensure_playable` 一律返回代理 URL（`http://192.168.18.172:8080/music/stream?src=...`），不再按 .mp3 后缀直连——代理负责 302 跟随与格式转码，设备只连局域网 IP；
  - 转码参数 `-ac 1 -ar 24000 -b:a 64k`：匹配设备 codec 输出 24kHz，免设备端 44.1k→24k 软件重采样，解码量减半、网络吞吐减半。
- **固件（2.4.9→2.4.17）**：
  - MusicPlayer worker 打开解码器前 `std::call_once` 注册 `esp_audio_dec_register_default()` + `esp_audio_simple_dec_register_default()`（simple_dec 默认只注册 WAV/M4A/TS/OGG，MP3 必须显式注册）；
  - ring/in_buf/mono/converted 走 PSRAM allocator，出队单帧拷贝进播放队列；`kMaxCompressedBuffer=64KB` 只挡 Read；
  - 行为：对话中收到 play_music 先登记 pending，TTS 播完再起流（"说完话再播"）；音乐期间设备回 idle 不聆听、停唤醒词检测（AFE 让位网络栈）、摄像头按需采集（`fb_count=1 + GRAB_WHEN_EMPTY`）；音乐结束或被按钮/新对话打断后恢复唤醒词与聆听；打断说话会丢弃 pending 防陈旧 URL 意外起播；
  - 管线遥测每 2s 打 `pipe: ring=/in_buf=/read=/pushed=/fail=`，卡顿定位从"猜"变"看"；
  - 顺手修：`lcd_display.cc` SetTheme 对 `low_battery_popup_` 判空（启动竞态 NULL 解引用曾致 2.4.10 OTA 回滚）。
- **方法论**：设备黑盒问题先建可观测性（遥测/串口）再动手；逐项排除法走了 6 版，遥测一加 2 版收敛。OTA 双槽回滚在 2.4.10 崩溃时正确兜底。
- **遗留**：音乐期间喊唤醒词无响应（AFE 已停，按按钮可打断）——产品取舍已接受；`tcp_receive` prio 1 与 HttpClient 8KB 反压是上游组件短板，若音乐仍偶发卡顿，下一步提升组件内接收任务优先级或改消费偏移消除头部搬移。
- **2026-09-11 地址漂移（不属于本 ADR 的管线问题）**：play_url 里的主机写死了旧 IP，Mac 漂到新 IP 后设备连不上，而 play_music 假成功不报错——症状是「搜完歌、说开始播放，机器人就不动了」。音乐无声**先跑 `tools/music_url_check.py` 排除地址**（判据：地址能取到音频字节），再查本 ADR 的管线。地址纪律见 ADR-0002「地址改用 mDNS 名」。
