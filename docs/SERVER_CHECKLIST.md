# 阿里云服务器上手清单

> 面向：一台阿里云 ECS（Ubuntu 22.04，按流量计费），用来跑**信令 + 页面 + coturn(TURN) + 流量守卫**。
> 摄像头那台机器（发送端）**不在这台服务器上** —— 它在本地，见第 4 节。
>
> **本清单已逐条核对过仓库里的实际文件**（每条都写明依据的文件与行号）。
> 带 ⚠️ 的是**会让你卡住或花钱**的地方。

---

## 0. 买之前（先决条件）

- [ ] **发票与实名**：确认账号实名认证类型能开你要的发票（单位抬头 / 税号 / 专票还是普票）
- [ ] **选型**（这些值是按本项目一路 4 Mbps 视频算的）：

| 项 | 值 | 为什么 |
|---|---|---|
| 地域 | **华北2（北京）** | 离使用地近，RTT 低；时钟同步往返低，延迟测量才有意义 |
| 镜像 | **Ubuntu 22.04 LTS** | 与开发机一致，脚本按这个写的 |
| 规格 | **2 核 2 G** | 只跑信令 + TURN，不跑编码 |
| 系统盘 | **20 GiB**（或更小） | 不买数据盘 |
| 公网 IP | **分配 IPv4** | 必须 |
| 带宽计费 | **按使用流量** ⚠️ | 没有金额上限，见 §4 |
| 带宽峰值 | **5 Mbps** | 够一路 4 Mbps |
| 时长 | 1 个月起 | 学生券可买一年 |

---

## 1. 控制台操作（**只能你做**）

- [ ] **费用告警**：设 20 / 50 / 100 元三档
      ⚠️ 告警**只能提醒不能止损** —— 按流量计费没有金额上限，等看到告警时钱已经在花
- [ ] **安全组入方向**（★ 最关键的一步）：

| 端口 | 协议 | 用途 | 漏了会怎样 |
|---|---|---|---|
| 22 | TCP | SSH | 连不上服务器 |
| **8765** | TCP | **页面 + 信令**（同一个端口，见下） | 谁都打不开页面 |
| **3478** | UDP + TCP | STUN / TURN | 中继不可用 |
| **49152-65535** | **UDP** | **TURN 中继端口段** ⚠️ | **客户端能拿到 relay 候选，但连接永远建不起来，而且没有任何报错** ✗ |
| 8000 | TCP | 旧的双端口模式（可选） | 不影响单端口模式 |
| 443 | TCP | TURN over TLS（可选） | 敌对网络下备用 |

      **为什么 8000 是可选的**：`signaling_server.py` 现在**一个端口同时提供页面和 WebSocket** ✓
      （见 `signaling_server.py` 的 `STATIC_FILES` / `static_response`）—— 好处是**页面和信令同源**，
      不会出现"页面能开但信令连不上"这类半通故障。本清单后续都用 **8765 单端口**。

- [ ] **SSH 密钥**：生成并绑定密钥对；之后在服务器上设 `PasswordAuthentication no` 并重启 sshd

---

## 2. 服务器上（已脚本化，照着敲）

```bash
# ① 放仓库
git clone <仓库地址> ~/webrtc-camera && cd ~/webrtc-camera

# ② 预检（逐项报告缺什么，别盲装）
python3 tools/preflight.py

# ③ 生成两个随机密钥 —— 各执行一次，记下两个值
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# ④ 把密钥放到【仓库外】（路径不能写错，见下方提醒）
sudo install -d -m 700 /etc/dsh-camera
sudo install -m 600 deploy/secrets.env.example /etc/dsh-camera/env
sudo $EDITOR /etc/dsh-camera/env      # 填 DSH_ACCESS_TOKEN 与 DSH_TURN_SECRET

# ⑤ 部署（装 coturn、渲染 turnserver.conf、装 4 个 systemd 单元）
sudo bash deploy/setup-server.sh

# ⑥ 确认四个服务都 active（脚本自己不通过会打印 NOT DEPLOYED 并非零退出）
systemctl status coturn dsh-signaling dsh-page dsh-traffic-guard
ss -tulnp | grep -E ':(3478|8765|8000)\b'
```

### ✅ 已核对的事实（所以你可以放心照做）

| 事实 | 依据 |
|---|---|
| 脚本会**加载**密钥文件 | `setup-server.sh:47-49` `set -a` + `. "$ENV_FILE"` |
| 密钥**必须放 `/etc/dsh-camera/env`** ⚠️ | `setup-server.sh:34` `ENV_FILE="/etc/dsh-camera/env"` |
| 文件不存在会**直接退出** | `setup-server.sh:41` `die "missing $ENV_FILE"` |
| 密钥为空会**拒绝部署** | `setup-server.sh:52-53` 两处 `die` |
| systemd 服务也拿到密钥 | `setup-server.sh:133/151/169` `EnvironmentFile=$ENV_FILE` |
| 信令与页面**非 root** 运行 | `:137/:155` `User=$RUN_USER`（= `SUDO_USER`，所以**要用普通用户 sudo**） |
| 流量守卫**以 root** 运行 | `:174` `User=root`（它需要停 coturn） |
| 密钥文件不会进 git | `.gitignore:24` |

> **本次核对顺手修掉的一个坑** ✗：`deploy/secrets.env.example` 的注释原本写"复制到 `deploy/secrets.env`"
> —— **和脚本要的路径不一致**，照做会在第 41 行撞上 `missing /etc/dsh-camera/env` 然后退出。
> 注释已改为 `/etc/dsh-camera/env`。

---

## 3. 部署后验证（照做就能确认，别猜）

- [ ] **TURN 真的可用**：打开 trickle-ice 页面 →
      URI 填 `turn:<公网IP>:3478?transport=udp`、用户名密码留空 →
      **IceTransports 从 `all` 改成 `relay`** → 点 Gather →
      **看有没有 relay 候选** ✓（没有就是端口段或 `external-ip` 错了）
- [ ] **信令鉴权生效**：连 `ws://127.0.0.1:8765/?token=wrong`，应当**被拒**（脚本 `verify_security.py` 也是这套）
- [ ] **端到端跑一次**（**不需要摄像头**）：
      `DSH_VIDEO_SOURCE=videotestsrc` 起发送端，浏览器开
      `http://<公网IP>:8765/index_latency.html?token=<token>`，看面板 `ICE path`

---

## 4. 三个必须记住的坑 ⚠️

1. **安全组的 `49152-65535/udp`** —— 漏了的表现是"**能拿到 relay 候选但永远连不上，且无任何报错**"，
   是最难排查的一种。
2. **`external-ip`** —— 阿里云的公网 IP 是 **1:1 NAT 到私网网卡**的，所以 coturn 必须写
   `external-ip=<公网IP>/<私网IP>`。脚本会渲染，**不要手改 `/etc/turnserver.conf`**，要改就改模板再重跑脚本。
3. **费用告警不止损** ✗ —— 按流量计费没有上限。真正的止损只有三招（见 §5），
   以及流量守卫（`DSH_DAILY_TX_BUDGET_GB`，默认 5 GB/天，超了自动 `systemctl stop coturn`）。

### 关于费用（按当前 4 Mbps 码率估算）

```
TURN 中继时：4 Mbps 出方向 = 约 1.8 GB/小时 = 约 ¥1.44/小时（¥0.80/GB）
  → 5 GB/天 的预算 ≈ 2.8 小时中继
P2P 成功时： 中继流量 = 0，只有信令（可以忽略）→ 费用 ≈ 0
```

**所以"尽量走 P2P"不只是性能问题，是钱的问题** ✓ —— 而当前实测：**移动网络下打洞失败**（`srflx 3` 但 `pairs: none`），
所以**真上线时大概率会走 TURN**。可以考虑给远程观看单独降码率。

### 发送端在哪

**不在这台 ECS 上** ✗ —— 它需要摄像头和 GStreamer，跑在摄像头所在的那台机器上
（依赖见 `docs/PORTING.md` §2）。ECS 只跑：信令 + 页面 + coturn + 流量守卫。

---

## 5. 应急（**手机就能做**）

**第一优先：阿里云控制台三招**（不用 SSH、不用在电脑前）
1. 带宽峰值降到 **1 Mbps**
2. 从安全组**删掉 TURN 相关规则**
3. **停止实例**

**第二：服务器上的紧急开关**
```bash
sudo bash deploy/kill-switch.sh status    # 看当前状态
sudo bash deploy/kill-switch.sh relay     # 停中继（P2P 不受影响）
sudo bash deploy/kill-switch.sh inbound   # 阻断入站
sudo bash deploy/kill-switch.sh restore   # 恢复
```
⚠️ `kill-switch.sh inbound` 加的 iptables 规则**重启后失效** —— 它是紧急按钮，不是配置；
真正的修复要去安全组做。

---

## 6. 还需要确认的两点（我没验证到）

- [ ] `deploy/turnserver.conf.template` 里有 `tls-listening-port=443`，但**模板里没有任何 `cert=`/`key=` 行**
      —— 不配证书时 coturn 在 443 上的行为**未验证**。443 在清单里标为可选，敌对网络才需要。
- [ ] `max-bps=750000` 的单位是**字节/秒**（约 6 Mbps）。模板注释要求部署时实测；
      **如果流被限速，第一个就调它**。

---

*本清单由 DSH 主 agent 核对 `deploy/` 与 `docs/` 下实际文件后整理；条目中的文件与行号均为实际读到的内容。*
