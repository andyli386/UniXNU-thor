# Thor v232 首次上机

v232 于 2026-11-25 13:14 临时启动，约 4 分钟后在"再点设置"时定格。分析、v233 的内核取证说明和上机检查项都写在 `../v233-intake/ANALYSIS.md`（同一份文档覆盖 v232 的结果与定格的原因）。

- 设备日志全量快照：`thor-out/recovery-v232-full-20261125T053704Z`（只读，1.3 GB）
- 本版本启动的控制台：`../v233-intake/console-v232-boot.txt`
- 历史取证：`thor-out/recovery-v227-history-20261125T053934Z.nHzBKi`
- 定格前后的 durable 日志解码：`../v233-intake/journals/stall-{runningboardd,SpringBoard,backboardd}.txt`
