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

> ⚠️ **这一节是当时的快照，已经过时。** 它列出的 `index.html`、
> `webrtc_sender_rtp_latency.py`、`index_latency_rtp_fixed.html` 现在都不存在。
>
> **当前的目录布局和入口见 §36**，或者直接看 `run_sender_loop.sh` 与
> `tools/restart_stack.sh` 实际调用了谁。
>
> 保留这一节是为了能看清演进过程 —— 但不要拿它当"现在有什么"的参考。

项目目录（**当时的**布局）：

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

---

# 29. 2026-10-09 渐进式劣化：无界日志饿死了主线程

## 29.1 现象

长测开始约半小时后，三个症状**同时**出现：

1. 延迟面板隔一会就停止更新；
2. `Source → Receive` 频繁出现负值，偶尔 `Receive → Callback` 也负；
3. FPS 从 30 慢慢降到几帧甚至 0.1 帧，**但浏览器画面肉眼看着一直是流畅的 30 帧**。

## 29.2 证据

同时刻采集：

| 时刻 | 日志长度 | rVFC FPS | 时钟往返 | 偏移误差 |
|---|---|---|---|---|
| T−1min | 179 880 字符 | 11.4 | 34 ms / 24 rejected | ~12 ms |
| T | **221 352 字符** | **7.4** | **72 ms / 40 rejected** | **25 ms** |

独立 TCP 探针测得真实偏移 **−41.0 ms**，页面估计 **−66.0 ms**。

rVFC 探针实测：回调比帧提交晚 **20–50 ms**，且存在 **200–300 ms 的空档**。

## 29.3 根因：`textContent +=` 的 O(n) 累积

```javascript
logElement.textContent += `[${timestamp}] ${message}\n`;  // 每次都拷贝整个字符串
logElement.scrollTop = logElement.scrollHeight;           // 每次都强制同步布局
```

映射批量消息**每秒 20 条**，日志只增不减。日志每增长一分，每条消息的成本就高一分 ——
**这是「渐进式劣化」的典型特征**。

## 29.4 传导链（解释了全部三个症状）

```text
日志无界增长
   ↓ 主线程被占满
   ├─→ requestVideoFrameCallback 被饿死
   │      → FPS 显示降低、延迟面板停止更新
   │      （画面本身由解码/合成线程负责，所以看起来正常）
   │
   └─→ ws.onmessage 被推迟 δ
          → t4 记晚 → 往返虚高 +δ → 偏移被带偏 −δ/2
          → 真实延迟只有 20–40 ms，偏 25 ms 后就会变负
```

> **`Source → Receive` 最先变负**：它的真实值最小（约 15–25 ms），偏移一偏就被压到零以下。

## 29.5 修复

1. **日志有界**（最多 150 行），滚动更新移到 `requestAnimationFrame`（每帧最多一次，
   而不是每条消息一次）；
2. **映射批量消息只计数不逐条记日志**，每秒汇报一次；
3. **估计器窗口放宽**（12 个样本 / 15 秒 / 上限 250 ms）—— 主线程忙时也能在窗口内找到
   一个「本机空闲」的样本，而最小 RTT 取样正是挑这个样本；
4. **新增 `Decoded frames/s`** 行（来自 `getStats().framesDecoded`）：它不依赖页面合成，
   所以「流是好的、只是页面呈现得少」能被直接看出来，而不是让人困惑；
5. sender 的映射刷新率从 20 Hz 降到 10 Hz，每消息开销减半，而 100 ms 的延迟远小于
   浏览器 120 帧（约 4 秒）的待配对窗口。

## 29.6 效果

| 指标 | 修复前 | 修复后 | 独立真值 |
|---|---|---|---|
| 日志长度 | 221 352 字符 | **4 459 字符（有界）** | — |
| rVFC FPS | 7.4 | **28.5** | ~30 |
| Decoded FPS | — | **29.0** | ~30 |
| 时钟往返 | 72 ms / 40 rejected | **3.76 ms** | — |
| 时钟偏移 | −66.0 ms | **−42.9 ms** | **−43.1 ms** |
| **偏移误差** | **25 ms** | **0.2 ms** | — |
| `Source → Callback` | 出现负值 | **24.5 ms** | — |

> **教训：一个「只增不减」的显示元素，在 20 Hz 的更新下会成为系统性的性能杀手，
> 而且危害是渐进式的 —— 前几分钟看不出来，之后越来越糟。**

---

# 30. 2026-10-09 一小时长测基线（60 分钟 / 719 次采样）

## 30.1 结论

**测量链路的可信度在这一轮得到确认**，已固化为新基线。

| 指标 | min | **P50** | P95 | **P99** | max | mean |
|---|---|---|---|---|---|---|
| Source → Callback | 20.9 | **46.0** | 59.1 | **63.9** | 69.7 | 45.8 |
| Source → Receive | 12.8 | 18.7 | 26.3 | 34.6 | 59.0 | 19.5 |
| Receive → Callback | 1.2 | 26.6 | 40.7 | 47.5 | 54.9 | 26.3 |
| Source → Expected display | 27.0 | 51.0 | 63.0 | 69.3 | 73.3 | 50.6 |

（单位 ms；n = 694 个有效样本）

| 稳定性项目 | 结果 |
|---|---|
| 负值延迟 | **0 / 694** |
| Match / Miss | **24 039 / 0 = 100.00%** |
| 浏览器异常 | 0 |
| 采集器读取失败 | 0 |
| 呈现帧率 (rVFC) | P50 29.8 / P95 30.8 / max 30.9 |
| 解码帧率 (getStats) | 30.0 |
| 码率 | P50 3.99 Mbps |
| 连接状态 | 691/719 CONNECTED，中途**自动恢复 2 次** |
| 超过 30 秒的断档 | **0** |
| 日志长度 | 7 343 字符 / 150 行（有界，见 §29） |

## 30.2 测量区段内部构成（重要修正）

安静状态下曾测得 `Source→Receive 23.4 ms / Receive→Callback 1.1 ms`，
一度得出「测量段本身已经很快」的印象。**一小时长测修正了这个判断**：

```text
Source → Callback  = 46.0 ms (P50)
  ├─ Source → Receive    = 18.7 ms (41%)   sender 编码打包 + 网络 + WebRTC 抖动缓冲
  └─ Receive → Callback  = 26.6 ms (58%)   Chrome 解码 + 合成器调度
```

**浏览器侧反而是测量区段里更大的一半。**

相关性分析（用于排除「页面被饿死」这一解释）：

| 分组 | 样本数 | 延迟 P50 | Receive→Callback P50 |
|---|---|---|---|
| 呈现帧率 ≥ 25 | 692 | 46.2 ms | 26.8 ms |
| 呈现帧率 < 25 | 2 | 27.0 ms | 3.6 ms |

方向与直觉相反：**帧率低时延迟反而更低**。26.6 ms ≈ 1.6 个显示周期（60 Hz），
是 30 fps 稳定运行下 Chrome 解码 + 合成流水线的正常深度 ——
**这是真实延迟，不是测量误差**。

## 30.3 时钟漂移：19.6 ppm，一小时 70 ms

```text
起始偏移 -44.2 ms  →  结束偏移 -114.9 ms
漂移 -70.7 ms / 60 min = -19.6 ppm（单调漂移，不是跳变）
```

**被测延迟 P50 只有 46 ms，而时钟一小时自己漂了 70 ms。**
即：若没有方案 A 的实时校正，跑一小时之后延迟数字会偏 70 ms ——
**偏差比被测对象本身还大**。方案 A 不是优化项，而是长时间测量的**前提条件**。

## 30.4 附带发现

- 码率 `max 385 Mbps` 是**采样假象**：重连后 `getStats` 的字节计数器归零，
  那一拍的增量算出了荒谬值。不是真实码率。
- 页面有 3 个 `backdrop-filter: blur(4px)` 角标压在 1080p 视频上。
  `backdrop-filter` 会让合成器每帧做一次背景模糊，是已知的性能杀手，
  **可能是那 26.6 ms 的一部分来源**（待验证的假设）。
- 60 分钟内自动恢复 2 次，无人工干预，无超过 30 秒的断档 —— §27/§28 的自愈机制有效。

## 30.5 复现方式

```bash
# 采集（Windows 侧，Chrome 需带 --remote-debugging-port=9222）
node tools/long_test.mjs 3600000 5000 measurements/soak.jsonl

# 分析
python tools/analyze_long_test.py measurements/soak.jsonl
```

原始数据：`measurements/2026-10-09-soak-60min.jsonl`
分析输出：`measurements/2026-10-09-soak-60min.txt`

---

# 31. 2026-10-10 逐级实测：每一级到底花多少毫秒

## 31.1 方法

`tools/stage_timing.py` 在链路每一级挂 pad probe，记录 `time.monotonic_ns()`，
并**按 buffer PTS 配对同一帧**（PTS 在 `x264enc` 之前是保留的，是精确的帧身份）。
全部在虚拟机内完成，**不涉及任何跨机器时钟**，所以得到的是纯粹的本机耗时。

## 31.2 实测结果（满载，与真实 sender 同管线）

| 环节 | 耗时 (P50) |
|---|---|
| ⑤ `jpegdec` | 7.72 ms |
| ⑥ `videoconvert` → BGR | 0.04 ms |
| **⑧ BGR → I420** | **8.33 ms** |
| ⑨ `x264enc` | 6.43 ms |
| ⑩ `h264parse` + `rtph264pay` | 0.11 ms |
| **合计（v4l2src → RTP 队列）** | **22.67 ms** |

空载对照（`v4l2src ! jpegdec ! fakesink`）：`jpegdec` 只要 **5.67 ms**。
所以此前「JPEG 解码 10–25 ms」的估算是偏高的，实测便宜得多。

## 31.3 一个测量方法的更正（重要）

最初用一个公式测「V4L2 队列等待」：

```text
running_now - buffer.pts
    running_now = clock.get_time() - pipeline.get_base_time()
```

**这个测量是无效的。** 对照实验：把 `do-timestamp` 从 `false` 改成 `true`
（PTS 被覆盖成出队时刻），结果几乎完全不变（P50 12.34 → 12.32 ms）。
如果它真是驱动时间戳与出队之间的间隔，覆盖后就应该是 0。
所以那约 12 ms 来自 GStreamer 自己的 base_time / 延迟约定，
**不是驱动队列等待**。

有意义的只是**尾部随负载增长**：空载 max 22.85 ms → 满载 max 224.54 ms，
说明负载下确实存在真实的排队尖峰。

另一个教训：**按缓冲区序号配对，在元素内部有缓存时会错位**。
满载时 `v4l2src out` 有 229 个、`jpegdec out` 有 228 个，
差 1 个就是 33 ms 误差。改用 PTS 配对后结果才稳定。

## 31.4 发现并修掉：BGR 往返白花 8.8 ms

原管线：

```text
jpegdec ! videoconvert ! video/x-raw,format=BGR ! identity
         ! videoconvert ! video/x-raw,format=I420 ! x264enc
         └─ I420→BGR（一次全屏色彩转换）        └─ BGR→I420（又转回去）
```

**`jpegdec` 输出本来就是 I420，`x264enc` 要的也是 I420**，中间却转成 BGR 再转回来。

**为什么会有 BGR？** 叠加层时代的遗留：最早的管线用 `textoverlay` 在画面上画
时间戳，并配 Python pad probe + OpenCV，而 **OpenCV 需要 BGR**。
现在探针只读 `buffer.pts` 和墙钟，**对像素格式没有任何要求**。

修复后的实测对比：

| 环节 | 有 BGR | 无 BGR |
|---|---|---|
| `jpegdec` | 7.72 | 5.93 ms |
| `videoconvert`→BGR | 0.04 | — |
| **BGR→I420** | **8.33** | **—** |
| `identity`→`x264enc` | 6.43 | 5.95 ms |
| `x264enc`→`rtph264pay` | 0.11 | 0.12 ms |
| **探针 → RTP 队列** | **14.89** | **6.09 ms** |
| **v4l2src → RTP 队列** | **22.67** | **12.07 ms** |

系统级验证（4 分钟采样，与 60 分钟基线对比）：

| 指标 | 基线（有 BGR） | 去掉 BGR |
|---|---|---|
| **Source → Receive (P50)** | **18.7 ms** | **11.7 ms** ← **−7.0 ms** |
| Source → Callback (P50) | 46.0 ms | 44.4 ms |
| Source → Callback (P99) | 63.9 ms | 53.6 ms |
| Receive → Callback (P50) | 26.6 ms | 32.1 ms（浏览器侧，本轮偏高） |
| Match / Miss | 24039 / 0 | 7058 / 0 |

**sender 侧如期缩短 7.0 ms**；总额变化不大，是因为浏览器侧那一轮偏高
（26.6 → 32.1 ms），而它本身的波动就大。

## 31.5 仍然测不到的部分

`v4l2src` 之前的一切（①曝光 ②摄像头内编码 ③USB 传输 ④驱动缓冲）
**在原理上测不到**：软件的第一个观测点在数据**已经到达之后**，
摄像头内部的时刻不存在于虚拟机的任何数据里。

- ④ 曾试图用 buffer 时间戳间接测，**已证伪**（见 §31.3）
- ①–③ 的总和只能用「差值法」界定：手机拍照约 150 ms − 本代码实测约 45 ms
  → 约 100 ms，误差 ±10–20 ms
- 要真正测 ③，需要 USB 抓包（`usbmon` / USBPcap）；①② 需要硬件触发

## 31.6 下一步的可操作项

1. ✅ BGR 往返已去掉（本次）
2. 空载 vs 满载的 `jpegdec` 差异（5.67 → 7.72 ms）说明有轻微排队；
   进一步降低 CPU 占用（原生 H.264）应能让尾部尖峰（max 224 ms）收敛
3. 浏览器侧 `Receive → Callback` 26–32 ms 是当前测量区段里最大的一块，
   且波动最大 —— 页面上 3 个 `backdrop-filter: blur(4px)` 角标是待验证的嫌疑

---

# 32. 2026-10-10 配置层抽象 + 公网可行性实测

## 32.1 为什么必须先把配置抽出来

在这之前，部署相关的值散落在 **6 个文件**里：sender 的信令地址、摄像头设备、
采集格式，信令服务器的绑定端口，页面的信令端口与 ICE 服务器，以及三个工具的
调试端口。移植一次要逐个找，**漏掉一个不会报错，只会表现为奇怪的行为**。

而公网阶段还会再引入一批新配置（TURN 地址、TURN 凭据、域名证书）。
**越晚抽，要改的地方越多，而且很容易把 TURN 密码误提交进 git。**

## 32.2 配置层设计

**单一真值源：根目录 `config.py`。** 优先级：

```
1. 环境变量 DSH_*        <- 部署时用，不改代码
2. config.py 里的字面值
3. 内置默认值
```

```bash
python3 config.py                                    # 打印当前生效配置
DSH_VIDEO_DEVICE=/dev/video2 ./run_sender_loop.sh    # 单次覆盖
```

**浏览器页面无法读 Python**，所以页面需要的两项（信令端口、ICE 服务器）由
`tools/make_page_config.py` 写进 `config.js`：

```bash
python3 tools/make_page_config.py           # 生成
python3 tools/make_page_config.py --check   # 校验是否过期（可放 CI）
```

`--check` 存在的意义：防止"改了 config.py 却忘了重新生成 config.js"——
那会表现为页面**静默使用旧设置**，是最难查的一类问题。

`config.js` 缺失时页面回落到内置默认值，**全新克隆的仓库行为不变**。

**页面里信令地址的主机名仍取自 `location.hostname`**（自适应），
只有端口和 ICE 服务器来自配置。同时协议也自适应了：

```javascript
(location.protocol === "https:" ? "wss://" : "ws://")
    + location.hostname + ":" + port
```

这样将来页面走 HTTPS 时会自动切换 `wss://`，**避免混合内容被浏览器拦掉**。

## 32.3 `videotestsrc` 模式：不需要摄像头的连通性测试

```bash
DSH_VIDEO_SOURCE=videotestsrc ./run_sender_loop.sh
```

把视频源换成 GStreamer 自带的测试彩条后，整条链路（信令 → STUN/ICE → DTLS →
SRTP → 浏览器）都能跑通，**只少了摄像头**。这让"公网能不能连通"可以在一台
**没有摄像头、甚至不用装虚拟机**的机器上验证。

探针位置在两个分支里是**同一个逻辑点**（解码后的帧），所以两种模式下测出的数字
仍然可比。

## 32.4 公网可行性实测：锥形 NAT，不需要 TURN

### 为什么需要专门写一个工具

用浏览器的 trickle-ice 页面测了三次，每次**只有一条 srflx 候选**，无法判断
NAT 类型。原因写在 `tools/stun_probe.py` 的注释里：

> **ICE 会把"映射地址完全相同"的 srflx 候选合并成一条。**
> 所以"只有一条"既可能是"只有一台 STUN 响应了"（无法判断），
> 也可能是"多台都响应了但映射相同"（锥形 NAT）。**这两种情况结论相反。**

`tools/stun_probe.py` 用**同一个本地 socket** 向多台 STUN 服务器各发一次
Binding Request，**逐台打印各自的 XOR-MAPPED-ADDRESS**，从而把两者区分开。

### 实测结果（2026-10-10）

```text
socket A（本地端口 57978）:
    stun.l.google.com:19302       -> 183.198.108.95:5296
    stun.miwifi.com:3478          -> 183.198.108.95:5296
    stun.cloudflare.com:3478      -> 183.198.108.95:5296
    stun.qq.com:3478              -> timeout

socket B（本地端口 57979）:
    stun.l.google.com:19302       -> 183.198.108.95:5322
    stun.miwifi.com:3478          -> 183.198.108.95:5322
```

| 判定 | 结果 |
|---|---|
| 同一 socket 对多台服务器的映射端口 | **完全一致** → 端点无关映射 |
| 跨不同目的端口（19302 / 3478） | 一致 → 排除地址相关映射 |
| 换本地 socket | 映射随之改变，但对各服务器仍一致 |
| 公网 IP | 三次都是 `183.198.108.95` → 无多出口负载均衡 |

**结论：锥形 NAT（端点无关映射），打洞可行，不需要 TURN 中继。**

同时反证了浏览器上那"只有一条 srflx"属于上面的第二种情况。

### 对架构的影响

| | 原估计 | **实际** |
|---|---|---|
| 需要 TURN | 可能要 | ❌ 不需要 |
| 需要 VPS | 可能要 | ✅ 不需要（信令也可放本地） |
| 最小公网方案 | VPS + TURN + TLS | **路由器上两条端口映射** |
| 成本 | 每月 VPS 费用 | **0** |

```text
8000/tcp  ->  页面所在机器
8765/tcp  ->  信令服务器
浏览器打开  http://183.198.108.95:8000/index_latency.html
```

页面走 http，`ws://` 不会被混合内容拦截，所以**连域名和证书都不需要**。

### 一个反直觉的延迟数字

```text
秦皇岛 ↔ 石家庄 ≈ 500–600 km
光纤中光速     ≈ 2×10^8 m/s
单向传播延迟   ≈ 2.5–3 ms
```

**地理距离几乎不贡献延迟。** 公网相比局域网多出来的延迟主要来自
**WebRTC 抖动缓冲变大**（因抖动与丢包），而不是网络本身。因此
`playoutDelayHint`（浏览器侧）和 `webrtcbin` 的抖动缓冲参数
会是公网阶段最值得调的旋钮。

### 测量精度的代价（必须显式报告）

时钟偏差估计误差 `<= (去程 - 回程)/2 <= RTT/2`：

| 场景 | RTT | 偏差估计误差 |
|---|---|---|
| 本机 | 0.4 ms | <= 0.2 ms |
| 公网跨省 | 15–40 ms | **<= 8–20 ms** |

**这是系统性偏差，不是噪声** —— 它会让所有延迟数字整体平移。
所以公网阶段不能只报"150 ms"，必须报"150 ± 10 ms"。
值得探索的标准替代方案是 **RTCP Sender Report**（NTP↔RTP 时间戳映射），
它走媒体路径而不是旁路，可能比我们的 NTP 式交换更准。

## 32.5 待办

- [ ] 在石家庄（查看端）也跑一次 `tools/stun_probe.py` —— 打洞只需一方锥形，
      但双方都是锥形时成功率和连接速度更好
- [ ] 若 sender 继续跑在虚拟机里，注意多一层 VMware NAT；生产环境建议裸机
      或桥接网络
- [ ] 把 `python3 tools/make_page_config.py --check` 加进提交前检查
- [ ] 探索 `playoutDelayHint` 与 RTCP SR 两条路径
- [ ] **配置层改动尚未在真机上实测**（改动时虚拟机停机），首次启动需验证：
      `python3 config.py`、`DSH_VIDEO_SOURCE=videotestsrc` 能起管线、
      页面 `Match / Miss` 仍为 100%

---

# 33. 2026-10-11 面板记录 ICE 候选对类型

## 33.1 为什么必须记录这个

**延迟数字的含义取决于这一路走的是 P2P 还是中继：**

```text
host  -> host          同一局域网，不涉及 NAT 穿越
host  -> srflx/prflx   直连对穿（P2P），延迟就是真实网络延迟
relay -> ...           经 TURN 中继，多了一段与摄像头和我们的软件
                       都无关的绕路
```

**所以「不知道路径类型」等于「不知道延迟数字代表什么」。** 对一个以可量化测量
为核心资产的项目，这是不能接受的盲区 —— 尤其是在公网阶段，ICE 有可能悄悄
回落到中继。

## 33.2 实现

面板 WebRTC 区新增一行 `ICE path`。取值来自 `getStats()` 里被选中的
`candidate-pair`（页面本来就用它算 RTT），再查 `localCandidateId` /
`remoteCandidateId` 对应的 `candidateType`：

```text
host -> prflx   P2P        直连（绿色）
relay -> host   TURN/udp   中继（黄色警告，并显示 relayProtocol）
```

`relayProtocol` 是 `udp` / `tcp` / `tls` —— 严格网络里只有 `tls`（443）能出去，
所以这个值在部署时有用。

样本同时进入长测 JSONL，分析器新增一段输出：

```text
ICE path selected:
      5 x  host -> prflx  P2P   (direct peer-to-peer)
```

## 33.3 当前实测（局域网）

Chrome 在宿主机、sender 在虚拟机，选中：

```text
host -> prflx  P2P
```

**`prflx` 是「对端反射候选」**：本端从收到的 STUN 连通性检查包里学到对端地址，
而不是从信令消息里收到的那个。**这本身值得注意** —— 说明信令里通告的候选和
实际可用的地址不完全一致（虚拟机在 VMware NAT 后面会出现这种情况）。

**这条信息在公网阶段会更重要**：如果两端都只能通过 `prflx` 建立连接，说明
信令通告的候选基本没用上，那 STUN 配置是否正确就成了关键。

## 33.4 顺带修掉：分析器把 match 计数算成负数

页面重载会让 match/miss 计数器归零，而分析器原来用 `last - first` 算总量，
于是**中途重载被算成负增量**（实测出现过 `-1163 / 0`）。

改为**累加正的增量**，负增量视为「计数器刚重置」并跳过。

这个问题只在页面自愈重载时出现，所以之前的 60 分钟长测（2 次重载）如果
恰好卡在边界上也会读到错误数字 —— 属于必须修的测量工具缺陷。

## 33.5 采样字段保持纯 ASCII

页面显示用 `host → prflx`（箭头好看），但长测工具采样时把 `→` 转成 `->`。

理由：这个字符串会被写进 JSONL、被分析器打印，而非 ASCII 字符在非 UTF-8
控制台（**Windows 默认就是**）会显示成乱码。**UI 保持可读，数据保持 ASCII。**

> 顺带确认：分析器一直是用 `encoding="utf-8"` 读文件的，JSONL 也是 UTF-8
> 写入，所以**数据链本身没有问题**，乱码只出现在控制台显示环节。

---

# 34. 2026-10-11 公网安全层

## 34.1 威胁模型

把服务放上公网之前必须解决两件事：

| 威胁 | 后果 |
|---|---|
| 信令服务器无鉴权 | 任何人可连上并注入 SDP，劫持会话 |
| **TURN 服务器无鉴权** | 被扫描器在几小时内找到、当成免费中继 —— **按流量计费下这是直接扣钱**（100 GB/天 约 ¥80/天） |

## 34.2 共享令牌

一个共享密钥，同时用于两处：

```text
信令 WebSocket :  ws://host:8765/?token=<secret>
页面链接       :  http://host:8000/index_latency.html?token=<secret>
                  （页面从自己的 URL 读出 token，再带给信令）
```

实现要点：

- **在 HTTP 握手阶段就拒绝**（`process_request`），而不是连上之后再断开 ——
  坏 token 得到干净的 401，而不是一个"开了就关"的 socket
- **常量时间比较**（`hmac.compare_digest`）—— 普通 `==` 会让攻击者通过
  计时逐字节猜出 token
- **token 为空 = 鉴权关闭**，启动时打印醒目警告。这样局域网工作流完全不变

**踩过的坑（值得记下来）**：第一版把检查放在 handler 里，从连接对象上取
`request.path`。websockets 16 上那个属性不在预期位置，于是**路径恒为空、
所有连接都被拒**（包括正确的 token），现象看起来像"鉴权逻辑坏了"。
改用 `process_request(connection, request)` 后 `request.path` 直接可用，
而且**确实带查询串**（`'/some/path?token=abc&other=1'`）。

## 34.3 TURN 短时凭据（服务器侧已就绪）

**绝不用长期密码，更绝不能把密码写进公开的页面。** 用 coturn 的
`use-auth-secret`：

```text
username   = <过期时间戳>
credential = base64(HMAC-SHA1(static-auth-secret, username))
```

密钥**永不离开服务器**。已实现并测试：

```text
[ok]  username is the expiry timestamp   1000003600
[ok]  credential is base64(HMAC-SHA1(secret, username))
[ok]  the secret itself is not in the result
[ok]  a later mint gets a different username
```

**发放链路**：信令服务器为每个连接铸一份，作为**第一条消息**发出，而且是在把
socket 加入 `clients` **之前** —— 浏览器在 socket 打开前就创建了
RTCPeerConnection，offer 若先到，answer 里就不会有任何 relay 候选，
中继会静默地永远不被使用。

两端都已接好并实测：

```text
页面   pc.getConfiguration().iceServers 里出现带 credential/username 的 TURN 条目
发送端 [WebRTC] TURN credentials received / TURN server(s) added: 1
链路   match/miss = 599/0 = 100%，ICE path = host -> prflx P2P
```

## 34.4 服务器加固（`deploy/turnserver.conf.template`）

| 项 | 作用 |
|---|---|
| `use-auth-secret` | 必须认证 |
| `max-bps` | 每会话带宽上限 |
| `total-quota` / `user-quota` | 并发配额 |
| `denied-peer-ip` 私有网段 | 防止被用来探测云内网 |
| `no-multicast-peers` / `no-tlsv1` | 常规加固 |
| `external-ip=PUBLIC/PRIVATE` | **阿里云公网 IP 是 1:1 NAT，少了这行 relay 候选全是坏的** |

## 34.5 仓库卫生

- 密钥放在 `/etc/dsh-camera/env`，**故意放在仓库之外** ——
  这样任何一次"静态文件服务器配错"都不可能泄露它
- `.gitignore` 额外挡一层
- **新增 `serve_page.py`**：原来的 `python -m http.server` 会
  **把整个工作目录发布出去**（含测量数据、笔记、任何残留密钥）。
  新服务器**只应答 `index_latency.html` 和 `config.js` 两个文件**

## 34.6 端到端验证

```text
part 1  TURN 凭据与 URL 组装                    10/10 通过
part 2  实时鉴权
        [ok] correct token accepted   open
        [ok] wrong token refused      handshake refused (InvalidStatus)
        [ok] missing token refused    handshake refused (InvalidStatus)

开鉴权后的正常链路（真摄像头，带 token）:
        conn=CONNECTED  peer=connected  ice=connected
        match/miss = 597 / 0  ->  100.00%
        ICE path   = host -> prflx  P2P
        fps 29.8,  Source->Receive P50 11.0 ms
```

**结论：鉴权没有影响正常链路。**

## 34.7 顺带发现的一个真问题：摄像头设备号不稳定

验证过程中 sender 反复报 `Failed to start pipeline`。查出来是
**摄像头从 `/dev/video0` 变成了 `/dev/video1`**（重新枚举过）。

**`/dev/videoN` 按枚举顺序分配，会变。** 稳定路径是：

```text
/dev/v4l/by-id/usb-webcam_webcam_AN20200907002-video-index0   <- 摄像头
/dev/v4l/by-id/usb-webcam_webcam_AN20200907002-video-index1   <- 元数据
```

**建议 `DSH_VIDEO_DEVICE` 用 by-id 路径。** 已做三件事：

1. `config.py` 启动时如果设备不存在会**明确报出来并列出 by-id 路径** ——
   否则症状是"管线启动失败"，会让人去查管线而不是查配置
2. `tools/preflight.py` 会主动列出所有稳定路径
3. `docs/PORTING.md` 把它标成移植时最容易踩的坑之一

## 34.8 新增/改动的文件

| 文件 | 作用 |
|---|---|
| `serve_page.py` | 只服务页面和 config.js（不再暴露整个仓库） |
| `tools/restart_stack.sh` | 安全重启整套服务（避免 pgrep 杀掉自己的 shell） |
| `tools/verify_security.py` | 安全层验证（凭据铸造 + 实时鉴权） |
| `deploy/turnserver.conf.template` | coturn 加固配置 |
| `deploy/setup-server.sh` | 一键部署 coturn + 信令 + 页面 |
| `deploy/secrets.env.example` | 密钥模板 |
| `deploy/README.md` | 完整部署与排错指南 |

---

# 35. 2026-10-11 流量止损：从"提醒"到"真的能停"

## 35.1 一个必须纠正的认知

我此前把「费用告警」列为主要防护手段。**这是不完整的**：告警只告诉你钱在花，
**它不停任何东西**。而按流量计费**没有金额上限**。

**"我注意到再处理"不是一个控制手段** —— 等你注意到，钱已经花出去了。

## 35.2 四层止损，从最快开始

| 层 | 手段 | 生效 | 需要什么 |
|---|---|---|---|
| **0** | **控制台：带宽峰值降到 1 Mbps** | **立即** | **手机就行** |
| **0** | **控制台：删掉安全组里的 TURN 规则** | **立即** | **手机就行** |
| **0** | **控制台：停止实例** | **立即** | **手机就行** |
| 1 | `kill-switch.sh relay`（停 coturn） | 秒级 | 终端 |
| 2 | `kill-switch.sh inbound`（再掐入站） | 秒级 | 终端 |
| 3 | **`dsh-traffic-guard` 自动执行** | 秒级 | 已安装 |

**第 0 层才是真正的紧急按钮** —— 不需要 SSH、不需要在电脑前。
**建议把「安全组」页面加入书签。**

**为什么第 1 层通常就够**：coturn 是这套部署里**唯一能跑出 GB 级流量**的东西。
停掉它不影响 P2P，而且一条命令就能恢复。

> **失去中继是可恢复的；跑飞的账单不是。**

## 35.3 自动止损：`deploy/traffic_guard.py`

盯着**默认路由网卡的出方向字节数**，超过每日预算就**自动停掉 coturn**。

```bash
python3 deploy/traffic_guard.py --status     # 今天用了多少、值多少钱
python3 deploy/traffic_guard.py --once       # 检查一次
```

- 预算默认 **5 GB/天（约 ¥4）**，`DSH_DAILY_TX_BUDGET_GB` 可调
- 状态存 `/var/lib/dsh-camera/traffic.json`，**重启不丢**，本地零点重置
- 由 `deploy/setup-server.sh` 装成 `dsh-traffic-guard` 服务

**两个设计决定值得说明：**

1. **测网卡计数而不是 coturn 自己的统计。** 它会把信令、SSH、apt 也算进去，
   但那些是 KB～MB 级，而中继是唯一能跑 GB 的东西。预算设在正常用量的几十倍
   以上，所以它是**跑飞探测器**，不是计量表。
2. **出错时绝不退出监控循环。** 监控脚本自己挂掉比没有监控更糟 ——
   它会让人以为还有保护。

## 35.4 为什么"封顶"不能只靠 coturn 的配额

coturn 的 `max-bps` × `total-quota` 能限制**速率**，于是也就限制了**每日上限**：

```text
max-bps=750000 (约 6 Mbps) x total-quota=12  =  约 72 Mbps
                                             =  约 777 GB/天
                                             =  约 ¥620/天     <- 太多了
```

所以配额要往下调（比如 `total-quota=4`），**但真正兜底的是这个流量守卫** ——
因为它限制的是**总量**，而配额限制的只是**速率**。

## 35.5 新增文件

| 文件 | 作用 |
|---|---|
| `deploy/traffic_guard.py` | 每日出方向预算；超了自动停 coturn |
| `deploy/kill-switch.sh` | 手动止损：`status` / `relay` / `inbound` / `restore` |

## 35.6 待办

- [ ] 部署时把 `total-quota` 从 12 调到 4
- [ ] 部署后跑一次 `traffic_guard.py --status` 确认计数正常
- [ ] **把安全组页面加书签**（真正的紧急按钮）
- [ ] 费用告警仍然要设（它是第 3 层的触发器，不是止损手段）

---

# 36. 2026-10-11 目录整理与代码总检

## 36.1 整理前的问题

根目录同时放着**当前在用的代码**和**十一个早期版本**，还有两个根本不是代码的文件：

| 文件 | 问题 |
|---|---|
| `cv.txt` | 一段 23 行的管线片段，**而且是错的**（没有时间戳探针，还是 BGR 往返之前的样子）。放在根目录里，是能被误抄的东西 |
| `t-inspect-1.0 timeoverlay` | `gst-inspect-1.0` 的输出被重定向到一个名字被截断的文件（185 行 ANSI 转义） |

**它们能被误用，就是它们待在根目录的代价。**

## 36.2 现在的布局

```text
根目录 / tools/ / deploy/     当前在用的代码
attic/                        早期版本与实验脚本（保留供追溯）
docs/                         技术文档、移植指南、代码管理规范
measurements/                 长测原始数据与分析输出
```

**当前的入口：**

```text
run_sender_loop.sh                   监管循环 + 断线重启
webrtc_sender_Now_latency_probe.py   发送端
index_latency.html                   浏览器页面（接收 + 测量 + 面板）
signaling_server.py                  信令（令牌鉴权 + 短时 TURN 凭据发放）
serve_page.py                        只服务页面和 config.js
config.py                            唯一配置真值源
```

## 36.3 总检结果

| 检查 | 结果 |
|---|---|
| Python 编译（14 个文件） | ✅ 全过 |
| shell 语法（4 个脚本） | ✅ 全过 |
| 页面内联 JS 解析（node --check） | ✅ 过 |
| `git grep` 找密钥 | ✅ 只命中测试假值 `TEST_SECRET = "testsecret"` |
| 硬编码部署值 | ✅ 只出现在 `config.py` 的默认值里 |
| TODO / FIXME / XXX | ✅ 无 |
| 当前代码是否引用被移动的文件 | ✅ 无 |

## 36.4 这次整理暴露出的一个规范问题

`docs/WORKFLOW.md` 第 1 节第 4 条明确禁止 `git reset --hard`，
但**在虚拟机停机期间，为了修一次失败的 `pull`，这个操作在 Windows 浏览副本上
被用过一次**。那次恰好无害（副本上没有未提交的工作），但"恰好无害"不是规则成立
的理由 —— 规则存在的意思就是不需要靠运气。

已经把这个场景写进 `WORKFLOW.md` 的 §0.1：**虚拟机停机时怎么安全地干活**，
包括正确的同步方式（`git pull --ff-only`，失败时先看清未跟踪文件是什么）。

---

# 37. 2026-10-11 独立代码审查与修复

## 37.1 为什么要做

短时间内往代码里加了很多东西（配置层、安全层、流量守卫、TURN 凭据发放），
**自己审自己一定会漏**。所以让一个独立的 agent 用怀疑的眼光过一遍"当前在用的"
文件，要求它给出 `文件:行号` 级别、可核实的发现，并明确区分"确认过"与"猜测"。

**结果证明这件事是必要的** —— 它找到的三个 HIGH 全是我自己写的、而且都没意识到。

## 37.2 三个 HIGH（已修复）

### H1. `<video autoplay>` 没有 `muted`，也没有 `play()` 兜底

**这是最危险的一个，因为它暴露的是一个测试盲区，而不是一个代码错误。**

所有测量会话都用 `--autoplay-policy=no-user-gesture-required` 启动 Chrome，
所以"普通浏览器会不会拒绝播放"**从来没有被验证过**。

如果被拒绝：面板显示 `peer=connected ice=connected`、FPS 停在初值、
延迟停在 `--`，**没有任何一行提示"自动播放被拒绝"**。

**修复**：加 `muted`（本来就没有音轨）+ 在 `ontrack` 里显式 `play()`，
把失败写进日志与面板。

**验证** —— 用**不带那个 flag** 的 Chrome 打开：

```text
{"paused": false, "readyState": 4, "muted": true,
 "currentTime": 16.89, "videoWidth": 1920}
```

画面正常播放，`match/miss = 453/0 = 100%`。

### H2. offer 分支没有 try/catch，也没有重复 offer 守卫

`ws.onmessage` 是 async，await 抛出会变成 **unhandled rejection** ——
socket 还在、页面还在跑，**没有任何东西会重载它**。

而 `scheduleReload` 只有两个触发点：`connectionState === "failed"` 与
`ws.onclose`。如果 `connectionState` 停在 `new`（**首次 offer 失败就是这个状态**），
两个都不触发 → **永久黑屏**。

**这个错误在本项目里真的出现过**："The order of m-lines in subsequent offer..."
—— 当时能恢复只是因为 `connectionState` 恰好走到了 failed。**它不总是会。**

**修复**：① 重复 offer 守卫，直接重载；② `setRemoteDescription` /
`createAnswer` / `setLocalDescription` 全部包 try/catch，失败即 `scheduleReload`；
③ 发 answer 前检查 socket 状态。

### H3. 时钟估计在 step 之后会 1 Hz 震荡

step 确认时更新了 `clockOffsetMs`，但 **15 秒窗口里 step 之前的旧样本还在**。

```text
step 确认      -> clockOffsetMs = 新值
下一条回复      -> 最新 3 条已与当前估计一致 -> 不满足 step 条件
                -> 落到 min-RTT 分支 -> 可能挑中 step 前的旧样本
                -> clockOffsetMs 退回旧值
再下一条回复    -> 又满足 step 条件 -> 跳回新值
```

**每次对一半的帧用错偏移，而且什么都不记录。** §30 报的 "-19.6 ppm 漂移" 里
可能就混着这个。

**修复**：step 确认后把窗口裁剪到只保留最新 3 条（一行）。

## 37.3 四个 MEDIUM（也已修复）

| # | 问题 | 修复 |
|---|---|---|
| **M4** | TURN 处理什么都没应用也打印 "applied"；`setConfiguration` 未捕获 | 按实际结果分别记录；包 try/catch |
| **M5** | 面板的 `Clock sync RTT` 不是所用偏移那条样本的 RTT | 各分支记录自己那条样本的 rtt |
| **M6** | **流量守卫遇到非法预算会崩掉、不再守卫** | 新增 `budget_from_env`：非法值打日志并回落默认 |
| **M8** | 文档说 TURN 发放端点"还没接"，其实已经接好了 | 更新 `deploy/README.md` 与 §34.3 |

**M6 是最坏的失败模式**：`float("5GB")` 在 argparse 构造时抛异常 → 进程退出 →
systemd 每 10 秒重启 → **守卫永远不真正运行，而 systemd 显示它在 restarting**。

**一个安全装置以这种方式失效，比没有安全装置更危险** —— 因为它会被信任。

实测：

```text
[GUARD] DSH_DAILY_TX_BUDGET_GB='5GB' is not a number; falling back to 5.0 GB
[GUARD] new day ... starting the counter at tx=4321622291     <- 继续运行
```

## 37.4 L3 不是理论问题 —— 它当场复现了

审查预测：`send()` 只注册了 resolve，CDP 中途断线会让 await 永不结束，
**整场 soak 静默停止采样**。

**就在审查进行期间，一次 20 分钟 soak 正好撞上了它** —— 我为另一个测试重启了
Chrome，harness 随即打印：

```text
Warning: Detected unsettled top-level await at long_test.mjs:173
  const panel = await takeSample();
```

**"静默停止"对测量工具是最坏的失效方式：它看起来就像运行正常结束了。**

**修复**：

1. `send()` 加 30 秒超时，超时 reject
2. `stream.end()` 的回调里才 `process.exit`，并加 5 秒兜底（原来直接
   `end(); exit(0)`，缓冲区里的尾部记录会被丢掉）
3. 采样增加 `decodedFps`（分析器一直在列这个指标，但因为 harness 没采，
   **整行被静默跳过**，报告看起来仍然完整）
4. 采样增加 `hidden`（后台标签页会节流定时器、停止 rVFC，采样器会一直记录
   最后一个值 —— **数据里"标签页被隐藏"和"延迟很稳"完全无法区分**）

**验证**：杀掉 Chrome 再跑一次，20 秒正常结束（`samples=10`），不再挂死。

## 37.5 审查确认"看起来可疑但其实正确"的地方

这部分同样有价值 —— 它把几个我本来就该确认的推理独立验证了：

| 位置 | 结论 |
|---|---|
| FIFO 配对 + 每帧重锚 `pts_offset` | **正确**。按丢帧场景推演：循环结束时 `source_frames[0]` 恰是时间上最接近的正确源帧，上游丢帧被吃掉并计入 `skipped`，不会整体错位一帧 |
| `send_turn_credentials` 先于 `clients.add` | **构成结构性保证**：服务器不可能把转发消息发给还不在 `clients` 里的连接，所以凭据必然先于 offer 到达 |
| `if (answer) ... elif (candidate)` | **语义正确**（前面的分支都 return 了）—— 但很脆：将来在两者之间插分支就会破坏它 |
| `pendingCandidates` 的清空竞态 | **不是 bug**：`remoteDescriptionSet = true` 早于 drain 循环，drain 期间新到的候选走另一条分支 |
| `run_sender_loop.sh` 里的 `$?` | **正确**：`echo` 的展开发生在 `python3` 之后，取到的是 python 的退出码 |

## 37.6 尚未处理

| # | 项 | 说明 |
|---|---|---|
| M1 | `[MAP] rate=100%` 是**存活检查**，不是正确性检查 | `pts_offset` 每帧重锚，所以一次错配会自我一致地固化、仍然报 100%。**面板和文档不该把它当作可信度指标** —— 真正支撑可信度的是 §31.5 的光学交叉验证 |
| M2 | `rtp_mapping_queue` 无上界 | 正常永不积压，但 GLib 主循环被阻塞时会无界增长（历史上发生过） |
| M3 | `remote_answer_set` 在 SDP 校验前就置位 | 持续性的坏 answer 会导致每 2 秒重启一次的活锁，而且日志看起来像"正常恢复" |
| L4 | 分析器把 clock step 与真实漂移混报成 "ppm" | §30 的 -19.6 ppm 可能含 step |
| L5/L6 | `setup-server.sh` 声称幂等但那一段是追加；且 unit 起不来也照常打印 "deployed" | 部署前该修 |
| L7 | `tools/pts_diag.py` 仍在搭已被删除的 BGR 往返 | 用它诊断当前管线量的是另一条管线 |
| L9 | 硬编码 `sdpMid: "video0"` | 名字由 webrtcbin 决定 |
| L10 | `serve_page.py` 的 docstring 说这里会放 TURN 端点 | 实际在 `signaling_server.py` |
| L11 | ICE 级 `failed`/`disconnected` 只改颜色、不触发恢复 | 健壮性缺口 |
| L12 | `getStats` 出错只进 console | 持续失败看起来像"健康但不动" |
| L13 | `receiveToCallback` 无符号守卫 | Chromium 的 `receiveTime` 偶尔超前 `now`，会显示负值（**本项目见过**） |
