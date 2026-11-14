// Standalone read-only APFS diagnostic exporter.
// Uses sgan81/apfs-fuse libapfs (GPL-2.0-or-later); no FUSE interface.
// New tool only: the upstream parser and device/project sources are unchanged.
#include <ApfsLib/ApfsContainer.h>
#include <ApfsLib/ApfsVolume.h>
#include <ApfsLib/ApfsDir.h>
#include <ApfsLib/Device.h>
#include <ApfsLib/GptPartitionMap.h>
#include <ApfsLib/Decmpfs.h>
#include <ApfsLib/Util.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <cerrno>
#include <cstring>
#include <cctype>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <vector>
namespace fs=std::filesystem;
static std::string q(const std::string&s){std::ostringstream o;o<<'"';const char*h="0123456789abcdef";for(unsigned char c:s){switch(c){case '"':o<<"\\\"";break;case '\\':o<<"\\\\";break;case '\n':o<<"\\n";break;case '\r':o<<"\\r";break;case '\t':o<<"\\t";break;default:if(c<32)o<<"\\u00"<<h[c>>4]<<h[c&15];else o<<char(c);}}o<<'"';return o.str();}
static std::string lower(std::string s){for(char&c:s)c=std::tolower((unsigned char)c);return s;}
static bool within(const std::string&p,const std::string&d){return p==d||p.rfind(d+"/",0)==0;}
static bool ends(const std::string&s,const std::string&e){return s.size()>=e.size()&&s.compare(s.size()-e.size(),e.size(),e)==0;}
static std::string now(){time_t t=time(nullptr);char b[40];tm v;gmtime_r(&t,&v);strftime(b,sizeof b,"%Y-%m-%dT%H:%M:%SZ",&v);return b;}
class ReadOnlyImage final:public Device{
 int fd=-1; uint64_t length=0;struct stat before{};
public:
 bool failed=false;uint64_t read_calls=0,read_bytes=0;
 ~ReadOnlyImage(){Close();}
 bool Open(const char*path)override{fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);if(fd<0)return false;if(fstat(fd,&before)||!S_ISREG(before.st_mode)){Close();errno=EINVAL;return false;}length=before.st_size;return true;}
 void Close()override{if(fd>=0){close(fd);fd=-1;}}
 uint64_t GetSize()const override{return length;}
 bool Read(void*out,uint64_t off,uint64_t len)override{read_calls++;if(off>length||len>length-off){failed=true;return false;}uint8_t*p=(uint8_t*)out;uint64_t done=0;while(done<len){ssize_t n=pread(fd,p+done,(size_t)std::min<uint64_t>(len-done,1024*1024),off+done);if(n<0&&errno==EINTR)continue;if(n<=0){failed=true;return false;}done+=n;read_bytes+=n;}return true;}
 bool unchanged()const{struct stat after{};return fstat(fd,&after)==0&&after.st_dev==before.st_dev&&after.st_ino==before.st_ino&&after.st_size==before.st_size&&after.st_mtim.tv_sec==before.st_mtim.tv_sec&&after.st_mtim.tv_nsec==before.st_mtim.tv_nsec&&after.st_ctim.tv_sec==before.st_ctim.tv_sec&&after.st_ctim.tv_nsec==before.st_ctim.tv_nsec;}
 int actual_flags()const{return fcntl(fd,F_GETFL);}
};
static const std::vector<std::string> roots={"logs","mobile/Library/Logs","root/Library/Logs","cores","log/com.apple.xpc.launchd"};
static const std::vector<std::string> tokens={"usermanager","migrat","lockdown","keybagd","fairplay","locationd","springboard","runningboard","backboard","mdmd","filecoordination","mediaremote","audiomx"};
static const std::vector<std::string> exact={"pong/libpong-ui18.dylib","pong/libpong-ui.dylib","Library/Preferences/com.apple.migration.plist","mobile/Library/Preferences/com.apple.migration.plist","Library/Preferences/com.apple.datamigrator.plist","mobile/Library/Preferences/com.apple.datamigrator.plist","db/.AppleSetupDone","preferences/FeatureFlags/Domain/AirPlay.plist","pong/etc/AirPlay-local-audio.plist"};
static bool selected(const std::string&p){
 if(within(p,"keybags"))return false;
 if(std::find(exact.begin(),exact.end(),p)!=exact.end())return true;
 std::string name=fs::path(p).filename().string(),n=lower(name);
 for(const auto&r:roots)if(within(p,r)){
  if(name.rfind("Pong",0)==0||ends(n,".ips")||ends(n,".crash"))return true;
  for(const auto&t:tokens)if(n.find(t)!=std::string::npos)return true;
 }
 if(name=="Info.plist" && (within(p,"pong/audio-hal18")||within(p,"pong/staged-audio18")||within(p,"Library/Audio/Plug-Ins/HAL")))return true;
 std::string par=fs::path(p).parent_path().string();
 if(par=="Library/Preferences"||par=="mobile/Library/Preferences")
  for(const auto&t:{"migrat","lockdown","setup","usermanager"})if(n.find(t)!=std::string::npos)return true;
 return false;
}
static std::string metadata(const std::string&p,const ApfsDir::Inode&i,uint64_t sz){
 std::ostringstream o,m;m<<"0o"<<std::oct<<(i.mode&07777);
 o<<"\"path\":"<<q(p)<<",\"bytes\":"<<sz<<",\"uid\":"<<i.owner<<",\"gid\":"<<i.group
 <<",\"mode\":"<<q(m.str())<<",\"mode_raw\":"<<i.mode<<",\"inode\":"<<i.obj_id<<",\"private_id\":"<<i.private_id
 <<",\"mtime_ns\":"<<i.mod_time<<",\"bsd_flags\":"<<i.bsd_flags
 <<",\"directory\":"<<(S_ISDIR(i.mode)?"true":"false")<<",\"symlink\":"<<(S_ISLNK(i.mode)?"true":"false");
 return o.str();
}
static void array(std::ostream&o,const std::vector<std::string>&v){o<<"[";for(size_t n=0;n<v.size();n++){if(n)o<<",";o<<"\n"<<v[n];}o<<"\n]";}
class Exporter{
 ApfsDir dir;ReadOnlyImage&image;fs::path output;std::set<uint64_t>seen_dirs;size_t nodes=0;uint64_t total=0;
 std::ofstream inventory;
public:
 std::vector<std::string>files,errors,skipped;std::string started=now();
 Exporter(ApfsVolume&v,ReadOnlyImage&i,const fs::path&o):dir(v),image(i),output(o),inventory(o/"inventory.jsonl"){if(!inventory)throw std::runtime_error("cannot create inventory");}
 void error(const std::string&p,const std::string&s){errors.push_back("{\"path\":"+q(p)+",\"error\":"+q(s)+"}");}
 void skip(const std::string&p,const std::string&s){skipped.push_back("{\"path\":"+q(p)+",\"reason\":"+q(s)+"}");}
 bool export_file(const std::string&p,const ApfsDir::Inode&i){
  uint64_t size=i.ds_size;
  if(i.internal_flags&INODE_HAS_UNCOMPRESSED_SIZE)size=i.uncompressed_size;
  std::vector<uint8_t>bytes,attr;
  bool compressed=(i.bsd_flags&APFS_UF_COMPRESSED)!=0;
  if(compressed){
   if(!dir.GetAttribute(attr,i.obj_id,"com.apple.decmpfs")||attr.size()<sizeof(CompressionHeader)){error(p,"missing/short decmpfs metadata");return false;}
   const auto*h=(const CompressionHeader*)attr.data();size=h->size;
   if(!IsDecompAlgoSupported(h->algo)){error(p,"unsupported decmpfs compression algorithm");return false;}
  }
  if(size>64ULL*1024*1024){skip(p,"over64MiB");return false;}
  if(size>1024ULL*1024*1024-total){skip(p,"total_export_limit_1GiB");return false;}
  if(compressed){
   if(!DecompressFile(dir,i.obj_id,bytes,attr)||image.failed||bytes.size()!=size){error(p,"decompression failed or size mismatch");return false;}
  }
  fs::path dest=output/"files"/fs::path(p);
  try{fs::create_directories(dest.parent_path());}catch(const std::exception&e){error(p,e.what());return false;}
  int fd=open(dest.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
  if(fd<0){error(p,std::string("output open: ")+strerror(errno));return false;}
  bool ok=true;uint64_t done=0;std::vector<uint8_t>chunk(1024*1024);
  while(done<size){
   size_t take=(size_t)std::min<uint64_t>(size-done,chunk.size());const uint8_t*data;
   if(compressed)data=bytes.data()+done;
   else{std::fill(chunk.begin(),chunk.end(),0);if(!dir.ReadFile(chunk.data(),i.private_id,done,take)||image.failed){ok=false;break;}data=chunk.data();}
   size_t at=0;while(at<take){ssize_t n=write(fd,data+at,take-at);if(n<0&&errno==EINTR)continue;if(n<=0){ok=false;break;}at+=n;}
   if(!ok)break;
   done+=take;
  }
  struct stat s{};if(fstat(fd,&s)||uint64_t(s.st_size)!=size||fsync(fd))ok=false;
  if(close(fd))ok=false;
  if(!ok){unlink(dest.c_str());error(p,"file export/read/write/flush failed; incomplete output removed");return false;}
  total+=size;files.push_back("{"+metadata(p,i,size)+",\"saved_as\":"+q("files/"+p)+",\"decompressed\":"+(compressed?"true":"false")+"}");
  std::cout<<"EXPORTED "<<p<<" "<<size<<"\n";return true;
 }
 void walk(uint64_t inode,const std::string&p,unsigned depth=0){
  if(++nodes>200000){error(p,"inventory node limit");return;}if(depth>128){error(p,"directory depth limit");return;}
  ApfsDir::Inode i;if(!dir.GetInode(i,inode)||image.failed){error(p,"GetInode/read failed");return;}
  uint64_t size=(i.internal_flags&INODE_HAS_UNCOMPRESSED_SIZE)?i.uncompressed_size:i.ds_size;
  inventory<<"{"<<metadata(p,i,size)<<"}\n";
  if(S_ISDIR(i.mode)){
   if(!seen_dirs.insert(inode).second){skip(p,"directory inode already traversed");return;}
   std::vector<ApfsDir::DirRec>entries;if(!dir.ListDirectory(entries,inode)||image.failed){error(p,"ListDirectory/read failed");return;}
   std::sort(entries.begin(),entries.end(),[](const auto&a,const auto&b){return a.name<b.name;});
   for(const auto&e:entries){
    if(e.name.empty()||e.name=="."||e.name==".."||e.name.find('/')!=std::string::npos||e.name.find('\0')!=std::string::npos){skip(p,"unsafe or dot directory name omitted");continue;}
    walk(e.file_id,p.empty()?e.name:p+"/"+e.name,depth+1);if(nodes>200000||image.failed)break;
   }
  }else if(selected(p)){
   if(S_ISREG(i.mode))export_file(p,i);else skip(p,"selected entry is not a regular file; symlinks are not followed");
  }
 }
 void manifest(const std::string&img,uint64_t offset,uint64_t size,const std::string&uuid,unsigned slot){
  inventory.flush();if(!inventory)error("inventory.jsonl","write failed");
  bool stable=image.unchanged();if(!stable)error(img,"source size/mtime/ctime/inode changed during extraction");
  std::ofstream o(output/"manifest.json");if(!o)throw std::runtime_error("manifest create failed");
  o<<"{\n\"started_utc\":"<<q(started)<<",\"finished_utc\":"<<q(now())<<",\"image\":"<<q(img)
   <<",\"image_bytes\":"<<image.GetSize()<<",\"host_readonly\":true,\"regular_image\":true,\"image_open_mode\":\"O_RDONLY|O_CLOEXEC|O_NOFOLLOW\""
   <<",\"image_actual_access_mode\":"<<(image.actual_flags()&O_ACCMODE)<<",\"source_stat_unchanged\":"<<(stable?"true":"false")
   <<",\"detached\":true,\"never_mounted\":true,\"fuse_used\":false,\"volume_root\":\"APFS inode 2\""
   <<",\"ownership_note\":\"uid/gid/mode are native APFS inode metadata, not host ownership mapping\""
   <<",\"apfs_partition_offset\":"<<offset<<",\"apfs_partition_bytes\":"<<size<<",\"apfs_volume_index\":"<<slot<<",\"apfs_volume_uuid\":"<<q(uuid)
   <<",\"inventory_file\":\"inventory.jsonl\",\"inventory_nodes\":"<<nodes<<",\"read_calls\":"<<image.read_calls<<",\"read_bytes\":"<<image.read_bytes
   <<",\"exported_bytes\":"<<total<<",\"files\":";array(o,files);o<<",\"errors\":";array(o,errors);o<<",\"skipped\":";array(o,skipped);o<<"\n}\n";o.flush();if(!o)throw std::runtime_error("manifest write failed");
  std::cout<<"SUMMARY files="<<files.size()<<" bytes="<<total<<" inventory="<<nodes<<" errors="<<errors.size()<<" skipped="<<skipped.size()<<"\n";
 }
};
int main(int argc,char**argv){
 try{
  if(argc!=3){std::cerr<<"Usage: apfs-direct-export REGULAR_IMAGE NEW_OUTPUT_DIRECTORY\n";return 64;}
  g_debug=0;g_lax=false;
  ReadOnlyImage image;if(!image.Open(argv[1]))throw std::runtime_error(std::string("read-only regular image open failed: ")+strerror(errno));
  uint64_t off=0,size=image.GetSize();GptPartitionMap gpt;
  if(gpt.LoadAndVerify(image)){int part=gpt.FindFirstAPFSPartition();if(part<0||!gpt.GetPartitionOffsetAndSize(part,off,size))throw std::runtime_error("no APFS GPT partition");}
  if(image.failed)throw std::runtime_error("image read error");
  ApfsContainer container(&image,off,size);if(!container.Init())throw std::runtime_error("container Init failed");
  const unsigned slot=1;apfs_superblock_t info{};if(!container.GetVolumeInfo(slot,info))throw std::runtime_error("Data slot 1 absent");
  std::string uuid=uuidstr(info.apfs_vol_uuid);for(char&c:uuid)c=std::toupper((unsigned char)c);
  if(uuid!="26FD8349-9B9A-4F22-89EB-C81E070D2AB2")throw std::runtime_error("unexpected Data volume UUID: "+uuid);
  if((info.apfs_fs_flags&APFS_FS_CRYPTOFLAGS)!=1)throw std::runtime_error("encrypted volume: no credential/key-content extraction supported");
  std::unique_ptr<ApfsVolume>volume(container.GetVolume(slot));if(!volume)throw std::runtime_error("volume init failed");
  fs::path out(argv[2]);if(!fs::create_directory(out))throw std::runtime_error("output exists; exclusive output required");
  fs::permissions(out,fs::perms::owner_all);fs::create_directory(out/"files");fs::permissions(out/"files",fs::perms::owner_all);
  Exporter exporter(*volume,image,out);exporter.walk(ROOT_DIR_INO_NUM,"");exporter.manifest(argv[1],off,size,uuid,slot);
  return exporter.errors.empty()?0:2;
 }catch(const std::exception&e){std::cerr<<"ERROR "<<e.what()<<"\n";return 1;}
}
