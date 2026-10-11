# attic

开发过程中的**早期版本与实验脚本**。保留是为了能看清演进过程，
**但它们不是当前在用的代码**，不要照着改、也不要部署。

当前在用的东西在仓库根目录、`tools/` 和 `deploy/` 下。
哪个是当前的，看 `docs/TECHNICAL_NOTES.md` 和 `README`（如果有）。

| 文件 | 说明 |
|---|---|
| `webrtc_sender.py` | 最早的单文件发送端，MJPEG 路线 |
| `webrtc_sender_direct.py` | 尝试原生 H.264 直通的版本 |
| `webrtc_sender_jpeg_latency.py` | 早期 JPEG 延迟测量版 |
| `webrtc_latency_test.py` | 最早的延迟测试（H.264 路线） |
| `webrtc_latency_test_jpeg.py` | 同上，MJPEG 路线 |
| `latency_jpeg_source.py` | 只跑采集 + 编码，不含 WebRTC，用于隔离测量 |
| `latency_overlay_test.py` | 用 textoverlay 把时间戳烧进画面 —— 叠加层路线 |
| `test_dynamic_overlay.py` | 动态叠加实验 |
| `text.py` | 小工具脚本 |
| `gst_api_test.py` | GStreamer API 试验 |
| `index_orgin.html` | 页面的早期版本（叠加层时代） |

## 为什么移到这里

原来它们和当前文件混在根目录里，很难一眼看出哪个是能跑的。
移动（而不是删除）是因为它们记录了"为什么最终选了现在这条路" ——
`docs/TECHNICAL_NOTES.md` 里的历史章节经常引用到这段过程。

Git 历史上它们仍然在原路径下可查：

```bash
git log --follow attic/webrtc_sender.py
git show baseline-2026-09-23:webrtc_sender.py
```
