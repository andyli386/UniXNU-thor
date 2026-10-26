# VM-MEM03：L2 242项通过，等待录像后单次L3（2026-10-26）

## 当前可执行边界

状态：PASS_L2_242_READY_AWAITING_USER_VIDEO_NOT_EXECUTED。
手机连接Mac mini。最终只读7/7正常；本轮零ADB重启、零Fastboot命令，L3目录不存在，镜像未消费。
用户要求在真机验证前停等录像，因此只准备并封存控制器，没有执行。收到用户本轮明确“录像开始”以后，才可使用该控制器单次临时启动。

## 已修复与新增验收

- bootstrap低地址identity与grid消费者统一为315页，共享范围契约，保留属性、边界、全零检查和原生TTBR0退役前后边界。
- 原安全90页内新增S/P/A/R故障显示；S为十六进制阶段，P为十六进制页号（FFFF表示非页检查），A为PA，R为立即保存的PAR；非页检查A/R为0表示未采样。
- 阶段：0001 post-TTBR1；0002 high VBAR；0003 root-ready；0004 framebuffer一般守卫；0041旧20页identity；0042新315页identity；0043旧20页high alias；0044新315页high alias。
- 诊断入口先确认kva_active=0及原TTBR0，不能安全写屏时直接stackless reset；退役后路径不调用低VA诊断。
- FDT同次快照保留UN名称/phandle/offset、属性存在性/status/no-map、每窗DYW、S状态文字、CRC/PAD、UNKNOWN及截断标识；最终清屏后重绘，UEFI仍不进入y0..319。
- 严格FDT解析器、读取许可及内存映射策略逐字节不变；未授权新256MiB内存，没有开放pmap或完整VM返回。
- 独立L2：242/242 PASS。Mac重新执行实际bootstrap/PTE审计与FV/FFS/LZMA/PE审计，379个输入、20模块、96lib原始字节及整份结构语义匹配。
- XNU 23回归、9类producer/PTE负例、原9语义变异与243 CFG通过；两次真实链接一致。UEFI34项（含12项C/libfdt）在i386和Mac真实回放通过，两次强制链接FD/PE一致。
- 打包31回归、独立gate39回归、控制器17回归通过；固件1正例+5负例实际通过。
- 额外5项CLI注入被连接器以Command not allowed/INVALID_ARGUMENT拒绝，未执行、未计PASS，未改写命令重试；范围记录见l2/validation-scope.json。

## 固定身份与位置

工程根：/Users/<user>/projects/thor-unixnu。

| 项目 | 身份 |
|---|---|
| run | thor-20261026-vm-mem03 |
| 新Boot v4 | 04802a8172f122f97fecd7f5da313212196cd2e4853dbf4243c56444029f12d2 / 25894912B |
| 新XNU | ed3981deb17e10f0d99dacf23bb5ccc5b1d380b50ab946689522be7213ad40d0 / 19279240B |
| XNU源码HEAD | 9b2a4adb4cd387476ebfd028d3b61f1cabe2da3e |
| 实际编译源码 | 960fe47c85385f3b4d5eac7a96007e8ecb39b590；后续仅audit/test，编译源码等价已核 |
| UEFI commit | bb8382c6417adf779b89adfde295832e846cee93 |
| FD | d259b23776f6a53e425d31aa88603f5c8bc799bebc8ce288b03a705b362e1c2e / 3145728B |
| 独立L2收据SHA | feb1a306ffdd6896593ed0ffbd7c137b3dee98d6ebba8532ccc6f5e74b1b8a57 |
| 控制器预检SHA | 40f1be48a6a4086afca551184d11bc10efa6100dc0cd1532ee68cdd78b8f8b1c |

候选artifacts/candidates/thor-20261026-vmdev-012；011为历史HW12，禁止覆盖。
新镜像和seed只留Mac的runs/thor-20261026-vm-mem03/l2/package，不入Git、不迁移i386。
源码独立树：worktrees/xnu/vm-mem03、worktrees/uefi/vm-mem03、worktrees/control/vm-mem03；旧工作树与未提交修改保留。
控制工具提交包含a1c590c、31c104c、f66c8c0、1c989de，均为本地提交。

主收据：runs/thor-20261026-vm-mem03/l2/final-host-readiness.json。
独立L2：runs/thor-20261026-vm-mem03/l2/package/independent-offline.json。
控制器：runs/thor-20261026-vm-mem03/l2/controller/controller-preflight.json，READY_NOT_EXECUTED。
设备最终只读：runs/thor-20261026-vm-mem03/l2/mac-final-readonly-preflight.json。
固件发布：runs/thor-20261026-vm-mem03-host-01/l2/firmware-release.json。
原始构建/对比/被拒绝场景收据保留，不回改历史结果。

## 下一次录像与L3

连续录像应覆盖整屏，从启动前到返回Android；重点顶部4×4（00–10预计完成，11–15保留）及五窗口/UN详情。
若出现故障页，拍清顶部S/P/A/R。成功路径不预计出现旧低VA琥珀倒计时；失败有界路径可能出现它。
执行时必须使用已封存controller.py execute，显式传recording-confirmed和本run/image pin；控制器自行复核所有封存输入、Android身份、Fastboot product/slot，最多一次reboot和一次临时boot，消费先于boot，任何异常停止不重试。
随后独立monitor只读Android恢复，再根据本轮画面验收；不能把Fastboot OKAY或Android返回当成网格或iOS成功。

MEM01/MEM02已消费镜像永不复用。623b444d…333aa旧内核有已证90/315缺陷，只作历史证据，不再选为新L3候选。所有flash/erase/format/切槽均不在本轮范围。
完整iOS仍有RAM所有权、loader峰值、真实Darwin22 VM/驱动/UFS/APFS/用户态关口。新的峰值源码依据见thor-ios-loader-memory-peak-20261026.md；本轮未宣称SpringBoard成功。
