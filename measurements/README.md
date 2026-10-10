# measurements

长测的原始数据与分析输出。放进版本库的理由：项目目标是「可重复测量」，
**结论必须能追溯到数据**。

约定：

- 文件名 `YYYY-MM-DD-<说明>.jsonl`（原始采样）配同名 `.txt`（分析输出）
- 采集期间**不要用那个浏览器窗口做别的事**：浏览器主线程的负载会直接
  影响 `Receive → Callback`（见 §29、§30）

```bash
# 采集（Windows 侧，需要 Chrome 带 --remote-debugging-port=9222）
node tools/long_test.mjs 3600000 5000 measurements/soak.jsonl

# 分析
python tools/analyze_long_test.py measurements/soak.jsonl
```
