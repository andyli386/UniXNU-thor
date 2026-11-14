THOR v73 / capture 20261114-025335

先读 THOR-v73-025335-REPORT.txt。
analysis/native-waits.txt 提供本轮原生场景连接栈与迁移/服务状态。
analysis/v73-source-review.txt 提供两个确定诊断缺陷及真实编译来源。
analysis/upstream-v73-review.md 提供上游记录、适用条件和本轮修订后的优先级。
analysis/v73-memory-latency.txt 提供当前内存压力与v72等窗口比较。

extracted/files 为只读导出的日志/状态文件。
extracted/inventory.jsonl 为Data卷全树metadata；路径相对于/private/var。
extracted/manifest.json 为原始导出清单，列出76文件及两库UUID。

便携ZIP有意不带：
- 8,591,003,648字节 Data-runtime.img（仅留i386原始采集目录）。
- pong/libpong-ui18.dylib 与 pong/libpong-ui.dylib（主机原始证据目录仍保留）。
- 中间传输ZIP/base64及完整外网文档工具回包（公开来源改列upstream-check-index.json）。

因此，ZIP内日志文件数不等于manifest中含二进制的76项；这不是采集遗漏。
隐藏的原生IPS已纳入。两份IPS本身截断，分析只使用完整前缀/子对象。
Console带ring回放和WRAP lost text，统计已去完整相同行；保留原始日志不清理不改写。
时间域在各分析文件分别注明，非规范七位小数时间戳未擅自重写。

i386原始目录：
/home/vincent/Projects/thor-unixnu/PongBoot-pull-20261114-025335

Mac开发目录中的报告及日志镜像：
/Users/<user>/projects/thor-unixnu1030/UniXNU-pong/reports/black-screen-20261114-025335

direct-export源码与编译/运行记录随采集保存；新只读读取器已与旧v72的Console、库、IPS逐字节交叉验证。
本轮未更改系统源码/固件，未刷机/重启，未改变手机存储。
