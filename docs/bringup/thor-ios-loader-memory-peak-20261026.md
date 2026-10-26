# iOS 16 加载器内存峰值审计（2026-10-26，主机只读）

本记录为后续完整iOS加载路径的源码依据；未修改loader阈值、内存授权或MEM03范围。

- DarwinBoot22/Driver/WindowSize.h:7的256MiB是parser bound，不是已实测的XNU固有最小RAM；当前代码不能通过单独调低此阈值解决124MiB容量限制。
- 实际APFS fullclosure内核SHA a798cf368ab013e7389f3c5944cea9381859fb98238b346b33423b00bf8d3a37：文件50,659,328B（48.3125MiB），VA跨度113,229,824B（107.984375MiB）。
- DarwinBoot.c:599–609布局还包括对齐后的boot_args、ADT、ramdisk、trustcache与trampoline，最后保留2MiB。即极小非空ADT、无ramdisk/trustcache，Used下界113,250,304B；按2MiB窗口粒度至少112MiB，真实输入可能更大，尚未证明XNU初始化预算足够。
- 当前F1e ThorBootMemoryDxe.c:196–205会把整个56MiB FAT复制到同124MiB DXE heap并作为EfiReservedMemoryType保留。DarwinBoot.c:552 ReadFile又分配48.3125MiB整份Image；:597再AllocateWindow，Image到:646才释放。
- 即把window降至112MiB，下界峰值仍56+48.3125+112=216.3125MiB；只去掉Image暂存仍56+112=168MiB，均超过124MiB，尚未计DXE自身或实际ADT等。当前256MiB窗口的峰值更高。
- 正确研究方向应同时覆盖：有界Mach-O header/load-command解析后按segment直接读入已拥有window；FAT只读源的所有权/生命周期或卸载BlockIO后的安全释放；短读、越界、重叠、源失效、失败清理和memory-map变化故障测试。不得读途中释放FAT源。
- 当前native_end=fffffff007fd0000，prelink固定B000000，留有48.1875MiB空洞（audit-ios16-storage-abi-thor-host.py:171）。压紧须重做storage ABI、原始组件slide和12972处指针重绑定、链接/重叠/relocation验收，不能直接搬动现候选字节。
- Kernel.Base低21位为0x4000，AllocateWindow:453要求物理起点F8004000，不能按F8000000计算可用窗口；旧memory_gate默认offset0只给静态上界。后续需实际Mach-O偏移+新GetMemoryMap共同预算。
- Entry.S只清cache/关MMU并跳入；XNU arm_vm_init.c:1944从topOfKernelData继续分配，:2273调用pmap_bootstrap。112MiB窗口余下约3.996MiB不能当成已足够VM/iOS。

下一阶段应以真实峰值预算和输入生命周期为验收项，再规划独立主机实现及新L3；MEM03仍先验证315页修复和同次FDT详情。
