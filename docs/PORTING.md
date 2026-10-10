# 移植指南：把项目搬到另一台 Ubuntu 22.04

> 结论先说：**测量链路本身的逻辑是机器无关的**，真正需要改的硬编码只有 **6 处**，
> 而且都很集中。但**摄像头的差异**是移植中最不可控的部分 —— 见第 5 节。

---

## 1. 可移植性评估

### 1.1 机器无关的部分（占绝大部分）

| 模块 | 为什么可移植 |
|---|---|
| RTP 同帧配对（FIFO + PTS 平移量自动跟踪） | 纯算法，只依赖 GStreamer 的 buffer 语义 |
| 时钟偏差估计（WebSocket 上的 NTP 式交换） | 纯算法，不依赖任何系统配置 |
| 延迟计算与统计（P50/P95/P99） | 纯算法 |
| 断线自愈（监管循环 + 页面重载） | 纯进程管理 |
| 浏览器端 `index_latency.html` | 已自适应：`ws://" + location.hostname + ":8765"` |

### 1.2 硬编码清单（移植时必须逐条确认）

| # | 值 | 位置 | 移植时的动作 |
|---|---|---|---|
| 1 | `ws://127.0.0.1:8765` | `webrtc_sender_Now_latency_probe.py:33` | 信令不在本机时**必须改**成目标地址 |
| 2 | `device=/dev/video0` | `webrtc_sender_Now_latency_probe.py:103` | 确认目标机的设备号 |
| 3 | `image/jpeg,width=1920,height=1080,framerate=30/1` | `webrtc_sender_Now_latency_probe.py:105` | 确认目标摄像头支持 MJPG 1080p30 |
| 4 | 端口 `8765` | `signaling_server.py:41` | 与浏览器、sender 三方一致即可 |
| 5 | `127.0.0.1:9222`（CDP） | `tools/cdp_probe.mjs:11`、`cdp_eval.mjs:11`、`long_test.mjs:20` | 仅影响自动化工具 |
| 6 | 端口 `9099` | `tools/timesrv.py:24` | 仅影响时钟诊断工具 |

**另有 3 处同样的设备/格式硬编码在诊断工具里**，与主程序无关但一起改比较省事：

```
tools/pts_diag.py:26,28        device= + image/jpeg,1920x1080,30/1
tools/stage_timing.py:58,61    CAPS + device=
```

> 建议移植时顺手把 #1 #2 #3 改成环境变量（约 20 行改动），这样以后再搬机器就不用动代码。
> 本次**没有**做这个改动 —— 需要先在目标机上实测通过再改。

---

## 2. 依赖清单

### 2.1 系统包（apt）

```bash
sudo apt update
sudo apt install -y \
    python3-gi \
    python3-gst-1.0 \
    gir1.2-gst-plugins-bad-1.0 \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-tools \
    v4l-utils
```

各包对应关系：

| 包 | 提供的 GStreamer 元素 |
|---|---|
| `plugins-base` | `videoconvert` `queue` `identity` |
| `plugins-good` | `v4l2src` `jpegdec` `h264parse` `rtph264pay` |
| `plugins-bad` | **`webrtcbin`** ← 最关键 |
| `plugins-ugly` | **`x264enc`** |
| `gir1.2-gst-plugins-bad-1.0` | **Python 的 `GstWebRTC` 绑定** ← 最容易漏 |
| `v4l-utils` | `v4l2-ctl` |

> `webrtcbin` 在 `plugins-bad` 里，而 Python 要 `gi.require_version("GstWebRTC", "1.0")`
> 还需要 `gir1.2-gst-plugins-bad-1.0`。**这两个最容易漏，漏了会报 `RequireVersion` 或
> `Namespace GstWebRTC not available`。**

### 2.2 Python 包（pip）

```bash
pip3 install --user websocket-client websockets
```

| 包 | 用途 | 谁用 |
|---|---|---|
| `websocket-client` | WebSocket **客户端** | sender |
| `websockets` | WebSocket **服务端** | signaling_server |

> 注意两个包名字很像但**是两个不同的库**，都要装。
> 仓库里 `webrtc_sender_Now_latency_probe.py` 用 `import websocket`（客户端），
> `signaling_server.py` 用 `import websockets`（服务端）。

### 2.3 Node.js（可选）

只有自动化测试工具需要（`tools/*.mjs`）。要求 **Node 22 以上**（用到原生
`WebSocket` 和 `fetch`）。

```bash
node --version   # 需要 v22+
```

不需要 npm 包 —— 那些脚本只用 Node 内置能力。

### 2.4 浏览器（接收端，通常不在同一台机器）

需要 **Chrome / Edge**（基于 Chromium）：

- `requestVideoFrameCallback`：Chrome 83+
- `metadata.rtpTimestamp`：Chromium 对 WebRTC 的扩展，**Firefox 不支持** ← 这是硬约束

---

## 3. 移植步骤

```bash
# ① 克隆
git clone <仓库地址> ~/webrtc-camera
cd ~/webrtc-camera

# ② 预检（先跑这个，它会逐项报告缺什么）
python3 tools/preflight.py

# ③ 按预检结果补依赖（见第 2 节）

# ④ 再次预检，直到全绿
python3 tools/preflight.py

# ⑤ 改硬编码（见 1.2 节，至少改 #1 #2 #3）

# ⑥ 单独验证摄像头能力（不要直接跑整个项目）
v4l2-ctl -d /dev/video0 --list-formats-ext | head -40

# ⑦ 单独验证 GStreamer 管线能起来
gst-launch-1.0 -v v4l2src device=/dev/video0 io-mode=mmap num-buffers=30 \
    ! image/jpeg,width=1920,height=1080,framerate=30/1 \
    ! jpegdec ! fakesink sync=false

# ⑧ 起服务（两个终端，或用监管循环）
./run_sender_loop.sh signaling_server.py     # 信令
python3 -m http.server 8000 --bind 0.0.0.0   # 页面

# ⑨ 浏览器打开 http://<这台机器的IP>:8000/index_latency.html

# ⑩ 最后起 sender
./run_sender_loop.sh
```

### 3.1 关于摄像头权限

sender 以普通用户运行时需要能打开 `/dev/video0`：

```bash
groups | grep -q video && echo 'ok' || sudo usermod -aG video $USER
# 加组后需要重新登录
```

### 3.2 关于防火墙

浏览器在**另一台机器**上时，需要放通两个端口：

```bash
sudo ufw allow 8000/tcp    # 页面
sudo ufw allow 8765/tcp    # 信令
```

---

## 4. 不同情况的处理

### 4.1 信令服务器不在 sender 这台机器上

改 `webrtc_sender_Now_latency_probe.py:33`：

```python
self.ws_url = "ws://127.0.0.1:8765"        # 改成
self.ws_url = "ws://<信令服务器IP>:8765"
```

**注意**：浏览器侧的地址是**自动跟着页面来源**的（`location.hostname`），所以浏览器
不需要改；但两条连接必须能互相看到对方的信令消息，也就是**必须连到同一个信令服务器**。

### 4.2 摄像头分辨率/格式不同

先查目标摄像头支持什么：

```bash
v4l2-ctl -d /dev/videoN --list-formats-ext
```

然后改 `webrtc_sender_Now_latency_probe.py:105` 的 caps。**三个必须匹配**：
`image/jpeg`（不是所有摄像头都支持 MJPG）、`width/height`、`framerate`。

> 如果目标摄像头**不支持 MJPG**，可以选择：
> - 支持 H.264 的 → 走原生 H.264 路线（§7–§9 已验证初步可行）
> - 只支持 YUYV 的 → 把 `image/jpeg` 换成 `video/x-raw,format=YUY2` 并去掉 `jpegdec`，
>   **但那会大幅增加 USB 带宽占用**

### 4.3 目标机不是虚拟机（裸机 Linux）

**这是好消息**：裸机省掉了 VMware 的虚拟 USB 层，理论上会降低摄像头那条链的延迟。

从我们已有的测量看，虚拟机里：

```
③ USB 传输 ≥ 12.1 ms   （290 KB ÷ USB2 等时上限 24 KB/ms，纯带宽计算）
```

裸机上这个 12.1 ms 是同一颗摄像头的**物理下限**，不会更低；但虚拟化层额外引入的
延迟会消失。**移植到裸机后建议用同样的方法测一遍，对比 §31 的逐级数据。**

### 4.4 目标机是 ARM（如 Jetson / 树莓派）

- `x264enc` 在 ARM 上很慢，**1080p30 软件编码可能跑不动** → 建议直接走原生 H.264
  或使用硬件编码器（`nvh264enc` / `v4l2h264enc`）
- `webrtcbin` 在 ARM 上可用，但要注意 GStreamer 版本

---

## 5. 移植中最不可控的部分：摄像头本身

**这是必须提前知道的**：根据 §30/§31 的实测，端到端 150 ms 里：

```
摄像头链（曝光 + 编码 + USB + 驱动）  ≈ 100 ms   ← 换机器不会变，换摄像头才会变
我们的软件（编解码 + WebRTC + 浏览器）≈  44 ms   ← 移植后不变
显示（液晶响应 + 刷新）               ≈ 10–30 ms
```

**所以换一台机器移植，延迟数字不会明显变化** —— 因为瓶颈在摄像头，不在机器。

想让延迟真正下降，必须换硬件：

| 需求 | 选择 |
|---|---|
| 更短的曝光 | 更好的照明（零成本）＋ 更大的光圈/传感器 |
| 更小的 USB 流量 | 原生 H.264/H.265 输出的摄像头 |
| 更低的传输延迟 | USB3 / GigE Vision，而不是 USB2 等时 |
| 曝光时刻可控 | **带外部触发（trigger）的工业相机** ← 唯一能把曝光纳入已知时间轴的办法 |

---

## 6. 移植后的验收清单

按顺序做，每步都要通过再进下一步：

- [ ] `python3 tools/preflight.py` 全绿
- [ ] `v4l2-ctl -d /dev/videoN --list-formats-ext` 里有目标分辨率/帧率
- [ ] `gst-launch-1.0 ... num-buffers=30 ! fakesink` 能跑完不报错
- [ ] 浏览器打开页面显示 `CONNECTED`
- [ ] 面板上 `Match / Miss` 的 Miss 应该接近 0
- [ ] `Clock offset` 显示一个具体毫秒值（不是 `stale`）
- [ ] `Source → Callback` 是正数
- [ ] sender 侧日志有 `[MAP] ... rate=100.0%`
- [ ] 用 `tools/timesrv.py` + 外部的时钟探针**独立验证**一次时钟偏差（见 §26 的方法）
- [ ] 跑一次 `tools/stage_timing.py full 8` 对照 §31 的逐级数字

---

## 7. 已知版本约束

| 组件 | 本机版本 | 约束 |
|---|---|---|
| Ubuntu | 22.04.5 LTS | 建议保持 22.04，GStreamer 版本一致 |
| kernel | 6.8.0-138 | 无特殊要求 |
| GStreamer | 1.20.3 | `webrtcbin` 行为在 1.20 上已验证 |
| Python | 3.10.12 | |
| python3-gst-1.0 | 1.20.1 | |
| `websockets` | 16.1.1 | 服务端写法 `handler(websocket)` 适用于 14+ |
| Node.js | v24 | 工具需要 22+ |
| Chrome | 155 | 需要支持 `rtpTimestamp` 的 Chromium |
