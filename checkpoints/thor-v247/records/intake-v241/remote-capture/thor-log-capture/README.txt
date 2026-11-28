Thor 日志抓取包（v241，两个镜像通用）

用途：在另一台电脑上把手机里 Thor（UniXNU 移植）的日志只读导出来，打包后发回。
全程不写入手机：TWRP 只在内存里启动，日志分区按原始块读取。

需要：macOS 或 Linux；fastboot 和 adb 在 PATH 里（Android platform-tools）；python3（3.9 以上，只用标准库）。

步骤
1. 手机关机，按住 音量下 + 电源键 进入 fastboot。
2. 在终端里进入本目录，执行：

     bash capture.sh

   脚本会：等 USB 链路稳定（最多约 8 分钟，刚进 fastboot 时链路会抖动）→
   内存启动 TWRP（boot-twrp.img，不刷入）→ 只读导出
   /private/var/logs（内核控制台镜像、PongConsole、CrashReporter 报告）和
   /private/var/mobile/Library/Logs → 回到 fastboot。
   全程约 4–5 分钟。
3. 导出结果是一个目录和一个压缩包：

     logs-thor-v241-<时间>.tgz

   把这个 .tgz 发回来即可（通常几十 MB）。
4. 脚本结束后手机在 fastboot；想进系统就执行 fastboot reboot（boot_a 里是当前刷入的 v241）。

如果出错
- "fastboot-not-answering" / "link-never-steady"：拔插一次数据线，等 30 秒再运行一次。
- 卡在 TWRP 界面没有回到 fastboot：执行 adb reboot bootloader。
- 脚本不会刷写任何分区；boot_a 里的镜像不变。

文件
  capture.sh        抓取脚本
  boot-twrp.img     TWRP 镜像（只在内存启动）
  fbprobe           fastboot 探测小工具（macOS arm64）
  full-snapshot.py  只读导出工具
  tools/apfs/       APFS 只读解析库（apfs.py, dmg.py）

v241 新增的日志
- /private/var/logs/PongWifid-v241.txt：Wi-Fi 守护进程 wifid 自己的日志（只有 v241-wifi 镜像会有；
  每次启动保留前 120 秒）。
