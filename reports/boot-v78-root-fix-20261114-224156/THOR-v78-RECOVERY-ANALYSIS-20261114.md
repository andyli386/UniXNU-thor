# Thor v78 根卷修复版真机启动分析

2026-11-14，北京时间。i386 在 22:23:14 记录到 Thor 离开 Fastboot，22:39:59 重新枚举为 Recovery。用户确认此次运行出现系统界面、未见进度条，触摸无响应。

## 采集和启动归属

22:41:56–22:46:06 从卸载状态的 SHNGROOT Data 窗口只读取得 8,591,003,648 字节。本次 Recovery UUID 为 d8b390f4-a4be-4c4f-85e4-c49ba38ec814。采集前后 userdata 及其映射的写入和丢弃计数一致。离线导出 311 个选定文件，共 17,030,645 字节，导出器报告零错误、零跳过。

当前 PongConsole.txt 共 15,698,914 字节，前 11,946,323 字节与已留存的 v77 原始文件逐字节一致。本轮分析取附加的 3,752,591 字节，排除边界处 45 字节的半行，归并旧采集器重复回放的完全相同行，保留原始行号。新增段有单一的 RAM 和 Darwin 启动标识，日志延续至内核时钟 973.559 秒。旧式日志没有内核 UUID，i386 缺少 22:23 的命令回执，本轮归属依据为前缀边界、同次现场时间和实际启动行为。

## 启动时间

同一内核时间域，单位为秒。

| 事件 | v77 | 本轮 | 本轮原始行 |
| --- | ---: | ---: | ---: |
| 根卷 UUID 匹配条件出现 | 26.521723 | 26.842190 | 88809 |
| 找到 BSD 根卷 disk4s1 | 26.642230 | 26.961793 | 88859 |
| APFS mountroot 进入 | — | 26.972599 | 88861 |
| SystemOS 只读 union 挂载完成 | — | 27.136857 | 88913 |
| Data 可写挂载 /private/var 通过 | — | 27.183358 | 88945 |
| XNU 请求装载 /sbin/launchd | 26.875808 | 27.184237 | 88946 |
| 第一条实测 launchd 启动任务日志 | 40.810602 | 41.164107 | 88989 |
| 第一次可见帧 | 164.982383 | 231.719470 | 95028 |

UUID 匹配到 BSD root 仅 0.120 秒，OS、SystemOS 和 Data 均正常进入预期挂载路径。首帧比 v77 晚 66.737 秒，其中 66.429 秒出现在 XNU 请求装载 launchd 至首帧这段。第一帧由 backboardd 软件路径提交（gpu=0）。本轮接管 665 MiB，v77 为 768 MiB，差 103 MiB。单次对照无法量化内存差异的影响。

SpringBoard 的进程日志另用客体时钟。v77 在 t57 提交主队列 +10 秒的 wake 回调，t159 进入回调，t160 记录主屏请求被接受。本轮在 t53 提交，t222 才进入，t224 记录主屏请求被接受。回调提交至进入扩长约 67 秒，回调体耗时约 2 秒。首帧和主屏请求由不同时间域记录，主屏请求为程序回执，实际界面以用户现场为准。后续 GPU 或缓存调优应有单变量对照，当前证据先定位到 SpringBoard 启动和主队列阶段。

本轮 PongBoot-SpringBoard.txt L230、L245 分别在客体时钟 t114、t177 采到 SpringBoard 主线程 TID 248。按同版 22H374 的归档符号表做精确地址匹配，两次都有 UIKit／FrontBoard 的 scene 建立栈，t177 还命中 SBEmbeddedDisplayWindowSceneDelegate 的窗口场景配置。当前日志未记录 dyld shared cache UUID，两点采样也无法证明持续阻塞或唯一原因。

PongDiag 记录两轮预读均完成 13 段相同范围，共 318.516 MiB。各段耗时之和由 v77 的 33.724 秒增至 64.837 秒，增加 31.113 秒。预读与 SpringBoard 启动并行，现有日志无法把这 31.113 秒直接计入首帧路径。可在恢复 768 MiB 内存交接的对照后，再对预读做独立开关试验。

## 迁移与进度条

旧版进度条已追到 DMMigrator::_createProgressWindow → PUIProgressWindow。本轮 lockdownd.log 原始 L188 在进程启动检查时报告 Data Migration Completed: true（客体时钟 62.766578 秒）。移动用户的 com.apple.migration.plist 与 v77 的 281 字节内容、APFS mtime 都相同，记录中仍是旧值 DMLastMigrationResults.success=false。本轮新增五个 PongDM 进程日志，只记录诊断注入，没有记录插件或客户端最终结果。导出的旧 ProgressUI 报告与 v77 一致，没有新增的进度窗口失败报告或迁移完成通知。现象与启动时已有迁移完成状态相符，缺少窗口调用的运行时回执，也没有新一轮成功结果。

## 触摸与后续验证

修复包实际设置 pong_touch=0，源码在进入 FTS 初始化前返回，本轮 Console 也没有 FTS 初始化记录。打包的触摸节点当前在 /pong-touch-fts，驱动在 /arm-io/pong-touch-fts 查找。触摸版应同时修正节点挂载、按 Thor 原厂 FTS 适配供电与协议并记录实读芯片身份、真实 FIFO 事件和 HID 投递。是否可用以手指点按、滑动为准。

i386 原始证据目录
/home/vincent/Projects/thor-unixnu/PongBoot-pull-v78-root-fix-20261114-224156/

主要文件
- extracted/files/logs/PongConsole.txt
- extracted/files/logs/lockdownd.log
- extracted/files/mobile/Library/Logs/PongSB-5.txt
- extracted/files/mobile/Library/Logs/PongBoot-SpringBoard.txt
- extracted/files/mobile/Library/Preferences/com.apple.migration.plist
- extraction.json、before.json、after.json、finish-capture-result.json
