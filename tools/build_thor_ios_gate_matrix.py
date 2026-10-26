#!/usr/bin/env python3
"""Generate evidence-linked multi-gate iOS boot matrix with no fabricated pass.

All receipts must be present and source proven. Hardware / XNU runtime
states require separate L3; pre-EBS static UEFI cannot observe post-EBS XNU.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

def ensure(condition,reason):
    if not condition:raise ValueError(reason)

def read(base,path):
    source=base/path
    ensure(source.is_file(),'missing immutable receipt '+path)
    raw=source.read_bytes()
    return json.loads(raw),{'path':path,'sha256':hashlib.sha256(raw).hexdigest()}

def gate(code,group,name,status,detail,evidence=()):
    ensure(status in ('PASS_HOST','PASS_SOURCE','BLOCKED','PENDING'),'invalid gate state')
    return dict(code=code,group=group,name=name,status=status,detail=detail,
                evidence=list(evidence),phone_passed=False)

def make(root):
    r=lambda path:read(root,path)
    fd,fdref=r('evidence/build/thor-20261026-ios-full-f1e/f1e-independent-structural-audit.json')
    fat,fatref=r('runs/ios16-f1e-full-kernel-on-fat56-host-20261026/real-Apple20G81-f1e-envelope-proof.json')
    qemu,qref=r('runs/ios-f1d-qemu-host-20261026/qemu-visible-menu-proof.json')
    fdt,fdtref=r('evidence/build/thor-20261026-ios-full-f1d/source-bootchain-audit.json')
    mem,memref=r('reports/thor-darwin22-memory-gate-20261026.json')
    ddr,ddrref=r('reports/thor-ddr-ownership-hypotheses-20261026.json')
    xnu,xnuref=r('reports/thor-ios16-storage-bound-native-host-20261026.json')
    apfs,apfsref=r('runs/ios16-apfs-full-closure-host-20261026/apfs-emission-proof.json')
    ufs,ufsref=r('reports/thor-pong-ufs-dt-diff-20261026.json')
    ensure(fd['result']=='PASS_REAL_F1E_FV_60M_FAT_RAM_FDT_AND_31_LINKED_EFI' and
           len(fd['matched_PE_outputs'])==31 and not fd['boot_eligible'],
           'F1e full firmware 31 module evidence missing')
    ensure(fat['result']=='PASS_ACTUAL_APFS_KERNEL_FAT56_THORFAT1_ENVELOPE_FITS_64M_INITRD'
           and fat['initrd_bytes']<64*1024*1024
           and not fat['volume_is_phone_boot_eligible'],'real FAT payload missing')
    ensure(qemu['result']=='PASS_REAL_AARCH64_UEFI_UNIXNU_MENU_DRAWN_AND_CONFIG_LOADED'
           and not qemu['real_phone_hardware_tested'],'QEMU menu evidence invalid')
    ensure(fdt['result']=='PASS_SOURCE_CALL_ORDER_FAT_CONFIG_DISCOVERY_HOST_ONLY'
           and fdt['conditions_passed']==32,'source-only FDT gate invalid')
    ensure(mem['result']=='BLOCKED_EXPECTED_F1C_CANNOT_BOOT_F2_FULL_KERNEL'
           and mem['available_MiB']==124 and mem['minimum_window_MiB']==256
           and not mem['phone_boot_allowed'],'memory cannot be marked passed')
    ensure(ddr['result']=='RESEARCH_ONLY_ZERO_HARDWARE_MEMORY_OWNERSHIP_PROVEN'
           and ddr['none_candidates_UEFI_allocatable'],'hypothetical DDR allocation must be forbidden')
    ensure(xnu['result']=='PASS_DARWIN22_REAL_20G81_STORAGE_ABI_NATIVE_UFS_LINK_HOST_ONLY'
           and xnu['kernel_imports_compared_exactly']==11 and not xnu['boot_eligible'],
           'storage ABI host link invalid')
    ensure(apfs['result']=='PASS_HOST_APFS_IOSTORAGE_CORETRUST_ORIGINAL_COMPONENTS_EMITTED'
           and apfs['component_count']==11 and not apfs['boot_eligible'],
           'APFS prelink invalid')
    ensure(fat['kernel_sha256']==apfs['candidate_sha256'],
           'FAT kernel differs from APFS executable input')
    ensure(ufs['result']=='PASS_PINNED_THOR_PONG_UFS_MMIO_PHY_IRQ_AND_STREAMID_AGREE'
           and not ufs['hardware_MMIO_or_DMA_authorized'],
           'vendor UFS evidence must remain source-only')
    gates=[
      gate('U01','UEFI','Full 31-module UniXNU firmware','PASS_HOST',
           'F1e real FD and executable FFS all byte-match the build; not phone tested.',[fdref]),
      gate('U02','UEFI','Actual UniXNU graphical menu','PASS_HOST',
           'QEMU EDK II ARM64 boot, FAT config and 1280x800 GOP frame confirmed.',[qref]),
      gate('U03','UEFI','Full APFS kernel on THORFAT1','PASS_HOST',
           'The real 50.659MB APFS kernel is byte-identical inside FAT56/initrd.',[fatref]),
      gate('U04','UEFI','FDT/HOB and BDS code ordering','PASS_SOURCE',
           '32 host source call-graph checks, not a Thor physical runtime.',[fdtref]),
      gate('U05','UEFI','256MiB owned DarwinBoot window','BLOCKED',
           'F1c exposes 124MiB ConventionalMemory, short 132MiB.',[memref,ddrref]),
      gate('U06','UEFI','Complete UniXNU running on Thor','PENDING',
           'No real hardware L3 with generic BDS/RAM FAT/DarwinBoot.'),
      gate('K01','XNU','Native Darwin22/XNU8796 ARM64 link','PASS_HOST',
           'Mach-O emitted in a host-only build.',[xnuref]),
      gate('K02','XNU','NativeUfs genuine Apple storage ABI','PASS_HOST',
           '190-slot class, 11 real original imports and source-linked UFS.',[xnuref]),
      gate('K03','XNU','Full Apple APFS/root security prelink','PASS_HOST',
           '11 original 20G81 modules; 12972 pointer bindings verified.',[apfsref]),
      gate('K04','XNU','Thor pmap, CPU/GIC/timer and scheduler','PENDING',
           'Old Darwin23 pre-pmap phone gate cannot certify new Darwin22 kernel.'),
      gate('S01','STORAGE','Thor UFS PHY/SMMU live read','PENDING',
           'Static shared SM8475 MMIO/SID not actual authorized hardware.',[ufsref]),
      gate('S02','USERLAND','Mounted OS/SystemOS/Data roots','PENDING',
           'Complete signed system-root payload and Thor APFS mount are absent.'),
      gate('S03','USERLAND','launchd and required services','PENDING',
           'No verified iOS userspace execution on Thor.'),
      gate('S04','GUI','Original SpringBoard rendered','PENDING',
           'No real native iOS display-services and SpringBoard frame on Thor.')
    ]
    counts={}
    for item in gates:counts[item['status']]=counts.get(item['status'],0)+1
    ensure(counts=={'PASS_HOST':6,'PASS_SOURCE':1,'BLOCKED':1,'PENDING':6},
           'gate distribution unexpectedly changed: '+str(counts))
    return {'schema':'thor-ios-native-springboard-evidence-gates-v1',
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'goal':'Original iOS 16.6.1 SpringBoard on Xiaomi 12S Ultra',
      'result':'HOST_PROGRESS_BUT_256M_RAM_AND_PHYSICAL_BOOT_BLOCKED',
      'total_gates':len(gates),'statuses':counts,'gates':gates,
      'fullchain_hardware_ready':False,
      'DarwinBoot_256MiB_qualified':False,
      'original_iOS_SpringBoard_confirmed':False,
      'device_actions':0}

def markdown(report):
    lines=['# Thor / UniXNU 原生 iOS 启动门禁（实际证据）','',
      '更新：'+report['generated_at'],'',
      '重要：HOST_PASS 只表示主机侧完成，绝非手机内核或 iOS 用户态启动成功。','',
      '| ID | 验收关口 | 当前状态 | 可证明的内容 |','|---|---|---|---|']
    for g in report['gates']:
        lines.append('| '+g['code']+' | '+g['name']+' | '+g['status']+' | '+g['detail']+' |')
    lines+=['',
      '下一关键依赖：取得真实设备 FDT、UEFI 内存图和所有权证明，建立至少 256MiB 的连续 EfiConventionalMemory，不可从公开厂商设备树直接宣称有权使用。',
      '全部 F1/F2 当前均非手机运行候选。不存在新的 Android 分区写入、fastboot、刷写或 A/B 切换。',
      '']
    return '\n'.join(lines)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',required=True,type=Path)
    p.add_argument('--json',required=True,type=Path)
    p.add_argument('--markdown',required=True,type=Path)
    opt=p.parse_args()
    ensure(not opt.json.exists() and not opt.markdown.exists(),
           'existing machine-readable evidence cannot be overwritten')
    report=make(opt.root.resolve())
    opt.json.parent.mkdir(parents=True,exist_ok=True)
    opt.markdown.parent.mkdir(parents=True,exist_ok=True)
    with opt.json.open('x') as f:json.dump(report,f,indent=2,ensure_ascii=False);f.write('\n')
    with opt.markdown.open('x') as f:f.write(markdown(report))
    print(json.dumps({'result':report['result'],'statuses':report['statuses'],
      'total_gates':report['total_gates'],'phone_boot_ready':False},indent=2))
if __name__=='__main__':main()
