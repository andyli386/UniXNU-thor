"""Verify release by parsing Android v4 and both FAT16 copies directly, independently of mtools."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tomllib

H = lambda b:hashlib.sha256(b).hexdigest()
U16 = lambda b,o:struct.unpack_from("<H",b,o)[0]
U32 = lambda b,o:struct.unpack_from("<I",b,o)[0]
out = Path(__file__).resolve().parents[1]
def decode(path):
    raw = path.read_bytes()
    assert raw[:8] == b"ANDROID!" and U32(raw,40)==4
    start = 4096 + ((U32(raw,8)+4095)//4096)*4096
    size=U32(raw,12)
    body=memoryview(raw)[start:start+size]
    assert start+size+U32(raw,1580)==len(raw)
    sec=U16(body,11)
    cluster=int(body[13])*sec
    table_start=U16(body,14)*sec
    table_size=U16(body,22)*sec
    copies=body[16]
    assert sec==512 and copies==2
    table=body[table_start:table_start+table_size]
    assert table==body[table_start+table_size:table_start+2*table_size]
    roots=table_start+copies*table_size
    roots_size=((U16(body,17)*32+sec-1)//sec)*sec
    first=roots+roots_size
    members={}
    used=set()
    def content(index):
        chunks=[]
        while index<0xfff8:
            assert 2<=index<0xfff0 and index not in used
            used.add(index)
            off=first+(index-2)*cluster
            assert off+cluster<=len(body)
            chunks.append(body[off:off+cluster])
            index=U16(table,index*2)
        return b"".join(chunks)
    def directory(name, entries):
        long={}
        for ofs in range(0,len(entries),32):
            entry=entries[ofs:ofs+32]
            if not entry[0]:break
            if entry[0]==0xe5:
                long={};continue
            attr=entry[11]
            if attr==0xf:
                order=entry[0]&0x3f
                if entry[0]&0x40:long={}
                long[order]=bytes(entry[1:11])+bytes(entry[14:26])+bytes(entry[28:32])
                continue
            base=bytes(entry[:8]).decode("cp437").rstrip()
            ext=bytes(entry[8:11]).decode("cp437").rstrip()
            short=base+("."+ext if ext else "")
            leaf=(b"".join(long[i] for i in range(1,len(long)+1)).decode("utf-16-le").split("\0",1)[0] if long else short)
            long={}
            if attr&8 or leaf in (".",".."):continue
            key=name+"/"+leaf.casefold()
            cid=U16(entry,26)
            data=content(cid) if cid else b""
            if attr&0x10:directory(key,data)
            else:
                size=U32(entry,28)
                assert size<=len(data) and key not in members
                members[key]=data[:size]
    directory("",body[roots:roots+roots_size])
    front=bytearray(raw[:start]);front[12:16]=bytes(4)
    return members,H(front),H(raw[start+size:]),H(raw)


