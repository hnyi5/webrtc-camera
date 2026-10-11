# 公网部署

把项目搬到一台公网 VPS 上：**coturn（STUN + TURN）+ 信令服务器 + 页面服务器**。

> 这一步同时给出 **P2P** 和 **TURN** 两条路 —— ICE 每次连接时自动先试 P2P，
> 失败才用 TURN。**不需要在两者之间做选择。**

---

## 0. 为什么要这么做

```
你的宽带处于双重 NAT 之后（路由器 WAN 是私有地址 10.20.18.184）
        ↓
「在家里做端口映射」这条路走不通
        ↓
信令服务器必须放在一个【双方都能主动连上】的公网地址上
```

**注意：只有「信令」需要公网地址。** 视频流仍然走 P2P，不经过服务器。

---

## 1. 买服务器

| 项目 | 选择 | 理由 |
|---|---|---|
| 产品 | 轻量应用服务器 / ECS | 2核2G 足够 |
| 地域 | **华北（北京）** | 离秦皇岛/石家庄最近，中继绕路最小 |
| 镜像 | **Ubuntu 22.04 LTS** | **和验证过的环境一致**，省掉移植风险 |
| 系统盘 | 20 GiB | 够（实际占用不到 8 GB） |
| 数据盘 | **不要** | 用不上 |
| 公网 IPv4 | 分配 | 必需 |
| 带宽计费 | **按使用流量** | P2P 成功时流量≈0，费用≈0 |
| 带宽峰值 | 5 Mbps | 够一路 4 Mbps 视频 |
| 付费 | 1 个月起 | 先试 |

**⚠️ 按流量计费没有硬上限** —— 一定要设**费用告警**（20/50/100 元三档）。

**⚠️ 发票**：先确认单位要什么（抬头、税号、专票还是普票），并确认账号的
**实名认证类型**能不能开出企业发票 —— **别买完才发现开不出来**。

---

## 2. 开防火墙（安全组）

购买向导只能勾几个常见端口，**自定义端口要买完之后到控制台加**：

```
控制台 → 云服务器 ECS → 网络与安全 → 安全组 → 配置规则 → 入方向 → 手动添加
```

| 协议 | 端口范围 | 授权对象 | 用途 |
|---|---|---|---|
| 自定义 TCP | `22/22` | `0.0.0.0/0` | SSH |
| 自定义 TCP | `8765/8765` | `0.0.0.0/0` | 信令 |
| 自定义 UDP | `3478/3478` | `0.0.0.0/0` | STUN / TURN |
| 自定义 TCP | `3478/3478` | `0.0.0.0/0` | STUN / TURN |
| 自定义 UDP | **`49152/65535`** | `0.0.0.0/0` | **中继端口段** |
| 自定义 TCP | `8000/8000` | `0.0.0.0/0` | 页面 |
| 自定义 TCP | `443/443` | `0.0.0.0/0` | TURN over TLS（可选） |

> **中继端口段是最容易漏的一条。** 漏了的现象是：客户端**能拿到 `relay` 候选**，
> 但连接**永远建立不起来**，而且没有任何错误 —— 极难排查。

---

## 3. 放密钥（**故意放在仓库外面**）

```bash
sudo install -d -m 700 /etc/dsh-camera
sudo cp deploy/secrets.env.example /etc/dsh-camera/env
sudo chmod 600 /etc/dsh-camera/env
sudo nano /etc/dsh-camera/env        # 填两个密钥
```

两个密钥用下面命令生成：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**为什么放 `/etc` 而不是仓库里**：仓库目录会被页面服务器读到。密钥放在仓库外，
**任何一次"静态文件服务器配错"都不可能泄露它**。`.gitignore` 里也额外挡了一层。

---

## 4. 部署

```bash
cd ~/webrtc-camera
sudo bash deploy/setup-server.sh
```

脚本会：

1. 检测公网/私有 IP（阿里云用实例元数据服务，不需要出网）
2. 装 `coturn` + `python3-websockets`
3. 用模板渲染 `/etc/turnserver.conf`（**含 `external-ip`** —— 少了它 relay 候选全是坏的）
4. 启用并启动 coturn
5. 安装两个 systemd 服务：`dsh-signaling`、`dsh-page`
6. 打印页面地址（**带 token**）和验证清单

**可重复执行**：改了密钥或模板再跑一次即可。

---

## 5. 验证

### 5.1 服务在跑吗

```bash
systemctl status coturn dsh-signaling dsh-page
ss -tulnp | grep -E ':(3478|8765|8000|443)\b'
```

### 5.2 ⭐ TURN 真的能用吗（**最关键的一步**）

打开：

```
https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/
```

```
① 填 STUN or TURN URI：
     turn:<公网IP>:3478?transport=udp
   TURN username / password：【留空】← 我们用短时凭据，这里先测端口通不通
② 把「IceTransports value」从 all 改成 relay
③ 点「招募候选人」
④ 看有没有 relay 类型的候选
```

**另一种验证方式**（更能说明问题）：用 `tools/stun_probe.ps1` 确认服务器可达，
再用页面实际连一次，看面板的 `ICE path` 是不是 `relay`。

### 5.3 信令鉴权生效了吗

```bash
# 不带 token 应该被拒绝
python3 - <<'PY'
import asyncio, websockets
async def main():
    try:
        async with websockets.connect("ws://127.0.0.1:8765/?token=wrong") as ws:
            await ws.recv()
            print("BAD: accepted a wrong token")
    except Exception as e:
        print("OK: rejected ->", type(e).__name__)
asyncio.run(main())
PY
```

### 5.4 端到端（**不需要摄像头**）

```bash
# 在这台机器上（先停掉本机的信令，避免端口冲突）
DSH_VIDEO_SOURCE=videotestsrc \
DSH_SIGNALING_URL=ws://<公网IP>:8765 \
DSH_ACCESS_TOKEN=<token> \
    ./run_sender_loop.sh
```

然后远端浏览器打开：

```
http://<公网IP>:8000/index_latency.html?token=<token>
```

**看面板的 `ICE path` 那一行：**

```
host -> prflx  P2P        ← 打洞成功，媒体直连，服务器只传了信令
relay -> host  TURN/udp   ← 打洞失败，媒体经服务器中继
```

---

## 6. 运维与排错

| 症状 | 原因 |
|---|---|
| 客户端拿到 `relay` 候选但连不上 | **中继端口段没开**（49152-65535/UDP） |
| relay 候选的地址是 `10.x.x.x` | `/etc/turnserver.conf` 里 **`external-ip` 没配或配错** |
| 信令立刻断开 | token 不对，或服务器没设 `DSH_ACCESS_TOKEN` |
| 页面打不开 | 8000 没开，或 `dsh-page` 没起来 |
| TURN 能用但很慢 | `max-bps` 限得太低（模板里是 750000，**单位是字节/秒**，部署时实测确认） |
| 流量费暴涨 | 看 `/var/log/turnserver.log`；检查 `total-quota` / `user-quota` |

**看日志：**

```bash
journalctl -u dsh-signaling -f
journalctl -u dsh-page -f
tail -f /var/log/turnserver.log
```

---

## 7. 安全清单（上线前逐条确认）

| # | 项 | 状态 |
|---|---|---|
| 1 | `DSH_ACCESS_TOKEN` 已设（非空） | setup 脚本会拒绝空值 |
| 2 | `DSH_TURN_SECRET` 已设（非空） | 同上 |
| 3 | coturn 用 `use-auth-secret`，**没有 `no-auth`** | 模板已内置 |
| 4 | `max-bps` / `total-quota` / `user-quota` 已设 | 模板已内置 |
| 5 | `denied-peer-ip` 封了私有网段 | 模板已内置 |
| 6 | **SSH 只允许密钥登录，禁用密码** | ⬅ 手动：`/etc/ssh/sshd_config` 里 `PasswordAuthentication no` |
| 7 | 费用告警已设 | ⬅ 手动：控制台 |
| 8 | 密钥不在仓库里 | `/etc/dsh-camera/env` |
| 9 | 页面不含任何长期密钥 | 短时凭据通过信令下发 |

---

## 8. 还差一步（下一步的工作）

**短时 TURN 凭据的发放端点还没接。** 现在的状态是：

- ✅ **服务器侧已经安全** —— coturn 要求 `use-auth-secret`，没有凭据就用不了中继
- ⏳ 浏览器/发送端**还拿不到**凭据，所以 TURN 暂时还不能实际使用

**下一步**：在页面服务器上加一个 `/turn-credentials?token=...` 端点
（`config.turn_credentials()` 已经写好并测试过），让客户端用它换取短时凭据。

**在那之前，P2P 已经完全可用** —— 它不需要任何凭据。
