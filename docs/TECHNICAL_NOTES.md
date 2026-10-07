# 低延迟 WebRTC 摄像头传输系统：项目技术文档

> 用途：保存项目上下文，方便本人和其他 AI 后续继续工作。  
> 状态约定：明确区分“已验证成功”“初步验证”“尚未完成”。

## 0. 项目现状摘要

长期目标：理解并复现 Phantom Bridge 类系统的核心视频链路，最终形成低延迟、高质量、高帧率、浏览器可访问、可跨公网、可量化测量、适合工业 PC 长期运行的远程摄像头系统。

当前核心链路：

```text
USB Camera → V4L2 → GStreamer → H.264 → RTP → WebRTC → Chrome
```

已实际跑通：
- `/dev/video0` 摄像头可用。
- 1920×1080 / 30 FPS H.264 能力已确认。
- MJPEG → jpegdec → videoconvert → x264enc → h264parse → rtph264pay → WebRTC 已跑通。
- Windows Chrome 可稳定显示 1920×1080、约 30 FPS。
- 基线链路手工端到端延迟进入 `<200 ms` 量级。
- Direct Native H.264 → RTP → WebRTC 初步跑通。
- 延迟测量已经从人工观察/错误 telemetry 关联推进到 **RTP timestamp 同帧关联**。

当前最重要的未完成事项：
1. ~~实际运行验证 RTP timestamp 映射稳定性。~~ ✅ 2026-10-07 已完成，见 §25。
2. 对比 x264 与 Native H.264 的 CPU、码率、延迟、稳定性。
3. 完成 P50/P95/P99/Max 统计。
4. 做 chrony/NTP 跨机器时钟同步。
5. 从局域网推进到公网。
6. 研究 NAT、STUN、TURN。
7. 工业 PC 长时间稳定运行。

---

# 1. 项目目标

目标不是简单“网页看到摄像头”，而是理解完整链路：

```text
摄像头 → V4L2 → GStreamer → H.264 → RTP → WebRTC → 浏览器
```

最终场景：

```text
秦皇岛：工业 PC + 摄像头
        ↓ Internet
北京 / 石家庄：Chrome
```

核心指标：
- 低端到端延迟
- 稳定高帧率
- 高图像质量
- 可控 CPU / 带宽
- 公网可达
- 可重复测量
- 长时间稳定

# 2. 实验环境

## Linux
- Ubuntu 22.04.5 LTS
- VMware
- Python 3.10.12
- GStreamer 1.20.3
- `python3-gst-1.0` 1.20.1
- FFmpeg 4.4.2
- `v4l2-ctl` 1.22.1
- kernel 6.8.0-138-generic

## Windows
- Windows 11
- Chrome 为主要验证浏览器

## Camera
- `/dev/video0`
- driver：`uvcvideo`
- 型号信息：`Integrated_Webcam_HD`

# 3. 最初的 WSL / 摄像头问题

最初环境：

```text
Windows 11 → WSL 2 → Ubuntu → Docker / ROS 2 → USB Camera
```

曾测试 1280×720 MJPEG、标称约 25 FPS，但实际只有约：

```text
4.9 FPS
```

`--stream-mmap` 与 `--stream-user` 都出现类似低帧率，因此不能简单归因于 mmap。

后来在合适参数下测试 640×480 MJPEG，可达到约 30 FPS。

结论：WSL 摄像头路径存在性能变量，因此后来迁移到 Ubuntu 22.04 VMware 建立可控 WebRTC 实验环境。

# 4. V4L2 摄像头能力验证

已确认：
- `/dev/video0` 可用；
- `uvcvideo` 正常；
- 支持 Video Capture / Streaming；
- V4L2 mmap 流式读取可用；
- 摄像头支持 1920×1080 H.264；
- H.264 可达到约 30 FPS；
- MJPEG 在合适模式下也可达到约 30 FPS。

原则：先证明输入端能力，再调 WebRTC。

# 5. 第一条完整 WebRTC baseline

核心管线：

```text
v4l2src device=/dev/video0 io-mode=mmap
! image/jpeg,width=1920,height=1080,framerate=30/1
! jpegdec
! videoconvert
! x264enc tune=zerolatency speed-preset=ultrafast bitrate=4000 key-int-max=30
! h264parse
! rtph264pay config-interval=1 pt=96
! application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000
! queue name=rtp_queue
```

这是当前 baseline。

# 6. Baseline 实测结果

已验证：
- 1920×1080
- 约 30 FPS
- Windows Chrome 稳定播放
- 手工 E2E 延迟 `<200 ms` 量级
- Sender CPU 曾观察到约 `57.7%`
- 静态场景码率可低于 1 Mbps
- 运动场景约 3–4 Mbps

注意：这些延迟数字属于实验性/手工结果，不是最终严格的跨公网统计。

# 7. Native H.264

摄像头可直接输出：

```text
1920×1080 H.264 @ 30 FPS
```

测试：

```text
v4l2src
! video/x-h264,width=1920,height=1080,framerate=30/1
! h264parse
! mp4mux
! filesink
```

成功生成 Native H.264 MP4。

曾测得约 `192 kb/s` 的当前摄像头输出码率。

重要：192 kb/s 不能直接与 x264 的 4000 kbps 比较画质，因为编码参数不同。

# 8. Native H.264 → RTP

测试：

```text
v4l2src device=/dev/video0 io-mode=mmap
! video/x-h264,width=1920,height=1080,framerate=30/1
! h264parse
! rtph264pay config-interval=1 pt=96
! fakesink sync=false
```

成功运行，无明显管线错误。

RTP caps 已验证为 H.264、90 kHz clock、payload 96 等正常形式。

结论：摄像头本身可以产生可直接进入 RTP/WebRTC 的 H.264。

# 9. Direct H.264 → WebRTC

链路：

```text
Camera Native H.264
→ h264parse
→ rtph264pay
→ WebRTC
→ Chrome
```

初步验证成功：
- Windows Chrome 正常显示；
- 1920×1080；
- 约 30 FPS；
- 手工观察约 130–180 ms；
- 通常低于 200 ms；
- 静态码率可低于 1 Mbps；
- 运动约 3–4 Mbps。

仍有偶发单帧模糊/损坏，因此尚不能宣布 Native H.264 已经全面优于 x264。

# 10. WebRTC 信令

当前使用简单 WebSocket signaling server：

```text
Sender → ws://127.0.0.1:8765
Browser → ws://192.168.106.128:8765
```

主要交换：
- SDP offer / answer
- ICE candidate
- 实验阶段的 telemetry / timestamp mapping

WebSocket 不是视频媒体通道；视频媒体走 WebRTC RTP/SRTP。

# 11. GStreamer / WebRTC 学习成果

已经建立的核心概念：
- Element：功能模块，如 `v4l2src`、`jpegdec`、`x264enc`、`rtph264pay`、`webrtcbin`
- Caps：描述上下游数据格式和能力
- Pad：Element 输入/输出接口
- Buffer：承载视频数据和 PTS 等时间信息
- Queue：隔离处理阶段并影响缓存/延迟
- Pipeline State：`NULL → READY → PAUSED → PLAYING`
- WebRTC：SDP / ICE / RTP / DTLS / SRTP / PeerConnection
- Python GI：通过 `Gst` 等 API 控制 GStreamer

# 12. 延迟测量方案演进

## 第一阶段：画面时间戳

曾使用 `textoverlay` + Python pad probe 在画面上显示：

```text
Frame ID
YYYY-MM-DD HH:MM:SS.mmm
```

验证成功，但不是最终方案。

问题：
- OCR 不适合高精度自动化；
- 修改视频本身；
- 不适合最终统计。

## 第二阶段：Frame ID + WebSocket telemetry

Sender 周期发送 Frame ID / 时间，Browser 用 `requestVideoFrameCallback()`。

根本问题：

> Sender telemetry 对应一个具体帧，但 rVFC 回调出来的帧没有证明就是这个 Frame ID。

因此可能得到数百毫秒、几秒甚至约 9 秒的错误值。

结论：**不能使用“最新 telemetry + 当前 rVFC”作为同帧延迟。**

## 第三阶段：浏览器 captureTime

尝试使用 rVFC metadata：

```text
captureTime
receiveTime
expectedDisplayTime
processingDuration
rtpTimestamp
```

当前 Chrome 路径可用：
- `receiveTime`
- `rtpTimestamp`
- `expectedDisplayTime`
- `processingDuration`

但没有得到可用的 `captureTime`。

所以不能单靠浏览器 metadata 算 Source → Browser。

# 13. 当前最终方案：RTP timestamp 同帧关联

核心思想：

> **RTP timestamp 是 Sender 与 Browser 两端识别同一视频帧的共同身份。**

架构：

```text
Camera
 ↓
Source timestamp
 ↓
GStreamer
 ↓
H.264
 ↓
RTP timestamp
 ├──────────────→ WebRTC → Chrome rVFC
 │                           ↓
 │                    metadata.rtpTimestamp
 │
 └→ WebSocket mapping
             ↓
      Browser 查表匹配
```

Sender 记录：
- frame_id
- capture_time_ns
- capture_time_ms
- capture_timestamp
- monotonic_ns
- pts_ns
- pts_ms

然后解析实际 RTP 包头中的 timestamp。

RTP 固定头：

```text
bytes 4..7 = RTP timestamp
```

建立：

```text
RTP timestamp → source timestamp
```

Browser 从 rVFC 获得同一个：

```text
metadata.rtpTimestamp
```

然后查表。

# 14. 当前浏览器端可计算的指标

### Source → Receive

```text
browser receive time - sender source time
```

### Source → Callback

```text
browser callback time - sender source time
```

### Source → Expected Display

```text
browser expected display time - sender source time
```

### Receive → Callback

```text
browser callback time - browser receive time
```

### Decode processing

使用：

```text
processingDuration
```

最终应统计：

```text
P50
P90
P95
P99
Max
```

# 15. 当前方案的重要边界

当前 Source timestamp 是：

> 视频 buffer 进入 GStreamer probe 时的时间。

它不是：

```text
sensor exposure timestamp
```

因此更严谨地称为：

```text
GStreamer source-buffer timestamp
```

如果未来要求真正的“传感器曝光 → 显示器显示”，需要额外硬件/触发/光电传感器等方法。

# 16. 跨机器时钟同步

RTP timestamp 同帧关联解决：

> 两端是不是同一帧？

但秦皇岛 Sender 与北京/石家庄 Browser 是不同机器，需要同步 wall clock。

计划使用：

```text
chrony / NTP
```

检查：

```bash
chronyc tracking
chronyc sources -v
chronyc sourcestats -v
```

之后再做跨机器 Source → Display 延迟测试。

# 17. 当前重要文件

项目目录：

```text
~/webrtc-camera/
├── webrtc_sender.py
├── webrtc_sender_direct.py
├── webrtc_sender_rtp_latency.py
├── signaling_server.py
├── index.html
├── gst_api_test.py
├── webrtc_latency_test.py
├── webrtc_latency_test_jpeg.py
├── latency_overlay_test.py
├── test_dynamic_overlay.py
├── webrtc_sender_jpeg_latency.py
└── webrtc_sender_Now_latency_probe.py
```

当前最新延迟实验版本：

```text
webrtc_sender_rtp_latency.py
index_latency_rtp_fixed.html
```

# 18. 当前实现的已知注意事项

## 18.1 Source PTS 与 RTP queue sink PTS

当前实现假设：

```text
source buffer PTS
↕
rtp_queue sink buffer PTS
```

可以稳定关联。

必须实际运行验证。

如果不稳定，保持总体方案不变，仍使用：

```text
RTP timestamp = frame identity
```

只调整 Sender 内部建立映射的位置，例如考虑在 `rtph264pay` sink 处建立 H.264 buffer 与 source 时间的关联。

## 18.2 一个 H.264 frame 可能对应多个 RTP packet

多个 RTP packet 可以拥有相同 RTP timestamp，这是正常的。

只需对每个新的 RTP timestamp 建立一次映射。

## 18.3 RTP timestamp 回绕

RTP timestamp 为 32 bit。

90 kHz 下：

```text
2^32 / 90000 ≈ 13.26 小时
```

会回绕。

短时间实验没问题，长期运行需要考虑 extended timestamp / 更稳健的帧身份。

## 18.4 当前 set 实现

曾使用：

```python
self.rtp_timestamps_sent = set(
    list(self.rtp_timestamps_sent)[-120:]
)
```

`set` 无序，不适合长期保存“最近 N 个 timestamp”。

后续应改成 `deque` 或 `OrderedDict`。

## 18.5 WebSocket mapping

目前 WebSocket mapping 是临时实验方案。

长期可以考虑：

```text
WebRTC DataChannel
```

但不要把 WebSocket mapping 的传输时间错误地算成视频媒体延迟。

# 19. 当前总体架构

```text
Camera
  ↓
V4L2
  ↓
GStreamer
  ├─ MJPEG → jpegdec → videoconvert → x264
  │
  └─ Native H.264
            ↓
        h264parse
            ↓
       rtph264pay
            ↓
          RTP
            ↓
        WebRTC
            ↓
          Chrome
```

测量链路：

```text
GStreamer source time
        ↓
RTP timestamp
        ↓
WebSocket mapping
        ↓
Browser rVFC RTP timestamp
        ↓
same-frame matching
        ↓
latency statistics
```

# 20. 工作状态

## 已验证成功

- [x] `/dev/video0`
- [x] uvcvideo
- [x] V4L2 mmap
- [x] 1920×1080 H.264 @ 30 FPS
- [x] MJPEG → JPEG decode → x264
- [x] H.264 → RTP
- [x] RTP → WebRTC
- [x] WebSocket signaling
- [x] Windows Chrome 1080p / ≈30 FPS
- [x] Baseline <200 ms 量级手工测试
- [x] Native H.264 → MP4
- [x] Native H.264 → RTP
- [x] Direct Native H.264 → WebRTC 初步跑通
- [x] 动态 timestamp overlay
- [x] rVFC RTP timestamp 获取
- [x] RTP timestamp → source 帧映射稳定（见 §25）
- [x] 真实窗口 1920×1080 稳定 30 FPS
- [x] 同帧端到端延迟 117–148 ms，Match 100%（见 §25）

## 初步验证 / 仍需测试

- [~] Direct H.264 长时间稳定性
- [x] RTP timestamp → source 帧映射（已验证，见 §25）
- [x] RTP timestamp 同帧 latency（已验证，见 §25）
- [~] P50/P95/P99/Max（P50/P95/Max 已实时显示；P99 未做）
- [~] 跨机器 Source → Display
- [~] Native H.264 与 x264 全面对比

## 尚未完成

- [ ] chrony/NTP 跨机器测试
- [ ] Internet 公网 WebRTC
- [ ] NAT traversal
- [ ] STUN
- [ ] TURN
- [ ] 工业 PC 长时间运行
- [ ] 断线重连
- [ ] 网络抖动/丢包测试
- [ ] 自适应码率
- [ ] 最终部署架构
- [ ] 与 Phantom Bridge 完整结构对照

# 21. 下一次继续工作的入口

> **2026-10-07 更新**：RTP timestamp 同帧映射**已验证稳定**（见 §25），
> 不要再从「运行 Sender + Chrome，先看 Match/Miss」开始。
> 当前真正的下一步是**跨机器时钟同步**（§25.5）。
> 下面这一节保留为历史记录。

不要重新从摄像头/GStreamer 基础开始。

直接从：

```text
webrtc_sender_rtp_latency.py
+
index_latency_rtp_fixed.html
```

开始。

第一步：运行 Sender + Chrome。

第二步：先看：

```text
RTP timestamp
Match / Miss
Metadata
```

不要一开始就相信 latency 数字。

第三步：验证：

```text
Browser rVFC RTP timestamp
        ↓
Sender RTP mapping
        ↓
稳定匹配
```

如果 Match 很低，优先检查：

```text
source buffer PTS
vs
rtp_queue sink buffer PTS
```

第四步：匹配稳定后，再统计：

```text
Source → Receive
Source → Callback
Source → Expected Display
Receive → Callback
Processing
P50 / P95 / P99 / Max
```

第五步：加入 chrony/NTP，再做跨机器测试。

# 22. 推荐技术路线

```text
单机 WebRTC
    ↓
RTP timestamp 同帧测量
    ↓
跨机器时钟同步
    ↓
局域网远程
    ↓
公网 WebRTC
    ↓
STUN / NAT traversal
    ↓
TURN fallback
    ↓
丢包 / 抖动 / 带宽测试
    ↓
码率 / GOP / 编码优化
    ↓
工业 PC 长时间运行
    ↓
与 Phantom Bridge 完整架构对照
```

# 23. 给后续 AI 的接手说明

1. 不要重新设计已经确定的 RTP timestamp 同帧关联思路。
2. 当前最大不确定性是 RTP timestamp 映射是否稳定，以及 Source PTS 是否正确对应 RTP 输出。
3. Chrome 是当前主要验证浏览器；Linux Edge / Firefox 不是主线。
4. WSL 低帧率是早期环境问题，不要与当前 VMware WebRTC baseline 混为一谈。
5. Native H.264 已初步跑通，但有偶发单帧模糊/损坏，不能直接宣布其全面优于 x264。
6. `<200 ms` 与 `130–180 ms` 是实验性/手工结果，不是最终严格公网统计。
7. Source timestamp 是 GStreamer buffer 时间，不是 sensor exposure timestamp。
8. WebSocket mapping 是临时方案，长期可以考虑 DataChannel。
9. 下一次工作最优先是运行最新 RTP timestamp 版本并检查 Match/Miss，而不是继续修改 UI。

# 24. 一句话总结

> **项目已经从“摄像头能不能传到浏览器”，推进到“能解释每一层、验证每一层，并开始建立同帧、跨机器、可统计的端到端延迟测量体系”。**

---

# 25. 2026-10-07 验证结果与两个根因

## 25.1 实测结论（真实 Chrome 窗口，非无头）

| 指标 | 实测值 |
|---|---|
| 分辨率 / 浏览器帧率 | 1920×1080 / 稳定 **29.6–32.8 fps** |
| sender 侧采集帧率 | 29.94–30.67 fps |
| 端到端同帧延迟 | **117.9–147.9 ms**（Source → Callback） |
| P50 / P95 / Max | 约 130 / 138 / 139 ms（典型 1 秒窗） |
| Source → Receive | 110.4–126.3 ms |
| Source → Expected display | 124.0–154.0 ms |
| Receive → Callback | 1.1–26.5 ms |
| Match / Miss | **1073 / 0**；连续 7 分钟长跑为 **11176 / 0** |
| 码率 | 2.60–5.12 Mbps |
| 浏览器端异常 | 0 |
| `processingDuration` | 恒为 0.0 ms —— Chrome 对本链路不提供，**该项不可用** |

验证方式：无头/真实 Chrome 均通过 CDP 读取页面面板（`tools/cdp_probe.mjs`），
不依赖人工读数。

## 25.2 根因一：x264enc 会重写 buffer PTS

`source probe` 看到的是运行时间基准（例如 `263644238`），而 `rtph264pay`
一侧看到的是被**整体平移一个常数**（约 3600 秒）之后的值
（例如 `3600000403991139`）。原实现用 PTS 相等查表，两侧属于不同域，
**100% 查不到** —— 实测 202/202 全部 miss，日志狂刷 `No source timestamp`。

修复：不再依赖 PTS 相等，改为 source 帧进入 FIFO，**按到达顺序**与编码帧
配对；从首帧测得偏移常数并逐帧重锚。这样上游丢帧只会被跳过，而不会让
之后的所有测量整体错位一帧。

诊断手段：`tools/pts_diag.py`（在链路每一级挂 probe 打印 PTS）、
`tools/sender_patch_probe.py`（不改原文件给回调插桩，可捕获被 PyGObject
吞进 stderr 的异常）。

## 25.3 根因二：浏览器端 `now` 未定义

`processVideoFrameObservation()` 内部引用了外层 rVFC 回调的参数 `now`，
但它**没有被作为参数传入** → 第一个带 `rtpTimestamp` 的帧就抛
`ReferenceError`；异常逃出 rVFC 回调，导致它无法重新注册自己，
**测量循环永久死亡**，页面上只剩 FPS 数字还在动。

这解释了 §12 第三阶段「拿到了 rtpTimestamp 却算不出延迟」的现象：
不是拿不到数据，是拿到后立刻崩了。

## 25.4 其他修正

- `rtp_queue` 改为有界（`max-size-buffers=32`，其余上限关闭）。默认上限
  在**没有消费者**时约 0.7 秒就把整条管线反压堵死（实测 12 秒只跑 15 帧）。
  这曾导致「摄像头只有 25 fps」的**误判** —— 有正常消费者时是满 30 fps。
- pending 帧一直没等到 mapping 而被淘汰时计为 miss。否则映射整体失效
  也会显示成健康状态。

## 25.5 仍然待办

1. **VM ↔ 主机时钟偏差尚未精确测定。** 同帧延迟是 sender 的 epoch 减
   浏览器的 epoch，偏差会**整体平移**所有延迟数字。在测出来之前，
   117–148 ms 应读作「相对可信、绝对值待校」。
2. `processingDuration` 恒为 0，解码耗时这一项拿不到。
3. P99 未统计（P50/P95/Max 已实时显示）。
4. §16 的 chrony/NTP 跨机器同步，以及公网、NAT/STUN/TURN、长时间稳定性
   等仍未开始。

---

# 26. 2026-10-07 时钟偏差：在此之前延迟数字全部是错的

## 26.1 现象

画面路径没有任何改动，页面上显示的端到端延迟却在两次运行之间从
117–148 ms 跳到 216–261 ms。

## 26.2 根因：两个时钟，两个时间源

| 机器 | 时钟由谁校准 | 备注 |
|---|---|---|
| Linux 虚拟机（sender） | `systemd-timesyncd` → `ntp.ubuntu.com`（互联网） | 网络校时，且日志里有超时重试 |
| Windows 主机（browser） | Windows 时间服务 | 另一个完全不同的源 |
| **两者之差** | | **约 −190 ~ −207 ms，并且在漂移（≈10 ppm）** |

`vmware-toolbox-cmd timesync status` = **Disabled**：虚拟机**没有**跟随主机
时钟，两台机器各自向互联网校时。两个源各有几十到上百毫秒误差，而且会各自
跳变，所以这个差值**既不小也不恒定**。

页面计算的是 `浏览器epoch − 虚拟机epoch`，于是**每一个延迟数字都被整体
平移了这整个偏差**。

## 26.3 解决：方案 A —— 在 WebSocket 上做 NTP 式偏差估计

不改任何系统配置，把偏差当作被测系统自己的一部分去估计：

```text
browser -> sender   {"type":"clock_sync","t1":...}
sender  -> browser  {"type":"clock_sync_reply","t1":...,"t2":...,"t3":...}
offset = ((t2 - t1) + (t3 - t4)) / 2        # sender_clock - browser_clock
```

浏览器每秒估计一次，保留最近 20 个样本中**往返最小**的那个（路径不对称最小），
然后把每个 sender 时间戳扣掉 offset，换算到浏览器时钟上**之后**再做减法。

实测吻合度：

| 方法 | 偏差 | RTT |
|---|---|---|
| 浏览器 WebSocket 估计 | −207.4 ms | 5.20 ms |
| 独立 TCP 探针（`tools/timesrv.py`） | −206.3 ms | 1.31 ms |

**两者相差 1.1 ms。** 校正后延迟为 **26–55 ms**。

## 26.4 §16 的计划需要修正

§16 写的是「用 chrony/NTP 做跨机器时钟同步」。但公共 NTP 池给不了亚毫秒
一致性（最好也就 10–50 ms），而要测 50 ms 量级的延迟，一个 ±50 ms 的基准
是不可能的。方案 A 把偏差当作被测系统自己的未知量去估计，天然适应任何
网络，也正是跨公网场景下唯一可行的做法。chrony 仍值得做，但作用是**减小
偏差量级**，而不是**消除**它。

## 26.5 与手机拍照测得的约 150 ms 的关系（重要，别混淆）

用手机拍「真实场景 + 浏览器画面」得到的约 150 ms，与本代码显示的约 50 ms
**并不矛盾 —— 因为起点不同**：

- 本代码的起点是 **GStreamer probe**（`identity`，位于 `jpegdec` 与
  `videoconvert` 之后、`x264enc` 之前），也就是**已解码的帧进入编码器**的时刻。
- 手机拍照的起点是**物理场景的传感器曝光瞬间**。

两者之差约 85–100 ms，正是摄像头自身那条链：

```text
传感器曝光 → 摄像头内部 MJPEG 编码 → USB 传输 → V4L2 缓冲
          → JPEG 解码 → 色彩转换   ← 本代码的起点在这里
```

这正是 §15「Source timestamp 不是 sensor exposure timestamp」所指的边界。
若要让页面数字与手机测量对齐，需要把探针前移到 `v4l2src.src`（可省掉解码
与色彩转换），进一步则要用 UVC 驱动提供的硬件时间戳（§15 提到的方向）。

---

# 27. 2026-10-07 回归与自愈：两个线程写同一个 WebSocket

## 27.1 现象

上线方案 A 后约一小时，页面延迟变成 **−1265.9 ms**，且「半天才跳一次」。

## 27.2 根因一（回归）：两个线程同时 send

- `flush_rtp_mappings()` 在 **GLib 主线程**发送映射；
- 新增的 `clock_sync` 应答在 **WebSocket 线程**里直接 `ws.send()`。

两个线程同时写同一个 WebSocket → 帧结构交叉 → 连接被服务端关闭
（`Connection closed normally`，code 1000）。之后没有任何帧时间戳再送达，
页面就停在被污染的最后一次结果上。

**修复**：应答改由 `GLib.idle_add` 交给 GLib 线程发送，所有发送回到单线程。
这正是代码里 `set_remote_answer` 已经在用的模式。

> **铁律：一个 WebSocket 只能有一个发送线程。**

## 27.3 根因二（设计缺陷）：估计器没有任何绝对校验

连接将死时有一次 `clock_sync` 往返花了 **2490 ms**，据此算出的「偏移」是
−1454 ms —— 页面于是把所有延迟都算成约 −1265 ms。

原实现是「取最近 20 个样本中 RTT 最小的」：它只在样本**之间**比较，
从不与绝对阈值比较，所以一个 2490 ms 的样本只要比同批其他样本小就会被采纳；
而且一旦被采纳就可能一直霸占（更老的样本 RTT 更小就永远赢），
真正的时钟跳变会被无声地无视。

**修复**：

1. **绝对阈值**：RTT > 100 ms 的样本直接丢弃（本机链路实测远小于 1 ms）。
2. **中位数**取代「最小 RTT」：单个坏样本无法移动中位数，真实跳变几个样本内跟上。
3. **时效性**：超过 5 s 没有有效样本即判定 `stale`，**拒绝显示任何延迟数字**。

> **原则：宁可显示「没有测量」，绝不显示「错误的测量」。**

## 27.4 根因三（原有缺陷）：断线后没有任何恢复

信令 WebSocket 是帧时间戳与时钟偏差的**唯一**通道，断了之后双方都无法重新协商。

**修复（自愈）**：

- sender 在 WebSocket 关闭时**退出**；
- `run_sender_loop.sh` 监管并在 2 s 后重启它 —— **每次重启都会发一个新的
  SDP offer**，这正是恢复会话所需的东西；
- 页面在 WebSocket 关闭时，**若曾经建立过会话**（`hadLiveSession`）则 3 s 后
  自动刷新；从未成功过就不刷新，避免 sender 未启动时反复刷新；
- 页面在 WebSocket 打开时主动发 `request_offer`，因为 sender 可能在本页面
  出现之前就发过一次 offer，那次是发到空处的。

## 27.5 故障注入验证

杀掉信令服务器（`fuser -k 8765/tcp`）后**无需任何人工干预**，45 秒内自动恢复：

```text
[SUPERVISOR] signaling_server.py exited (code 137), restarting in 2 s
[SUPERVISOR] webrtc_sender_Now_latency_probe.py exited (code 0), restarting in 2 s
[SIGNALING] WebSocket closed: None None
[SIGNALING] Exiting so the restart loop can re-establish the session
[SIGNALING] WebSocket connected
[SIGNALING] SDP offer sent
```

恢复后：CONNECTED / 1920×1080 / 26–30 fps / 延迟 22–63 ms / Match 100%。

> 注意：重新协商后延迟会短暂偏高（43–63 ms 对比正常 17–31 ms），
> 那是 WebRTC 抖动缓冲重新收敛的过程，几秒后回落。

---

# 28. 2026-10-07 估计器偏差与换对端恢复

§27 的自愈上线后，暴露了两个更深的问题。

## 28.1 中位数是用错的药：单侧延迟会污染偏移估计

实测（同一分钟内，逐样本）：

| 往返 RTT | 估计出的偏移 |
|---|---|
| 6.61 ms | −202.4 ms |
| 10.40 ms | −203.8 ms |
| 48.63 ms | −216.2 ms |
| 61.48 ms | −220.4 ms |

**偏移随 RTT 增大而越来越负。** 若延迟是双向对称的（网络慢），偏移**不会**被带偏；
只有**单侧**延迟才会。这里的单侧延迟来自**浏览器主线程**：它正忙于解码和呈现
1080p 视频，`onmessage` 被推迟 δ，于是 t4 记晚了：

```text
偏移误差 = −δ/2        往返看起来大了 +δ
```

§27 用中位数取代「最小 RTT」是**用错了药**：中位数会保留这种偏差，因为偏差存在于
**大多数**样本里。**NTP 之所以取最小 RTT，正因为那个样本是「本机不忙」的样本，
单侧偏差最小。**

**正确做法**：

1. 回到**最小 RTT**（NTP 的做法）；
2. 但样本窗口**限制在 5 秒内** —— 这才是当年「旧样本长期霸占」问题的真正解法；
3. **跳变检测**：若最新 3 个样本彼此一致（差 < 5 ms）且与当前估计差 > 50 ms，
   判定为时钟跳变，直接采用；
4. **最后一道防线**：延迟不可能为负。任何 < −20 ms 的结果一律显示 `--`，
   并把原始值写进 Metadata 行 —— 无论根因是什么，都绝不显示不可能的数字。

## 28.2 换对端必须换 pipeline，不能重协商

页面重载后，sender 的日志是**完整成功**的：

```text
[WebRTC] Negotiation needed
[WebRTC] SDP offer created
[SIGNALING] SDP offer sent
[WebRTC] Setting remote SDP answer      ← 页面也应答了
→ peer: failed
```

**SDP 成功、ICE 失败。** 原因：`webrtcbin` 里还留着**上一个浏览器实例**的
ICE/DTLS 状态。用同一个元素去和**另一个**对端重协商，SDP 层能过，ICE 永远起不来。

> **规则：换对端 = 换 pipeline。**

于是 `request_offer` 的行为改为：

- 该元素**已经完成过一次协商**（`remote_answer_set`）→ **退出**，由监管循环启一个
  全新 sender（全新 pipeline + 全新 offer）；
- 从未协商过 → 直接重发 offer（省掉一次无谓重启）。

## 28.3 失败的对端连接无法原地重建

`RTCPeerConnection` 一旦进入 `failed` 就不能原地复用；而信令 WebSocket 此时可能
**完全健康**，只等 WebSocket 关闭会让页面永远卡住。

**修复**：对端连接 `failed` 时也触发页面重载，并加**有限重试预算**
（`sessionStorage` 计数，上限 5 次，连接成功后清零），防止 sender 根本起不来时
变成无限刷新循环。

## 28.4 最终状态

```text
CONNECTED / 1920×1080 / 29.5–32.6 fps / 约 4 Mbps
延迟            20.8–32.1 ms
Source→Receive  17.1–27.8 ms
时钟偏移        −201.5 ms（独立 TCP 探针 −205 ms，差 3.5 ms）
时钟 RTT        9.1–12.0 ms
Match / Miss    100% / 0
```
