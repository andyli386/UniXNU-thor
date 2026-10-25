#!/usr/bin/env python3
"""Fail-closed post-link binder for Thor's reloc-free AArch64 transition blob.

The UEFI handoff assembly encodes the actual E06_* offsets directly.
Whenever the linked XNU span shifts, the 500-byte PE code changes and
the expected SHA must be derived from the exact linked PE before releasing
firmware. No phone, storage or bootloader commands.
"""
from pathlib import Path
import argparse,datetime,hashlib,json,re,struct,subprocess

OLD_EXPECTED="dda3e10fa9be82aea2a5883b4efa78df2c55ad79aaa2714f02cf6179fb5b2c16"
OLD_TRANSITION="dda3e10fa9be82aea2a5883b4efa78df2c55ad79aaa2714f02cf6179fb5b2c16"

def sha(data):return hashlib.sha256(data).hexdigest()
def load_pe(path, first, count):
    data=path.read_bytes()
    assert data[0:2]==b"MZ" and len(data)>8192
    pe=struct.unpack_from("<I",data,0x3c)[0]
    assert data[pe:pe+4]==b"PE\0\0"
    machine,nsections=struct.unpack_from("<HH",data,pe+4)
    assert machine==0xAA64 and 2<=nsections<=12
    optional_size=struct.unpack_from("<H",data,pe+20)[0]
    opt=pe+24
    assert struct.unpack_from("<H",data,opt)[0]==0x20B
    assert optional_size>=160
    directory_count=struct.unpack_from("<I",data,opt+108)[0]
    assert directory_count>5
    secstart=opt+optional_size
    sections=[]
    for i in range(nsections):
        off=secstart+40*i
        name=data[off:off+8].rstrip(b"\0").decode("ascii")
        virtual_size,rva,raw_size,raw_ptr=struct.unpack_from("<IIII",data,off+8)
        assert raw_ptr+raw_size<=len(data)
        sections.append(dict(name=name,rva=rva,virtual_size=virtual_size,
                             raw_size=raw_size,raw_ptr=raw_ptr))
    def fetch(rva,n):
        for s in sections:
            if s["rva"]<=rva and rva+n<=s["rva"]+s["raw_size"]:
                off=s["raw_ptr"]+rva-s["rva"]
                return data[off:off+n]
        raise ValueError("RVA outside file-backed PE sections "+hex(rva))
    assert any(s["name"]==".text" and s["rva"]<=first
               and first+count<=s["rva"]+s["raw_size"] for s in sections)
    reloc_rva,reloc_bytes=struct.unpack_from("<II",data,opt+112+5*8)
    reloc_hits=[]
    total=0
    if reloc_bytes:
        raw=fetch(reloc_rva,reloc_bytes)
        offset=0
        while offset<reloc_bytes:
            assert offset+8<=len(raw)
            page,size=struct.unpack_from("<II",raw,offset)
            assert size>=8 and size%2==0 and offset+size<=len(raw)
            for i in range(offset+8,offset+size,2):
                relocation=struct.unpack_from("<H",raw,i)[0]
                kind=relocation>>12
                if not kind:continue
                va=page+(relocation&0xfff)
                total+=1
                if first<=va<first+count:
                    reloc_hits.append({"rva":hex(va),"kind":kind})
            offset+=size
    assert not reloc_hits,"Transition bytes contain PE relocation(s)"
    return fetch(first,count),{
        "pe_sha256":sha(data),
        "pe_file_bytes":len(data),
        "pe_machine":"aarch64",
        "transition_rva":hex(first),"transition_bytes":count,
        "relocation_directory_bytes":reloc_bytes,
        "total_pe_relocations":total,
        "relocations_in_transition":reloc_hits,
        "sections":sections}

def transition_symbols(map_file):
    text=map_file.read_text()
    def symbol(name):
        pattern=r"^([a-fA-F0-9]+)\s+[a-fA-F0-9]+\s+\d+\s+"+name+r"\s*$"
        matches=re.findall(pattern,text,re.M)
        assert matches and len(set(matches))==1,(name,matches)
        return int(matches[0],16)
    start=symbol("ThorEntry06TransitionStart")
    end=symbol("ThorEntry06TransitionEnd")
    assert end>start and end-start==500
    assert start%4096==0 and end-start<4096
    return start,end

def header_expected(path):
    text=path.read_text()
    assert "#define THOR_ENTRY06_TRANSITION_BYTES 500u" in text
    arr=re.search(r"mEntry06TransitionSha256\[32\]\s*=\s*\{\s*([^}]+)\}",text)
    assert arr
    n=re.findall(r"0x([a-fA-F0-9]{2})",arr.group(1))
    assert len(n)==32
    return bytes.fromhex("".join(n)),text,arr

def pe_paths(root):
    d=root/"Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe"
    return d/"OUTPUT/ThorEntryBdsDxe.efi",d/"DEBUG/ThorEntryBdsDxe.map"

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--source",type=Path,required=True)
    ap.add_argument("--baseline",type=Path,required=True)
    ap.add_argument("--run-id",required=True)
    ap.add_argument("--apply",action="store_true")
    args=ap.parse_args()
    root=args.root.resolve();source=(root/args.source).resolve()
    baseline=(root/args.baseline).resolve()
    assert source.is_relative_to(root/"worktrees/uefi")
    assert baseline.is_relative_to(root/"sources") and source.is_dir() and baseline.is_dir()
    assert re.fullmatch(r"thor-[0-9a-z-]{7,80}",args.run_id)
    path=source/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntry06TransitionExpected.h"
    oldheader=baseline/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntry06TransitionExpected.h"
    pinned,original,match=header_expected(path)
    prior,_,_=header_expected(oldheader)
    assert pinned==prior==bytes.fromhex(OLD_EXPECTED)
    oldpe,oldmap=pe_paths(baseline)
    oldstart,oldend=transition_symbols(oldmap)
    oldcode,oldmeta=load_pe(oldpe,oldstart,oldend-oldstart)
    assert sha(oldcode)==OLD_TRANSITION
    pe,mapping=pe_paths(source)
    start,end=transition_symbols(mapping)
    code,info=load_pe(pe,start,end-start)
    current_sha=sha(code)
    assert len(code)==500
    if current_sha==OLD_EXPECTED:
        raise RuntimeError("No changed transition bytes: refusing nonessential rebind")
    changes=[i for i in range(0,len(code),4) if code[i:i+4]!=oldcode[i:i+4]]
    assert changes==[76,96,100,156],("Unexpected instruction differences",changes)
    values=", ".join("0x"+current_sha[i:i+2] for i in range(0,64,2))
    newheader=original[:match.start(1)]+values+original[match.end(1):]
    assert newheader.count("mEntry06TransitionSha256[32]")==1
    testmatch=re.search(r"mEntry06TransitionSha256\[32\]\s*=\s*\{\s*([^}]+)\}",newheader)
    assert sha(code)==bytes.fromhex("".join(re.findall(r"0x([a-fA-F0-9]{2})",testmatch.group(1)))).hex()
    report={
     "schema":"thor-transition-pe-postlink-identity-v1",
     "created_at":datetime.datetime.now().astimezone().isoformat(),
     "run_id":args.run_id,"result":"PASS_EXACT_LINKED_AARCH64_TRANSITION",
     "source":str(source),"baseline":str(baseline),
     "old_pin_sha256":pinned.hex(),"old_pe_actual_sha256":sha(oldcode),
     "new_pe_actual_sha256":current_sha,
     "new_pe_info":info,"old_pe_info":oldmeta,
     "changed_instruction_word_offsets":changes,
     "changed_word_count":len(changes),
     "linked_bytes_used_not_source_assembly":True,
     "pe_relocations_in_transition":0,
     "header_old_sha256":sha(original.encode()),
     "header_new_sha256":sha(newheader.encode()),
     "phone_actions":0,
     "runtime_guarantee":"No transition VA relocation; still require final PE and runtime hash verification"
    }
    out=root/"runs"/args.run_id/"l2"/"postlink-transition"
    if args.apply:
        assert not out.exists() and path.read_text()==original
        assert not subprocess.check_output(["git","-C",str(source),"status","--porcelain"],text=True).strip()
        out.mkdir(parents=True)
        with (out/"transition-identity.json").open("x") as f:json.dump(report,f,indent=2);f.write("\n")
        path.write_text(newheader)
        print("TRANSITION_EXPECTED_SHA_REBOUND",current_sha,str(path))
    else:
        print(json.dumps(report,indent=2))
if __name__=="__main__":main()
