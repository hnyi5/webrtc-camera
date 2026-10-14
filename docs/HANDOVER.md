# WebRTC 摄像头远程观看项目 —— 完整技术交接文档

> 本文档供**新对话 / 新 AI**接续工作使用。所有内容均为**实际执行并验证过**的结论。
> 凡属推断、未验证或已失败者，均已明确标注。
>
> 生成时间：2026-10-14
> 仓库主线：`main = 6b34ea2`，四层已同步（见 §3.4）

---

## 0. 30 秒速览

**做什么**：把一台摄像头（1080p30）通过 WebRTC 推到浏览器，**任意有网的地点**都能打开观看，且能**严格测量延迟**（不许出现不可信的数字）。

**做到哪了**：
- ✅ **基线目标已达成**：手机走**移动数据**、经阿里云 ECS 的 **TURN 中继**，成功看到画面，**延迟 91.6ms**，路径 `relay → srflx TURN`。
- ✅ 生产服务器已部署并验证（四个 systemd 服务、安全组、鉴权、流量守卫）。
- ⚠️ **一个未解决的主问题**：画面**从运动转为静止时**，浏览器的帧回调会**停止**，画面冻结在最后一帧，帧率在 30/10/4/14 之间循环（详见 §9.1）。
- ⏭️ **下一步**：把媒体路径**固定成只走 TURN**，消除 ICE 在两条路之间的跳动（详见 §10）。

**最要紧的三条纪律**（血泪换来的）：
1. **绝不把含引号/括号的 shell 文本内联进 SSH 命令** —— PowerShell 会吃引号、括号会变语法错误。**永远写成脚本文件再 scp 执行**。
2. **改动一处、测一处** —— 一次改一堆，出问题时无法归因。
3. **密钥不打印、不进对话** —— 只写它们在哪。

---

## 1. 项目目标与当前状态

### 1.1 原始需求
- 摄像头与观看者**不在同一地点**（演示场景：摄像头在秦皇岛，观看者在石家庄）。
- 要求 **1080p30**、**低延迟**、**延迟数字必须可信**（不能显示一个自己都不信的数）。
- 用户明确要求：**代码版本管理必须能扛住 AI 的一次错误改动**，不能因此把已有进展报废。
- 部署现实：**公网可达**（选择了租服务器 + TURN 的路线）。

### 1.2 当前状态总览

| 能力 | 状态 | 证据 |
|---|---|---|
| 远程观看（跨网络） | ✅ **已实测** | 手机移动数据 + TURN 中继成功，见 §7 |
| 延迟测量 | ✅ **已实测** | 同一会话中显示 91.6ms |
| 生产服务器部署 | ✅ 完成 | 四服务 active、外网 200、端口全通 |
| TURN 中继 | ✅ 可用 | 发送端与手机均拿到 `typ relay` 候选 |
| P2P 打洞 | 🔸 部分 | 有线/局域网可行；**移动网络实测失败**（`srflx 3` 但 `pairs: none`）→ 必须靠 TURN |
| **静止时帧率塌陷** | ❌ **未解决** | 见 §9.1，这是当前主问题 |
| ICE 路径跳动 | ❌ 未解决 | 已定位原因，方案已定，见 §10 |
| 多观看者 | ❌ 未做 | 用户决定先拖；技术方案已调研，见 §9.5 |
| HTTPS / 域名 | ⏸ 等备案 | 域名已购、备案在流程中，见 §3.5 |

---

## 2. 系统架构

### 2.1 数据流

```
[摄像头 1080p30]
      │  USB  (MJPG / H264 / YUYV 三选一，见 §6.4)
      ▼
[虚拟机 Ubuntu 22.04]  ── 发送端 webrtc_sender_Now_latency_probe.py
      │   v4l2src ! jpegdec ! x264enc ! h264parse ! rtph264pay ! webrtcbin
      │   （另有 identity 探针 + RTP 映射旁路，用于延迟测量）
      │
      │  ① WebSocket 信令（offer/answer/candidate/TURN凭据/帧映射/时钟同步）
      │  ② WebRTC 媒体（UDP，P2P 或经 TURN 中继）
      ▼
[阿里云 ECS 101.200.129.193]  ──  signaling_server.py（8765，同时供页面）
      │                          ──  coturn（3478 + 49152-65535 中继）
      ▼
[观看者浏览器]  ──  index_latency.html（面板：延迟/帧率/ICE 路径/丢包…）
```

### 2.2 端口与组件（服务器侧）

| 端口 | 协议 | 用途 | systemd 单元 |
|---|---|---|---|
| 8765 | TCP | **页面 + 信令（同一端口，因此同源）** | `dsh-signaling` |
| 3478 | UDP+TCP | STUN / TURN | `coturn` |
| 49152-65535 | UDP | **TURN 中继端口段（最易漏、最难查）** | `coturn` |
| 8000 | TCP | 旧的双端口页面服务（已非必需） | `dsh-page` |
| — | — | 流量守卫（超预算自动停 coturn） | `dsh-traffic-guard` |

> **重要设计**：页面与信令**共用 8765**，使二者**同源**。这样页面里的信令地址可由
> 页面自身 host 推导，避免了"页面能开但信令连不上"的跨域类故障。

---

## 3. 环境清单（重要内容单独列出）

### 3.1 ★ 生产服务器（阿里云 ECS）

| 项 | 值 |
|---|---|
| 公网 IP | `101.200.129.193` |
| 私网 IP | `172.16.121.179`（**1:1 NAT，coturn 的 `external-ip` 必须写两者**）|
| 实例 ID | `iZ2zehi0uewdpos7vj918zZ` |
| 地域 / 规格 | 华北2（北京）/ 2 核 2 GiB |
| 系统 | Ubuntu 22.04.5 LTS |
| 计费 | **包年包月** + **按使用流量**带宽（注意：这两者是不同的计费维度）|
| 仓库位置 | `/root/webrtc-camera` |
| 密钥文件 | `/etc/dsh-camera/env`（mode 600，目录 700）|
| 访问地址 | `/root/access-url.txt`（mode 600，**含 token，不要打印**）|
| 服务 | coturn / dsh-signaling / dsh-page / dsh-traffic-guard（均 active）|
| 流量守卫 | 默认 5 GB/天，超限自动 `systemctl stop coturn` |

### 3.2 ★ 开发虚拟机（摄像头所在）

| 项 | 值 |
|---|---|
| 地址 | `192.168.106.128`（VMware **NAT** 模式）|
| SSH 别名 | `dsh-vm`（用户 `hnyi`）|
| VMX 路径 | `F:\phntm_bridge\Ubuntu64_phntm_bridge.vmx` |
| 启动方式 | `"F:\Program Files (x86)\VMware\VMware Workstation\vmware.exe" -x <vmx>`（约 20 秒后 SSH 可用）|
| 系统 | Ubuntu 22.04 + **GStreamer 1.20.3**（**没有 webrtcsink**，只有 webrtcbin）|
| CPU | Intel i5-11260H（**核显 `/dev/dri/renderD128` 存在** ✓，但缺 VAAPI 插件 ✗，见 §9.4）|
| 摄像头 | `/dev/v4l/by-id/usb-webcam_webcam_AN20200907002-video-index0` |
| 仓库 | `~/webrtc-camera` |
| 临时 token | `/tmp/dsh-token`（43 字符，**由宿主机经 stdin 传入，勿打印**）|
| 发送端日志 | `/tmp/sender.log` |
| 监控进程 | `run_sender_loop.sh` —— 发送端退出后会自动重启（**调试时要注意它会抢回摄像头**）|

### 3.3 宿主机（Windows）

| 项 | 值 |
|---|---|
| 裸仓库镜像 | `F:\视频传输协议项目\webrtc-camera.git`（origin 指向它）|
| 工作副本 | `F:\视频传输协议项目\webrtc-camera`（供人工阅读/编辑）|
| DSH 工作目录 | `C:\Users\dell\Documents\deepseek-harness\default-workspace` |
| 代理 | `127.0.0.1:7892`（**时开时关，依赖它访问 GitHub 会失败**）|
| SSH 私钥 | `~/.ssh/dsh_ecs_ed25519`（**只在本机，从未外传**）|
| `~/.ssh/config` | 已含 `dsh-vm` 与 `dsh-ecs` 两个别名 |

### 3.4 四层同步关系（改代码必须同步！）

```
虚拟机 ~/webrtc-camera  ──push──▶  GitHub (hnyi5/webrtc-camera)
       ▲                                  │
       │                                  ├──fetch──▶ 宿主机镜像 webrtc-camera.git
       │                                  │                    │
       └──────────── 人工 pull ◀──────────┴────────────────────┴──▶ 宿主机工作副本

生产服务器 /root/webrtc-camera ──pull──▶ GitHub（**注意：该机到 GitHub 的 TLS 常中断 ✗**，
                                          必要时用 scp 直接递文件）
```

**当前四层均等于 `6b34ea2`** ✓

### 3.5 域名与备案

| 项 | 值 |
|---|---|
| 域名 | `mywebrtc.cn`（阿里云，到期 2027-10-14）|
| 备案状态 | 已提交 → 阿里云初审**已通过** → 现处「**待提交管局**」|
| 卡点 | 截图原文：**"因订单中有域名注册/过户未满2天，系统将于1-2天后提交备案。提前提交会被管局自动退回"** |
| ⚠️ 待办 | ① **不要点"撤回修改"**（会重排流程重计时）② **工信部短信核验须 24 小时内回复**（超时驳回，从头再来）③ 阿里云备案来电 `95187 / 0571-2895**** / 8804****` 必须接 |
| 备案服务码 | 有 3 个（1 个备案中、2 个可使用；每码只能备一个域名）|
| **重要认知** | 「按流量计费」是**带宽**计费方式，**不等于**「按量付费」实例计费方式。本 ECS 是包年包月，**完全符合备案要求** ✓ |
| **HTTPS 不需要备案** | 域名实名认证通过后即可解析 + 用 Caddy 自动申请 Let's Encrypt 证书，消除浏览器"危险网站"警告（页面变 https，`ws` 自动变 `wss`，**代码零改动**，因信令地址由页面 host 推导）|

---

## 4. ★ 凭据与访问方式（**本文档不含任何密钥**）

### 4.1 密钥都在哪里

| 密钥 | 位置 | 权限 | 用途 |
|---|---|---|---|
| `DSH_ACCESS_TOKEN` | 服务器 `/etc/dsh-camera/env` | 600 | 客户端访问信令/页面须携带 |
| `DSH_TURN_SECRET` | 同上 | 600 | coturn 的 `static-auth-secret`，用于签发短期凭据 |
| 完整访问 URL（含 token）| 服务器 `/root/access-url.txt` | 600 | 直接给观看者用 |

**读取方式**：
```bash
ssh dsh-ecs "cat /root/access-url.txt"                 # 完整网址
ssh dsh-ecs "grep ^DSH_ACCESS_TOKEN /etc/dsh-camera/env"  # 只看 token
```

### 4.2 ⚠️ 凭据处理纪律

- **绝不要把密钥打印进对话或日志**。本项目曾因部署脚本收尾横幅明文打印 token，
  导致**两套凭据泄漏**，已轮换三次并修复脚本（横幅现在只提示用 `grep` 去读）。
- 如需轮换：编辑 `/etc/dsh-camera/env` 中的两个值，然后重跑 `sudo bash deploy/setup-server.sh`
  （该脚本**幂等**，重跑安全）。
- 轮换后旧 token 立即失效（已实测：旧 token 返回 401 ✓）。

### 4.3 SSH 访问

```bash
ssh dsh-vm      # 虚拟机（用户 hnyi）
ssh dsh-ecs     # 生产服务器（用户 root）
```

- 服务器**只接受 publickey**（不支持密码登录）✓
- 公钥已写入服务器 `authorized_keys`；**私钥只在宿主机** `~/.ssh/dsh_ecs_ed25519`
- 撤销方式：从服务器 `~/.ssh/authorized_keys` 删除对应行即可

---

## 5. 代码结构（`~/webrtc-camera`）

| 文件 | 职责 |
|---|---|
| `config.py` | **单一配置源**。环境变量 `DSH_*` > 文件 > 默认值。含设备/码率/信令/STUN/TURN/token 等全部配置项与辅助函数 |
| `webrtc_sender_Now_latency_probe.py` | **发送端主体**。GStreamer 管线、webrtcbin 手工协商、RTP 映射旁路、时钟同步应答、信令客户端 |
| `signaling_server.py` | **信令服务器**。token 鉴权（401）、角色感知转发、下发 TURN 凭据、**同端口提供静态页面** |
| `index_latency.html` | **观看页面 + 延迟面板**。含 PeerConnection、时钟同步、延迟计算、ICE 路径显示、事件日志 |
| `serve_page.py` | 旧的独立页面服务（8000），非必需 |
| `tools/` | `preflight.py` 环境预检、`restart_stack.sh` 重启栈、`long_test.mjs` 自动化采样、`analyze_long_test.py` 分析、`stage_timing.py`/`pts_diag.py` 分段计时、`verify_security.py`/`verify_relay.py` 验证、`stun_probe.py` NAT 类型探测 |
| `deploy/` | `setup-server.sh` **一键部署（幂等）**、`turnserver.conf.template`、`secrets.env.example`、`traffic_guard.py` 流量守卫、`kill-switch.sh` 应急开关、`README.md` |
| `docs/` | `TECHNICAL_NOTES.md`（1900+ 行技术笔记，§31.5 有光学交叉验证）、`PORTING.md` 移植指南、`WORKFLOW.md`、`SERVER_CHECKLIST.md` 服务器上手清单 |
| `attic/` | 11 个历史文件（保留作参考） |

---

## 6. 关键技术决策与理由

### 6.1 为什么用 webrtcbin 手工协商，而不是 webrtcsink
GStreamer 1.20.3 **没有 `webrtcsink`**（属 gst-plugins-rs，需自行编译 Rust）。因此用 `webrtcbin` 手工处理 offer/answer/ICE。

### 6.2 为什么页面与信令共用同一端口（8765）
保证**同源**，页面可自行推导信令地址，避免跨域与"半通"故障。

### 6.3 延迟测量的原理与诚实边界
- 发送端在**编码前**插入 `identity name=timestamp_probe` 取源端时间戳；
- 每帧的 RTP 时间戳与源端时钟映射通过 **WebSocket 旁路**发给浏览器；
- 浏览器按**顺序配对**（不是按 PTS —— `x264enc` 会重写 PTS），得到 `Source → Callback`；
- **误差棒 = 时钟同步 RTT / 2**；当误差 > 10ms 时面板**拒绝显示分段拆解**（宁可说"不知道"）。
- **⚠️ 测量覆盖范围**：从 V4L2 交付帧开始，**不包含摄像头内部延迟**。
  因此端到端 = 摄像头内部（约 130ms）+ 本测量（91.6ms）+ 显示（10~30ms）。

### 6.4 摄像头三种格式（`v4l2-ctl --list-formats-ext` 实测）

| 格式 | 特点 | 可用分辨率/帧率 |
|---|---|---|
| **MJPG**（当前使用）| 摄像头内部 JPEG 编码，**我们还要再解码+编码** ✗ | 最高 1920x1080@30 |
| **H264** ✓ | ★ **摄像头直出 H.264**，可省掉解码+编码两趟 ✗→✓ | 1920x1080@30（已验证可出流 ✓）|
| YUYV | 未压缩，延迟最低 | **受 USB 2.0（480Mbps）限制**：720p 只有 10fps ✗ |

### 6.5 编码器配置（当前）
```
x264enc tune=zerolatency speed-preset=ultrafast bitrate=4000 key-int-max=30
```
- `tune=zerolatency` **已隐含关闭 B 帧与前瞻** ✓（这一点曾经是优化项，现已满足）
- ⚠️ `key-int-max=30`（应为 1 秒）**实测未精确生效**，真实关键帧间隔约 **3 秒**（见 §9.3）

---

## 7. 实测性能基线（全部为真实测量，非估算）

| 场景 | 延迟 | 路径 | 其他 |
|---|---|---|---|
| **局域网** | **19.4 ms** | `host → prflx` P2P | 解码 29.0 fps，923 帧 0 丢失 |
| 局域网（另一次）| 45.2 ms | `host → prflx` P2P | — |
| **手机移动数据** ✓ | **91.6 ms** | **`relay → srflx` TURN** | 1920×1080 H264，收包 11793，丢包 2.44%，RTT 74ms |
| **端到端（玻璃到玻璃）** | **220 ~ 320 ms** | — | 与"摄像头 130ms + 软件 92ms + 显示"的推算吻合 |

**链路与设备指标**：
| 指标 | 值 |
|---|---|
| 摄像头交付帧率 | **30.47 fps** 满帧（598 样本，P05=29.29，低于 15fps 的样本 0 个）|
| 编码器输出帧率 | **29.87 ~ 30.00 fps** 满帧 |
| 发送端 CPU | **59.5%**（单路 1080p 软编，已是 ultrafast）|
| 静止画面码率 | **0.046 Mbps**（每帧 <200 字节）← 关键数据，见 §9.1 |
| 运动画面码率 | 3.96 Mbps（跑满设定值）|
| 时钟同步 RTT（经 ECS）| 49.76 ms（±24.9）← 隧道时代是 239ms（±120）|
| 流量费用（走 TURN 时）| 4 Mbps ≈ 1.8 GB/小时 ≈ **¥1.44/小时**；5 GB/天预算 ≈ 2.8 小时 |

---

## 8. ★ 试错记录（做过什么、为什么、结果）

### 8.1 成功的关键决策
| 决策 | 结果 |
|---|---|
| 先做 **TURN**、P2P 后置 | ✅ 正确 —— 移动网络下 P2P 实测打不通，TURN 兜住了 |
| 引入**服务器**替代隧道 | ✅ 决定性 —— clockRtt 从 239ms 降到 50ms，测量首次可信 |
| 页面与信令**同端口** | ✅ 消除了跨域类故障 |
| 密钥放 `/etc/dsh-camera/env`（仓库外）| ✅ 静态文件服务器即使配错也不会泄漏 |
| 部署脚本**幂等** | ✅ 重跑安全，轮换密钥只须重跑 |
| 每步改动打 **tag** | ✅ `known-good-*` 系列，可随时回退 |

### 8.2 失败与弯路（含我犯的错）
| # | 事件 | 教训 |
|---|---|---|
| 1 | 审查报告列 11 条问题，实际 **1 条是报告错误的**（L13 早已修好，行号偏了约 90 行）| **AI 的输出是线索，不是事实**；关键结论必须复核 |
| 2 | 云隧道 **cloudflared 失败**（API 超时）→ 改用 pinggy | 隧道方案有单点依赖，**生产必须自建服务器** |
| 3 | 抓隧道 URL 时**两次抓错**（抓到 `api.trycloudflare.com` / `dashboard.pinggy.io` 这类品牌/API 链接）| 从日志取 URL 要**用判据筛选**，不能取第一个 |
| 4 | 手机 WebSocket 连不上（`code=1006`），同一台笔记本连手机热点却成功 | 设备/浏览器特异性问题；**换系统浏览器可解** |
| 5 | ★ **我把 `rtp_mapping_queue` 从 list 改成 `deque` 后，仍保留切片 `d[:20]`** | **deque 不支持切片** → 每次 flush 抛 TypeError → 被 GLib 回调吞成一行日志 → **帧映射永远发不出去 → 延迟测量永远不可能工作**。`py_compile` **抓不到**这类运行时错误 |
| 6 | ★ **页面里 `const` 声明在使用之后**（TDZ）| 456 个采样中有 **452 个 ReferenceError**；只取 3-4 个稳定期采样**完全看不到** |
| 7 | 部署脚本用了**不存在的元数据字段** `public-ipv4`（正确是 `eipv4`），备用源 `api.ipify.org` 在国内拒连 | 脚本**在它专门针对的平台上死掉** |
| 8 | 部署脚本 apt 装的 `python3-websockets` 只有 **10.3**，而代码需 **≥13**（`websockets.asyncio`）| 信令服务起不来，唯一线索是一行 ImportError |
| 9 | 部署横幅**明文打印访问令牌** | 两套凭据泄漏，已轮换修复。**保密措施被自己的总结破坏** |
| 10 | 安全组的**四个坑**（详见 §8.3）| 逐条记录在 `docs/SERVER_CHECKLIST.md` |
| 11 | 在**云命令行 CloudShell** 里改配置（以为在改实例）| CloudShell 是**临时虚拟机**（1 小时销毁）；正确做法看提示符是否含实例 ID |
| 12 | ★ **我用 `videotestsrc pattern=smpte` 当作"静止画面"** | **它是全速率画面（40 秒 20MB = 4Mbps）** —— 真正的静止每秒不到 200 字节/帧。**我的复现实验无效**，因为验证源本身的工作没做 |
| 13 | ★ `jitterBufferTarget: 0` | 为降延迟而设，结果：延迟多出约 149ms 而卡顿**毫无改善** → **已回退**。教训：**优化项必须单独验证收益** |

### 8.3 安全组的四个坑（全部实际踩到）
1. **配好的安全组必须绑定实例** —— 判据：列表里 `IP 占用数` 列为 `0 (已用)` 就是没人用它，规则全白配。
2. **一键检测只认 5 个固定端口**（80/443/22/3389/8080），我们的 8765/3478/49152-65535 **必须用"自定义检测"**。
3. **自定义检测的协议必须与规则一致** —— 49152 是 UDP，用 TCP 测必然"未开通"。
4. **自定义检测的源地址要填单个 IP**（不能填 `0.0.0.0/0`）。

**另**：不要点「开通全部端口」（会白开 3389/8080）；多安全组时注意**同优先级下拒绝优先**。

---

## 9. ★ 已知问题与待办（按优先级）

### 9.1 🔴 主问题：静止时浏览器帧回调停止
**现象**（用户精确描述）：
- 保持运动 → 稳定 30fps，不卡 ✓
- **画面转为静止的那一刻 → 瞬间卡住** ✗
- 静止期间帧率在 **30 / 10 / 4 / 14** 之间循环，约 10 秒经历一遍 ✗
- **卡住后画面停在之前那一帧（干净、不花屏）** ← 关键特征
- **`Decoded frames/s` 与"延迟数字"同时冻结** ← 关键特征（二者同源，都来自浏览器帧回调）
- `Packets received` **一直在涨**（数据在到达 ✓），`Jitter` 稳定 20，丢包稳定 2.5%（后降到 0.8%），RTT 60-140 波动

**已排除**：
- ✗ 网络（包一直在到达）
- ✗ 抖动缓冲（`jitterBufferTarget` 从 0 改到 100 **无改善**，反而多 149ms，已回退）
- ✗ ICE 路径跳动（**运动时也会跳**，与卡顿不绑定）
- ✗ 编码器不产帧（真静止下实测仍按时产 30fps）

**已锁定**：问题在**浏览器收到包之后、呈现之前**（帧回调彻底停止）

**当前最佳假设**（**未验证，标记为假设**）：
静止时每帧 **<200 字节**，链路有稳定丢包 → 丢一个 P 帧导致**后续所有 P 帧缺少参考帧无法解码** → 浏览器冻结在最后可解帧 → 直到下一个 IDR（实测间隔约 3 秒，见 §9.3）才恢复 → 周期性卡顿。
（此假设可解释"干净旧画面"与"周期性"，但**尚未取得直接证据**。）

### 9.2 🔴 ICE 路径跳动
- 面板显示 `pairs: succeeded 2` → **同时存在两条可用路径** → ICE 持续打分择优切换。
- 跳动间隔不定（几秒 ~ 一分钟以上）。
- **结论：没有 API 能控制 ICE 的选路或切换周期**（检查节奏由 WebRTC 规范定时器决定）。
- **唯一可靠手段：只提供一类候选** → `iceTransportPolicy: "relay"`。
- ⚠️ 但存在**结构性障碍**，见 §10。

### 9.3 🟡 `key-int-max=30` 未精确生效
实测真实关键帧间隔约 **3 秒**（约 92 个 NAL），而配置为 30 帧（1 秒）。这直接放大了 §9.1 的恢复时间。

### 9.4 🟡 硬件编码未启用
- `/dev/dri/renderD128` **存在** ✓（核显已透传）
- 但 `vah264enc` / `vaapih264enc` / `qsvh264enc` **全部 not available** ✗（缺 `gstreamer1.0-vaapi` 等包）
- 若装上，CPU 可从 59.5% 降到接近 0；且**属性名跨版本不兼容**（技能有对照表），代码须做 `find_property` 防御。

### 9.5 🟡 多观看者尚未实现
- 现状：信令只把发送端内容转给**最新连接的 viewer**；第二个观看者会**静默接管**，第一个不会被踢但无法重新协商。
- 调研结论（**建议方案**）：单路采集 + 单次 x264enc → **在 `h264parse` 之后 `tee`**（**不是** `rtph264pay` 之后，因为 RTP 序列号必须每对端独立）→ 每 viewer 一条 `queue(leaky=2)` + 独立 `rtph264pay` + 独立 `webrtcbin`。
- 风险：软编 1080p30 上限约 4-6 路；PLI/FIR 关键帧请求风暴；上行带宽 = N × 码率。
- 难度评估：**medium**。

### 9.6 🟢 其他
- 页面本身**不校验 token**（任何人可打开页面看到空面板；但信令强制校验，拿不到画面）。可收紧，优先级低。
- `index_latency.html` 在有多条 succeeded 候选对时**显示的是候选汇总而非真实选中路径**（黄字），导致**看不到当前实际走哪条路**。
- 服务器日志有扫描器造成的 `InvalidMessage` traceback 噪音。
- 虚拟机网络是 **NAT**；技能明确指出 WebRTC 场景**应改桥接（Bridged）**。

---

## 10. ⏭️ 下一步：固定媒体路径为 TURN（详细交接）

### 10.1 目标
消除 ICE 在两条可用路径之间的跳动 —— 接受"放弃自动选优"，换取**可预测**。

### 10.2 ★ 必须先解决的结构性障碍

当前代码（`index_latency.html` 约 906 行）：
```javascript
const DSH_ICE_SERVERS = DSH_CONFIG.iceServers || [];   // 来自 config.js，通常只有 STUN

const pc = new RTCPeerConnection({        // ★ 页面加载时【立即创建】
    iceServers: DSH_ICE_SERVERS           // ★ 此刻【还没有 TURN 凭据】
});
```

而 TURN 凭据是**之后**通过 WebSocket 下发的。

**因此**：
- ❌ 直接加 `iceTransportPolicy: "relay"` → 创建时无 TURN 服务器 → **一个候选都收集不到 → 完全连不上**
- ❌ 事后 `setConfiguration` 补 → 规范禁止在首次 `setLocalDescription` 之后更改 `iceTransportPolicy`

### 10.3 正确做法
```
① 调整时序：先连信令 → 收到 turn-credentials → 【再创建 pc】
② 创建时带上完整 iceServers（STUN + TURN）+ iceTransportPolicy: "relay"
③ 加兜底：凭据迟迟不来或下发失败时【明确报错】，绝不静默失败
```

### 10.4 验证顺序
```
局域网自测（用宿主机的浏览器）→ 手机移动网络 → 观察面板 ICE path 是否稳定为 relay
```

### 10.5 分支与回退
- 分支已建好：**`ai/turn-only`**（从 `main = 6b34ea2` 创建）
- 改动前先确认四层同步；改完单独验证；成功后再合并 `--no-ff` 并打 tag

### 10.6 ⚠️ 代价（用户已知悉并同意）
固定走 TURN 后，**观看期间约 ¥1.44/小时**（4 Mbps）；流量守卫默认 5 GB/天 ≈ 2.8 小时会停 coturn。
若长期使用，建议**降低远程观看码率**（如 1.5 Mbps ≈ ¥0.54/小时，5GB 可撑约 7.4 小时）。

---

## 11. 工作纪律与工具约定（都是踩坑换来的）

### 11.1 绝对遵守
1. **绝不把含引号、括号、反引号的 shell 文本内联进 SSH 命令** —— 一律「写成脚本文件 → `scp` 到 `/tmp` → `ssh ... "bash /tmp/x.sh"`」。
2. **多行提交信息用 `git commit -F <文件>`**，不要用 `-m "多行"`（会被拆断）。打 tag 同理用 `-F`。
3. **绝不 `git reset --hard` / `git checkout .`**。分支用 `ai/<主题>`，合并用 `--no-ff`，成功后打 `known-good-YYYYMMDD-<主题>` tag。
4. **密钥不打印**。轮换后验证旧值失效。
5. **改动一处、测一处**。一次改多处会导致无法归因。
6. **删除/移动前先确认解析后的绝对路径**。

### 11.2 调试技巧
- 远程命令里 `$(...)` 会被**本地 PowerShell 先展开** —— 要传给远端就别用 `$()`。
- 从日志里取 URL / 关键值，**要用判据筛选**，不能取第一条。
- 长日志**先过滤再看**（本项目曾有 190 万行的日志把浏览器主线程拖死）。
- 想量某个指标，**先验证"测量工具本身是否可信"**（我用错的测试源浪费了一整轮）。

### 11.3 WorkBuddy 派活协议「大进小出」
- **只在用户明确要求时使用**。
- 只用 `deepseek-v4.1-flash`。
- **核心**：输入可以巨大（它读整个仓库都行），**输出必须极小**（那会进我的上下文并每轮重发）。
- **强制 schema 三件套，不设"正文"字段**：`{verdict ≤60字, evidence ≤8条带"文件:行号", open ≤3条}`。
- **它的输出是【线索】不是【事实】** —— 曾报错一次，关键结论必须自己复核。
- **我绝不把它返回的文字粘进回答**，否则省下的又花回去。

### 11.4 DSH 插件安装的两个坑
1. `pnpm` 会往 `~/.dsh/profiles/desktop/pnpm-workspace.yaml` 的 `allowBuilds` 插入占位符
   `xxx: set this to true or false` → 非交互调用直接失败。**解法：改成 `true`**（`@` 开头的键要带引号）。
2. 安装中途失败后，`dependencies` 已写入 → 再装会**跳过 bundles 登记** → **装了但不加载（静默失败）**。
   **解法：跑 `fix-dsh-plugin-bundles.ps1`**（`-Check` 会明确报 "MISSING from bundles"）。
   注意该脚本单向（依赖→bundles），卸载后残留条目需手工删。

---

## 12. 常用操作手册

### 12.1 查看当前状态
```bash
# 服务器
ssh dsh-ecs "systemctl is-active coturn dsh-signaling dsh-page dsh-traffic-guard"
ssh dsh-ecs "python3 ~/webrtc-camera/deploy/traffic_guard.py --status --budget-gb 5"

# 发送端是否在跑 / 是否在产帧
ssh dsh-vm "pgrep -af webrtc_sender; tail -3 /tmp/sender.log"

# 页面是否可达（从外网）
curl -s -o /dev/null -w '%{http_code}\n' http://101.200.129.193:8765/index_latency.html
```

### 12.2 重启发送端（指向 ECS）
```bash
ssh dsh-vm
cd ~/webrtc-camera
export DSH_ACCESS_TOKEN="$(cat /tmp/dsh-token)"
export DSH_SIGNALING_URL="ws://101.200.129.193:8765"
export DSH_VIDEO_DEVICE=/dev/v4l/by-id/usb-webcam_webcam_AN20200907002-video-index0
bash tools/restart_stack.sh
```

### 12.3 服务器重新部署（幂等）
```bash
ssh dsh-ecs
sudo bash /root/webrtc-camera/deploy/setup-server.sh
```

### 12.4 取访问网址给观看者
```bash
ssh dsh-ecs "cat /root/access-url.txt"
```

### 12.5 摄像头维护（**高频问题**）
```bash
ls -l /dev/v4l/by-id/     # 设备是否还在
v4l2-ctl --list-formats-ext --device <DEV>   # 支持的格式与帧率
```
- **摄像头掉线表现**：发送端 `exited (code 1) after 0s`，日志里有
  `!! <设备路径> does not exist`。修法：VMware 菜单 → **Removable Devices → 连接摄像头**；
  接回后**监控进程会在 30 秒内自动拉起发送端，无需人工干预** ✓
- **调试时注意**：`run_sender_loop.sh` 会自动重启发送端并**抢回摄像头**，
  所以做摄像头测试前必须**先停掉监控进程**。

---

## 13. 当前最重要的三件事（给接手的 AI）

1. **先读 §10** —— 固定 TURN 的结构性障碍（pc 创建早于 TURN 凭据）是下一步的核心。
2. **§9.1 是主线难题** —— 静止时帧回调停止。所有已排除项都写在里面，**不要再重复排除**。
3. **遵守 §11 的纪律** —— 尤其"脚本文件 + scp"和"改动一处测一处"，这两条能省掉大量返工。
