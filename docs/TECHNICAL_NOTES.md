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
1. 实际运行验证 RTP timestamp 映射稳定性。
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

## 初步验证 / 仍需测试

- [~] Direct H.264 长时间稳定性
- [~] RTP timestamp → source PTS 映射
- [~] RTP timestamp 同帧 latency
- [~] P50/P95/P99/Max
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
