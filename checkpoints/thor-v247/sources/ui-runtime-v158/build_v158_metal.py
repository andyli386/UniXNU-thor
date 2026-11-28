import os
from pathlib import Path
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import uuid


root = Path(os.environ['THOR_ROOT'])
out = root / "thor-out/v158-metal-staging"
old_bridge = root / "thor-out/v147-import-concurrency/bridge"
old_header = root / "thor-out/v98-gpu-present-guard/PongMetal/PongSurfaceHandoff.h"
old_ui = root / "thor-out/v156-switcher-cellular/switcher-runtime"
new_bridge = out / "bridge"
new_ui = out / "ui"
runtime = out / "runtime"
for path in (new_bridge, new_ui, runtime):
    path.mkdir(parents=True, exist_ok=True)


def replace(source, before, after):
    if source.count(before) != 1:
        raise ValueError(f"Expected one match for {before[:150]!r}")
    return source.replace(before, after)


def edit(source_path, dest_path, changes):
    original = source_path.read_text()
    edited = original
    for before, after in changes:
        edited = replace(edited, before, after)
    dest_path.write_text(edited)
    return "".join(difflib.unified_diff(
        original.splitlines(keepends=True), edited.splitlines(keepends=True),
        str(source_path.relative_to(root)), str(dest_path.relative_to(out)),
    ))


header_patch = edit(old_header, new_bridge / old_header.name, [
    ("static kern_return_t handoff(mach_port_t right,bool end,bool failed) {",
     """static kern_return_t handoff(mach_port_t right,bool end,bool failed,uint64_t *releaseTick=nullptr) {"""),
    ("    return call(connection,0x504f4701u,input,3,nullptr,nullptr);",
     """    if(releaseTick) {
        unsigned count=1;
        const auto result=call(connection,0x504f4701u,input,3,releaseTick,&count);
        // The legacy selector rejects the extra result before changing ownership.
        if(static_cast<unsigned>(result)!=0xe00002c2) {
            if(result || count!=1)*releaseTick=0;
            return result;
        }
        *releaseTick=0;
    }
    return call(connection,0x504f4701u,input,3,nullptr,nullptr);"""),
    ("""static EndFailure complete(std::vector<HeldSurface> &rights,bool failed) {
    EndFailure failure;
    for(const auto &held:rights) {
        const kern_return_t result=handoff(held.right,true,failed);""",
     """struct ReleaseSample {
    uint64_t beginUs,endUs,tick;
    uint32_t sid;
    kern_return_t status;
};
struct ReleaseSamples {
    ReleaseSample samples[8];
    size_t count=0;
};
static EndFailure complete(std::vector<HeldSurface> &rights,bool failed,
                           ReleaseSamples *timing=nullptr,uint64_t (*nowUs)()=nullptr) {
    EndFailure failure;
    for(const auto &held:rights) {
        ReleaseSample *sample=timing && timing->count<8 ? &timing->samples[timing->count++] : nullptr;
        if(sample)*sample={nowUs(),0,0,held.sid,0};
        const kern_return_t result=handoff(held.right,true,failed,sample?&sample->tick:nullptr);
        if(sample) {
            sample->endUs=nowUs();
            sample->status=result;
        }"""),
])

frontend_file = "NativeBridgeMetalFrontend.mm"
frontend_patch = edit(old_bridge / frontend_file, new_bridge / frontend_file, [
    ("""static bool frameTraceOn(){static const bool on=getenv("SHENG_GPU_TRACE")!=nullptr;return on;}
template<typename... T> static void frameEvent""",
     """static bool frameTraceOn(){static const bool on=getenv("SHENG_GPU_TRACE")!=nullptr;return on;}
static bool producerTimingOn() {
 static const bool compositor=getprogname() && !strcmp(getprogname(),"backboardd");
 return compositor && gFrameTransport;
}
template<typename... T> static void frameEvent"""),
    (""" std::vector<std::pair<uintptr_t,size_t>> merged;
 for(auto it=ranges.begin();it!=last;++it) {
  if(!it->first || !it->second || it->second>UINTPTR_MAX-it->first)continue;
  if(!merged.empty()) {
   auto &previous=merged.back();const uintptr_t end=previous.first+previous.second;
   if(it->first<=end){previous.second=std::max(end,it->first+it->second)-previous.first;continue;}
  }
  merged.push_back(*it);
 }""",
     """ size_t merged=0;
 for(auto it=ranges.begin();it!=last;++it) {
  if(!it->first || !it->second || it->second>UINTPTR_MAX-it->first)continue;
  if(merged) {
   auto &previous=ranges[merged-1];const uintptr_t end=previous.first+previous.second;
   if(it->first<=end){previous.second=std::max(end,it->first+it->second)-previous.first;continue;}
  }
  ranges[merged++]=*it;
 }
 ranges.resize(merged);"""),
    (" for(const auto &range:merged) {", " for(const auto &range:ranges) {"),
    (" uint64_t createdUs,traceNumber;",
     " uint64_t createdUs,traceNumber;\n@package\n uint64_t writerBeginUs,submitUs,callbackUs;"),
    (""" const auto release=pongsurface::complete(displayHandoffs,failed);
 if(release.count)reportProducerFailure""",
     """ // Only instrument commands already late enough to miss a 120 Hz display deadline.
 // The kernel accepts records only during its bounded compositor capture.
 const bool sample=callbackUs && !displayHandoffs.empty() && nowUs()-self.committedUs>=12000;
 pongsurface::ReleaseSamples releaseSamples;
 const auto release=pongsurface::complete(displayHandoffs,failed,sample?&releaseSamples:nullptr,nowUs);
 if(release.count)reportProducerFailure"""),
    (""" const uint64_t flushBegan=nowUs();flushSurfaces(_surfaces,kernelSynced);
 frameEvent(61,traceNumber,1,flushBegan-syncBegan,nowUs()-flushBegan,""",
     """ const uint64_t flushBegan=nowUs();flushSurfaces(_surfaces,kernelSynced);
 const uint64_t flushDone=nowUs();
 frameEvent(61,traceNumber,1,flushBegan-syncBegan,flushDone-flushBegan,"""),
    (""" if(queue)dispatch_async(queue,run);else run();
}
- (void)commit {""",
     """ if(queue)dispatch_async(queue,run);else run();
 // Transport only after all ownership calls and normal handler dispatch.
 for(size_t i=0;i<releaseSamples.count;++i) {
  const auto &entry=releaseSamples.samples[i];
  if(!entry.tick)continue;
  NativeGpuTraceRecord record{};
  record.event=64;
  const uint64_t fields[]={entry.sid,traceNumber,self.committedUs,writerBeginUs,submitUs,callbackUs,
                           flushBegan,flushDone,entry.beginUs,entry.endUs,
                           static_cast<uint64_t>(failed || entry.status),entry.tick};
  static_assert(sizeof(fields)==sizeof(record.value),"producer event fields");
  memcpy(record.value,fields,sizeof(fields));
  (void)gFrameTransport(NativeGpuTraceMethod,&record,sizeof(record));
 }
}
- (void)commit {"""),
    (""" if(!self.encodeFailure && !pongsurface::begin(_surfaces,displayHandoffs))
  [self refuse:@"IOSurface CPU/GPU ownership handoff unavailable"];""",
     """ if(!self.encodeFailure) {
  if(producerTimingOn() && _surfaces.count)writerBeginUs=nowUs();
  if(!pongsurface::begin(_surfaces,displayHandoffs))
   [self refuse:@"IOSurface CPU/GPU ownership handoff unavailable"];
 }"""),
    ("""  if(nmb_command_buffer_on_completed(_handle,completed,context) &&
     nmb_command_buffer_on_scheduled(_handle,scheduled,context) && nmb_command_buffer_commit(_handle))return;""",
     """  if(nmb_command_buffer_on_completed(_handle,completed,context) &&
     nmb_command_buffer_on_scheduled(_handle,scheduled,context)) {
   if(writerBeginUs && !displayHandoffs.empty())submitUs=nowUs();
   if(nmb_command_buffer_commit(_handle))return;
  }"""),
    (""" const uint64_t callbackBegan=nowUs();
 int64_t scheduledUs=-1;""",
     """ const uint64_t callbackBegan=nowUs();
 if(commandBuffer->submitUs)commandBuffer->callbackUs=callbackBegan;
 int64_t scheduledUs=-1;"""),
    (""" if(frameTraceOn() && loader) {
  void *library=dlopen(loader,RTLD_NOW|RTLD_NOLOAD);
  if(library)gFrameTransport=(bool (*)(uint32_t,const void *,uint32_t))dlsym(library,"tu_darwin_frame_diagnostic");
  nmb_gpu_diagnostic_hook""",
     """ if(loader && (frameTraceOn() || (getprogname() && !strcmp(getprogname(),"backboardd")))) {
  void *library=dlopen(loader,RTLD_NOW|RTLD_NOLOAD);
  if(library)gFrameTransport=(bool (*)(uint32_t,const void *,uint32_t))dlsym(library,"tu_darwin_frame_diagnostic");
  if(frameTraceOn()) {
   nmb_gpu_diagnostic_hook"""),
    ("""  frameText(46,getprogname());frameEvent(48,singleSample(),getenv("SHENG_GPU_PIXELS")!=nullptr);
  if(library)if(auto build=(const char *(*)())dlsym(library,"tu_darwin_frame_build_id"))frameText(58,build());
  frameText(59,getenv("TU_DEBUG")?getenv("TU_DEBUG"):"default");
#ifdef NATIVE_METAL_BUILD_ID
  frameText(47,NATIVE_METAL_BUILD_ID);
#endif
 }""",
     """   frameText(46,getprogname());frameEvent(48,singleSample(),getenv("SHENG_GPU_PIXELS")!=nullptr);
   if(library)if(auto build=(const char *(*)())dlsym(library,"tu_darwin_frame_build_id"))frameText(58,build());
   frameText(59,getenv("TU_DEBUG")?getenv("TU_DEBUG"):"default");
#ifdef NATIVE_METAL_BUILD_ID
   frameText(47,NATIVE_METAL_BUILD_ID);
#endif
  }
 }"""),
])


def run(*args):
    subprocess.run(list(args), check=True)


def output(*args):
    return subprocess.check_output(list(args), text=True)


def mac_uuid(path):
    result = output("xcrun", "dwarfdump", "--uuid", path)
    values = re.findall(r"UUID: ([0-9A-F-]+) \(arm64\)", result)
    if len(values) != 1:
        raise ValueError(result)
    return uuid.UUID(values[0])


def sign_and_verify(path, existing):
    run("/usr/bin/codesign", "--force", "--sign", "-", "--pagesize", "4096", path)
    run("/usr/bin/codesign", "--verify", "--strict", path)
    assert output("xcrun", "lipo", "-archs", path).strip() == "arm64"
    assert output("xcrun", "otool", "-hv", path).splitlines()[-1].split()[4] == "DYLIB"
    assert output("xcrun", "otool", "-D", path).splitlines()[1:] == output("xcrun", "otool", "-D", existing).splitlines()[1:]
    assert output("xcrun", "otool", "-L", path).splitlines()[1:] == output("xcrun", "otool", "-L", existing).splitlines()[1:]
    assert sorted(output("xcrun", "nm", "-gjU", path).splitlines()) == sorted(output("xcrun", "nm", "-gjU", existing).splitlines())


bridge_product = runtime / "PongMetalBridge.dylib"
bridge_recipe = json.loads((old_bridge / "build-command.json").read_text())
bridge_files = {
    str(old_bridge / frontend_file): str(new_bridge / frontend_file),
    str(old_bridge / "PongMetalFrontend.o"): str(new_bridge / "PongMetalFrontend.o"),
    str(old_bridge / bridge_product.name): str(bridge_product),
}
for step, args in bridge_recipe.items():
    bridge_recipe[step] = [bridge_files.get(arg, arg) for arg in args]
(new_bridge / "build-command.json").write_text(json.dumps(bridge_recipe, indent=2) + "\n")
run(*bridge_recipe["compile"])
run(*bridge_recipe["link"])
sign_and_verify(bridge_product, old_bridge / bridge_product.name)
stock_uuid = mac_uuid(old_bridge / bridge_product.name)
new_uuid = mac_uuid(bridge_product)
assert str(stock_uuid).upper() == "8F8693A4-C887-3433-A96F-EAE7407D972B"
assert new_uuid != stock_uuid


def uuid_array(value):
    return ", ".join(f"0x{byte:02x}" for byte in value.bytes)


ui_source = old_ui / "ui_compat18-v156.c"
ui_edited = new_ui / "ui_compat18-v158.c"
ui_patch = edit(ui_source, ui_edited, [
    (uuid_array(stock_uuid), uuid_array(new_uuid)),
    (str(stock_uuid).upper(), str(new_uuid).upper()),
])
shutil.copyfile(old_ui / "thor-app-exec.h", new_ui / "thor-app-exec.h")
ui_product = runtime / "libpong-ui.dylib"
ui_recipe = json.loads((old_ui / "compile-switcher-runtime.json").read_text())
ui_recipe[ui_recipe.index(str(ui_source))] = str(ui_edited)
ui_recipe[ui_recipe.index("-o")+1] = str(ui_product)
(new_ui / "compile-switcher-runtime.json").write_text(json.dumps(ui_recipe, indent=2) + "\n")
run(*ui_recipe)
sign_and_verify(ui_product, old_ui / ui_product.name)

tag = f"thor-metal-pool-{new_uuid.hex[:8]}"
byte_count = bridge_product.stat().st_size
(out / "thor_metal_pool_payload.h").write_text(
    "#pragma once\n"
    f"#define THOR_METAL_POOL_PAYLOAD_BYTES {byte_count}u\n"
    f'#define THOR_METAL_POOL_NAME "{tag}"\n'
    f"extern const unsigned char thor_metal_pool_payload[{byte_count}];\n"
    '__asm__(".section __TEXT,__const\\n.p2align 12\\n.globl _thor_metal_pool_payload\\n'
    '_thor_metal_pool_payload:\\n'
    f'.incbin \\"{bridge_product}\\"\\n.text\\n");\n'
)

(out / "producer-timing.patch").write_text(frontend_patch + header_patch + ui_patch)
products = [
    bridge_product, ui_product, out / "thor_metal_pool_payload.h",
    out / "producer-timing.patch",
]
manifest = {
    "bridge_uuid": str(new_uuid).upper(),
    "ui_uuid": str(mac_uuid(ui_product)).upper(),
    "metal_pool_name": tag,
    "artifacts": {
        str(path.relative_to(out)): {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in products
    },
}
(out / "verified-products.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps(manifest, indent=2))
