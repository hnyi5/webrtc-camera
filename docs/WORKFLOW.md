# 代码管理与推进规范

> 目的：任何一次改动——不管是人、AI 还是脚本——都不允许把已有进展变成**不可恢复**的状态。
> 适用范围：本项目全部代码。任何 AI 接手本仓库时，**先读这份文件再动手**。

## 0. 三层防线

| 层 | 位置 | 防的是什么 | 怎么更新 |
|---|---|---|---|
| ① 本地版本库 | 虚拟机 `~/webrtc-camera/.git` | 改错、改坏、想回到上一版 | 每次改动前后 `git commit` |
| ② 主机镜像 | Windows `F:\视频传输协议项目\webrtc-camera.git` | 虚拟机磁盘损坏、guest 起不来 | 在 Windows 上 `git fetch --all` |
| ③ GitHub 私有仓库 | `github.com/hnyi5/webrtc-camera` | 整机丢失、硬盘故障 | `git push` |

②③ 都是**镜像/裸仓库**，不是工作面。**只有虚拟机里的 `~/webrtc-camera` 是可以改代码的地方。**

## 1. 铁律

1. **`main` 分支永远保持"已知能跑"**。禁止直接在 `main` 上做实验性修改。
2. 任何 AI 改动、任何实验，**先开分支**：`ai/<主题>` 或 `exp/<主题>`。
3. **改动前先 commit 一次当前状态**（哪怕是脏的）。先有回退点，再动手。
4. **禁止**未经确认执行：`git push --force`、`git reset --hard`、`git clean -fd`、
   `git branch -D`、`git rebase`。这些操作会**不可逆地**丢东西。
5. 每取得一个"确实能跑"的进展，**打一个 tag**：`known-good-YYYYMMDD-<说明>`。
6. 提交信息写**为什么**，不是只写"改了什么"。

## 2. 日常工作流

```bash
cd ~/webrtc-camera

git status                      # 1. 先看现在是什么状态
git checkout -b exp/rtp-mapping # 2. 开实验分支（名字说明你在试什么）

# ... 改代码、跑实验 ...

git diff                        # 3. 提交前一定看一眼改了什么
git add -A
git commit -m "为什么改这个"

# 实验成功：
git checkout main
git merge exp/rtp-mapping       # 4. 合回 main
git tag -a known-good-20261007-rtp-match -m "RTP timestamp 匹配率 >95%"
git push origin main --tags     # 5. 推到 GitHub

# 实验失败：
git checkout main               # 直接扔掉分支，main 毫发无损
git branch -D exp/rtp-mapping
```

**关键认知**：分支让"失败的实验"变成零成本。你不需要在动手前想清楚对不对——
只要在分支里做，最坏结果是丢掉这个分支，`main` 上的进展一点都不受影响。

## 3. 给 AI 的会话检查清单

**开工前**
- [ ] `git status` —— 工作区是否干净？有未提交的改动就先提交
- [ ] `git log --oneline -10` —— 现在在哪个提交上
- [ ] 确认要动的文件属于哪个功能，必要时 `git checkout -b ai/<主题>`

**收工前**
- [ ] `git diff` 看过每一个改动
- [ ] 提交信息说明动机
- [ ] 如果达成了新能力：打 `known-good-*` tag
- [ ] 推送到 GitHub，并刷新主机镜像

**绝对不要做**
- 不要为了让改动"生效"而 `git reset --hard` / `git checkout .`
- 不要删除 `docs/TECHNICAL_NOTES.md`（它是跨 AI 接手的上下文）
- 不要在 `main` 上直接覆盖 `webrtc_sender*.py` 而不留分支

## 4. 回退手册（出事了怎么做）

| 情况 | 命令 |
|---|---|
| 文件被改坏了，还没 commit | `git checkout -- <文件>`（丢弃该文件的未提交改动） |
| 已经 commit 但想撤销这一次提交 | `git revert <commit>`（生成一个反向提交，历史保留） |
| 想看看某个基线时文件长什么样 | `git show baseline-2026-09-23:webrtc_sender.py` |
| 想整体回到基线 | `git checkout -b rescue baseline-2026-09-23` |
| 想对比两个版本 | `git diff baseline-2026-09-23 main -- webrtc_sender.py` |
| 虚拟机彻底起不来 | 在 Windows 上：`git clone F:\视频传输协议项目\webrtc-camera.git 恢复的代码` |

## 5. 命令速查

```bash
git status                 # 现在有什么改动
git diff                   # 改了什么内容
git log --oneline --graph  # 历史长什么样
git add -A                 # 把所有改动放进暂存区
git commit -m "说明"       # 存成一个版本
git branch                 # 有哪些分支
git checkout -b 新分支      # 新建并切到新分支
git checkout main          # 切回 main
git merge 分支             # 把分支合进来
git tag                    # 有哪些里程碑
```

## 6. 文件约定

- `.gitignore` 排除了 `__pycache__/`、`*.pyc`、`*.mp4`、`*.h264`、`*.log`。
  这些是**实验产物**，不是源码，不进版本库。如果你有必须保存的样片，在
  `.gitignore` 里加 `!文件名.mp4` 例外。
- `docs/TECHNICAL_NOTES.md` 是项目技术文档，**与代码同库同版本**，一起演进。
