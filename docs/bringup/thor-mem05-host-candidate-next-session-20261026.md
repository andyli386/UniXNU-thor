# MEM05 内核主机候选完成后的接续

MEM04真机结论和整体阶段图见thor-progress-20261026-mem04-l3.md。用户要求每轮最终汇报整体流程图；只由用户录像后手动启动，新镜像/L2就绪就直接发命令，不再询问授权。MEM04 boot bd37e82c...a04f7已消费，禁止重跑。

Mac候选artifacts/candidates/thor-20261026-vmdev-014已封存kernel.macho、kernel-layout.json、manifest.json、build-manifest.json、machinecode.json、mem05-grid-vm-machinecode-final.json和mem05-reproducible-link.json。kernel SHA765bcb34234def5ba344a593e9ef76176d44d0fdd1576dabef0eae043d1c9c89，19285016B；span20185088B，SHA9d7eb694b709b029b72fd4e40b62b050290e4db8b4e4b68c52b4e64798869f23。XNU树worktrees/xnu/vm-mem05，HEAD16194ea46fcd34b922c9cbe929ea78a76cdf6314 clean；actual compile be4da579d6baf282f3cf1f4c2141441a0eb22eca，后续仅auditor和CFG test两文件，C源等价。

已完成真实link-02及强制link-03字节一致；通用audit-01对象/内核通过；12项生产C预算/PTD测试和8项CFG测试通过，最终日志runs/thor-20261026-vmdev-014/l2/test_thor_mem05_*-final.log。最终MC为mem05-grid-vm-machinecode-final.json，SHA7c26d2fa82704d404781e50616f65ad09878f676521424c7196b48f6971b29fa。之前attempt01/02/04/05是auditor断言终止CFG适配失败；attempt03性能差被终止；没有计PASS，保留所有log。attempt06及最终PASS。

新代码保留MEM04全部pmap守卫，独立post-pmap分配游标；64MiB不变，额外L3页至多20页81920B，102个初始PTD剩余池严格匹配，拒绝扩池。两组native L3循环都有写前检查、真实bzero和pmap_init_pte_page以及发布后复核；原始高VA current_thread.preemption_count和lowGlo写权限/owned范围预探测，实际arm_vm_init返回后核first_avail/lowGlo/TTBR0/TTBR1/VBAR、512 alias+空root、335FB翻译和新页/PTD。0E=L3 CALL、0F=VM RETURN，00–0D保持，16格仅代表本次早期诊断，不代表完整iOS。返回后noreturn12秒PSCI reset，未放行pmap_map_globals或后续平台初始化。

注意几何：entry0x3ff488、vectors0x400000、sync0x3ff000+0x3000，与MEM04相同；但span增加0x4000，intstack/excepstack等从新layout派生，必须完整重绑UEFI所有依赖offset，不可只换SHA。预计500B交接代码仍8ce4...，必须以新真实PE检验，不可预设通过。低globals诊断alias的后续pmap_map_globals冲突仍未放行；arm_vm_init本轮只修改原始lowGlo对象，不把LOW_GLOBAL+0x2000重映射。

下一步：i386 UEFI当前仍为worktrees/uefi/vm-mem04 d339...，未创建MEM05或改源。将公共kernel+JSON用已有同SHA MEM04 kernel作zstd差分传输（禁止传seed/boot）。在独立UEFI vm-mem05中基于bind-thor-mem04-kernel.py新增MEM05 schema/16阶段/early-vm-return绑定，完整重建span依赖；主机UI标题改MEM05，内存政策与FDT解析保持。两次真实固件link、2FV/20PE/96lib/379输入、45 renderer及19帧验证，核500B实际code。Mac control新树worktrees/control/vm-mem05已有进度报告，打包器/独立gate尚只有MEM03/MEM04 profile，需要添加严格MEM05 profile/native-return proof验证及相应负例；不得假装MEM04 proof schema来绕过。然后Mac新seed只生成一次→整包独立L2→readonly Android baseline→prepare controller→发execute/monitor命令；目前无MEM05 image/seed/controller，不能发可执行boot命令。额外5个CLI fault场景曾被连接器拒绝，不变形重试，不计PASS。
