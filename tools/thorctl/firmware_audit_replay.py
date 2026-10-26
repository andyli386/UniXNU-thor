#!/usr/bin/env python3
"""Replay the unchanged FV/FFS/build auditor on explicitly relocated public inputs.

Generated GNUmakefiles and response files remain byte-for-byte unchanged.  Only
the auditor's Path constructor maps the declared original source prefix to the
new clean checkout.  Parser outputs go into a private temporary directory.
"""
import contextlib
import importlib.util
import io
import json
import hashlib
from pathlib import Path


class ReplayError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ReplayError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PublicPathMap:
    def __init__(self, source, original_source, original_root, exact=None):
        self.source=Path(source).resolve()
        self.original_source=Path(original_source)
        self.original_root=Path(original_root)
        self.exact={str(Path(k)):Path(v).resolve() for k,v in (exact or {}).items()}
        require(self.original_source.is_absolute() and self.original_root.is_absolute(),
                "explicit absolute original source/root prefixes")
        require(self.original_source.is_relative_to(self.original_root),"source inside original project")

    def __call__(self,*parts):
        path=Path(*parts)
        if str(path) in self.exact:
            return self.exact[str(path)]
        if path.is_absolute() and path.is_relative_to(self.original_source):
            return self.source/path.relative_to(self.original_source)
        if path.is_absolute() and path.is_relative_to(self.original_root):
            raise ReplayError("unmapped original public input: "+str(path))
        return path


def semantic(value, replacements):
    if isinstance(value,dict):
        result={key:semantic(item,replacements) for key,item in value.items() if key!="audited_utc"}
        if "inputs" in result and isinstance(result["inputs"],list):
            result["inputs"]=sorted(result["inputs"],key=lambda item:item["path"])
        return result
    if isinstance(value,list):
        return [semantic(item,replacements) for item in value]
    if isinstance(value,str):
        for old,new in replacements:
            value=value.replace(old,new)
        return value
    return value


def replay(root,source,original,index,temporary):
    root,source,temporary=Path(root).resolve(),Path(source).resolve(),Path(temporary).resolve()
    original=json.loads(Path(original).read_text())
    require(original["status"]=="PASS" and not original["failures"],"original composition audit passed")
    original_fd=Path(original["fd"]["path"])
    suffix=Path("Build/thorEntry09/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd")
    require(str(original_fd).endswith("/"+str(suffix)),"known original FD path layout")
    old_source=original_fd
    for _ in suffix.parts:
        old_source=old_source.parent
    require(old_source.name in {"vm-mem03","vm-mem04","vm-mem05","vm-mem06"} and old_source.parent.name=="uefi",
            "MEM03 original build source identity")
    old_root=old_source.parents[2]
    actual_fd=source/suffix
    require(actual_fd.is_file() and actual_fd.stat().st_size==original["fd"]["bytes"]
            and digest(actual_fd)==original["fd"]["sha256"],"original audit FD identity matches actual relocated firmware")
    mapper=PublicPathMap(source,old_source,old_root)
    mapper.candidate_stage=old_source.name.removeprefix("vm-")
    index_info=verify_public_index(index,root,mapper)
    # Every original auditor input is checked before the audit starts; no path
    # search, missing-file fallback, or text rewriting is permitted.
    require(len(original["inputs"])>=100,"complete original public input set")
    for item in original["inputs"]:
        require(item["path"] in mapper.indexed_origins,"auditor input absent from explicit public transfer index: "+item["path"])
        path=mapper(item["path"])
        require(path.is_file() and path.stat().st_size==item["bytes"] and digest(path)==item["sha256"],
                "relocated auditor input bytes differ: "+item["path"])
    auditor=Path(__file__).with_name("firmware_audit.py")
    spec=importlib.util.spec_from_file_location("mem03_replayed_firmware_audit",auditor)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser=module.Parser
    parser_root=temporary/"parser"
    module.Path=mapper
    module.Parser=lambda _root:parser(parser_root)
    with contextlib.redirect_stdout(io.StringIO()):
        status=module.Audit(root,source,temporary/"audit").run()
    require(status==0,"fresh FV/FFS/build audit rejected relocated public evidence")
    label="thor_fd_"+original["fd"]["sha256"][:12]+"-audit.json"
    fresh_path=temporary/"audit"/label
    fresh=json.loads(fresh_path.read_text())
    replacements=[(str(old_root/"analysis/firmware-volumes"),"$PARSER/analysis/firmware-volumes"),
                  (str(parser_root/"analysis/firmware-volumes"),"$PARSER/analysis/firmware-volumes"),
                  (str(old_source),"$SOURCE"),(str(source),"$SOURCE"),
                  (str(old_root),"$ROOT"),(str(root),"$ROOT")]
    before,after=semantic(original,replacements),semantic(fresh,replacements)
    require(before==after,"fresh firmware audit differs beyond explicit paths and timestamp; sections="+repr([key for key in before if before.get(key)!=after.get(key)]))
    return {"schema":"thor-"+mapper.candidate_stage+"-fresh-firmware-audit-replay-v1","result":"PASS",
            "fd_sha256":fresh["fd"]["sha256"],"modules":len(fresh["actual_modules"]),
            "link_inputs":len(fresh["unique_link_inputs"]),"volumes":len(fresh["volumes"]),
            "all_original_input_bytes_rechecked":len(original["inputs"]),
            "full_audit_semantics_equal_after_explicit_relocation":True,
            "relocation":{"original_source":str(old_source),"local_source":str(source)},
            "public_index":index_info,"auditor_sha256":digest(auditor),
            "parser_sha256":digest(auditor.with_name("firmware_parse_volumes.py")),
            "adapter_sha256":digest(Path(__file__)),"phone_actions":0}


def verify_public_index(index,root,mapper):
    index=Path(index).resolve()
    require(index.is_relative_to(root),"public transfer index inside project")
    document=json.loads(index.read_text())
    require(document["schema"]=="thor-"+mapper.candidate_stage+"-public-source-firmware-transfer-v1", "known explicit public transfer index schema")
    require(document["contains_seed"] is False and document["contains_boot_image"] is False
            and document["phone_actions"]==0,"public transfer contains no entropy/boot image")
    require(document["actual_efi_count"]==20 and document["actual_link_input_libraries"]==96,
            "complete public module/library input transfer")
    require(isinstance(document["audited_input_files"],list) and len(document["audited_input_files"])>=300,
            "complete indexed public auditor input set")
    origins=set()
    for item in document["audited_input_files"]:
        require(item["archive"] in {mapper.candidate_stage+"-uefi-main",mapper.candidate_stage+"-uefi-link-inputs"},"declared public archive name")
        relative=Path(item["relative_path"])
        require(not relative.is_absolute() and relative.parts[0]=="uefi-root" and ".." not in relative.parts,
                "bounded explicit archive-relative input")
        origin=Path(item["origin_path"])
        require(origin.is_absolute() and origin.is_relative_to(mapper.original_source)
                and str(origin) not in origins,"unique original public source input")
        destination=mapper.source/Path(*relative.parts[1:])
        require(mapper(origin)==destination,"public index origin and overlay destination agree exactly")
        require(destination.is_file() and destination.stat().st_size==item["bytes"] and
                digest(destination)==item["sha256"],"indexed public input bytes missing/changed: "+str(destination))
        origins.add(str(origin))
    import subprocess
    require(subprocess.check_output(["git","-C",str(mapper.source),"rev-parse","HEAD"],text=True).strip()
            ==document["source_commit"],"public transfer source commit matches clean checkout")
    mapper.indexed_origins=origins
    return {"path":str(index),"sha256":digest(index),"indexed_files_checked":len(origins),
            "source_commit":document["source_commit"]}


def main():
    import argparse,sys,tempfile
    parser=argparse.ArgumentParser(description=__doc__)
    for option in ("root","source","original-audit","transfer-index","out"):
        parser.add_argument("--"+option,type=Path,required=True)
    args=parser.parse_args()
    root=args.root.resolve();out=args.out.resolve()
    require(out.is_relative_to(root/"runs") and not out.exists(),"new immutable precheck output inside runs")
    out.parent.mkdir(parents=True,exist_ok=True)
    report={"schema":"thor-mem03-fresh-firmware-audit-replay-v1","phone_actions":0}
    try:
        with tempfile.TemporaryDirectory(prefix="mem03-firmware-precheck-",dir=out.parent) as directory:
            report=replay(root,args.source,args.original_audit,args.transfer_index,Path(directory))
    except Exception as error:
        report.update(result="FAIL",error_type=type(error).__name__,error=str(error))
    with out.open("x") as handle:json.dump(report,handle,indent=2);handle.write("\n")
    print(json.dumps(report,indent=2))
    return 0 if report["result"]=="PASS" else 1


if __name__=="__main__":
    raise SystemExit(main())
