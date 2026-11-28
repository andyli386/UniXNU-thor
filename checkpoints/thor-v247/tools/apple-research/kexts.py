#!/usr/bin/env python3
"""Prelink info of an Apple kernelcache whose XML plist uses interned values (<string ID="n">, <string IDREF="n"/>,
<integer ID="n" size="64">0x..</integer>). plistlib cannot read it.
usage: kexts.py <kc.macho> list [substr...]        -> load addr, size, bundle id, version
       kexts.py <kc.macho> dump <bundle-substr>     -> full prelink dict of matching kexts (JSON)
       kexts.py <kc.macho> classes <bundle-substr>  -> IOKitPersonalities of matching kexts
       kexts.py <kc.macho> find <substr>            -> kexts whose dict text mentions substr
       kexts.py <kc.macho> save <out.json>          -> whole _PrelinkInfoDictionary as JSON
"""
import re, struct, sys, json, html

def load(path):
    f = open(path, "rb")
    magic, _ct, _cs, _ft, ncmds, sizeofcmds, _fl, _res = struct.unpack("<IiiIIIII", f.read(32))
    assert magic == 0xFEEDFACF
    cmds = f.read(sizeofcmds)
    segs, sects = [], []
    off = 0
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", cmds, off)
        if cmd == 0x19:
            name = cmds[off+8:off+24].split(b"\0")[0].decode()
            vmaddr, vmsize, fileoff, filesize, _mp, ip, nsects, _f = struct.unpack_from("<QQQQiiII", cmds, off+24)
            segs.append((name, vmaddr, vmsize, fileoff, filesize))
            so = off + 72
            for _s in range(nsects):
                sn = cmds[so:so+16].split(b"\0")[0].decode(); sg = cmds[so+16:so+32].split(b"\0")[0].decode()
                addr, ssize, soff = struct.unpack_from("<QQI", cmds, so+32)
                sects.append((sg, sn, addr, ssize, soff)); so += 80
        off += size
    f.seek(0)
    return f, segs, sects

def section(f, sects, seg, name):
    for s in sects:
        if s[0] == seg and s[1] == name:
            f.seek(s[4]); return f.read(s[3]), s[2]
    return None, 0

TOKEN = re.compile(rb"<key>(.*?)</key>|<(string|integer|data|date)([^>]*)>([^<]*)</\2>|<(true|false)/>|<(string|integer|data|date) IDREF=\"(\d+)\"/>|<(dict|array)>|</(dict|array)>|<plist[^>]*>|</plist>", re.S)

def parse(xml):
    ids = {}
    # pass 1: interned values
    for m in re.finditer(rb'<(string|integer|data) ID="(\d+)"[^>]*>([^<]*)</\1>', xml, re.S):
        kind, i, raw = m.group(1), int(m.group(2)), m.group(3)
        ids[i] = (kind, raw)
    def value(kind, attrs, raw):
        size = re.search(rb'size="(\d+)"', attrs)
        if kind == b"integer":
            s = raw.strip()
            if s.startswith(b"0x") or s.startswith(b"-0x"):
                neg = s.startswith(b"-")
                v = int(s.lstrip(b"-"), 16)
                return -v if neg else v
            return int(s) if s else 0
        if kind == b"string": return html.unescape(raw.decode("utf8", "replace"))
        if kind == b"data": return raw
        return raw.decode("utf8", "replace")
    tokens = []
    for m in TOKEN.finditer(xml):
        g = m.groups()
        if g[0] is not None: tokens.append(("key", html.unescape(g[0].decode("utf8", "replace"))))
        elif g[1] is not None:
            attrs = g[2] or b""
            idm = re.search(rb' ID="(\d+)"', attrs)
            v = value(g[1], attrs, g[3])
            tokens.append(("val", v))
        elif g[4] is not None: tokens.append(("val", g[4] == b"true"))
        elif g[5] is not None:
            kind, raw = ids[int(g[6])]
            tokens.append(("val", value(kind, b"", raw)))
        elif g[7] is not None: tokens.append(("<" + g[7].decode() + ">", None))
        elif g[8] is not None: tokens.append(("</" + g[8].decode() + ">", None))
    i = [0]
    def p():
        kind = tokens[i[0]][0]; i[0] += 1
        if kind == "<dict>":
            d = {}
            while tokens[i[0]][0] != "</dict>":
                k = tokens[i[0]][1]; i[0] += 1
                d[k] = p()
            i[0] += 1; return d
        if kind == "<array>":
            a = []
            while tokens[i[0]][0] != "</array>":
                a.append(p())
            i[0] += 1; return a
        return tokens[i[0]-1][1]
    return p()

def get(path):
    f, segs, sects = load(path)
    data, base = section(f, sects, "__PRELINK_INFO", "__info")
    start = data.find(b"<dict>"); end = data.rfind(b"</dict>")
    xml = data[start:end+7]
    pl = parse(xml)
    return pl, segs, sects

def main():
    pl, segs, sects = get(sys.argv[1])
    pre = pl.get("_PrelinkInfoDictionary", [])
    cmd = sys.argv[2]
    if cmd == "list":
        subs = sys.argv[3:]
        for k in pre:
            bid = k.get("CFBundleIdentifier", "")
            if subs and not any(s.lower() in bid.lower() for s in subs): continue
            print("%#x +%#x  %s  v%s" % (k.get("_PrelinkExecutableLoadAddr") or 0, k.get("_PrelinkExecutableSize") or 0, bid, k.get("CFBundleVersion")))
        print("# %d kexts" % len(pre))
    elif cmd == "dump":
        for k in pre:
            if sys.argv[3].lower() in k.get("CFBundleIdentifier", "").lower():
                print("=====", k.get("CFBundleIdentifier"))
                print(json.dumps(k, indent=1, default=lambda o: "<data %d bytes>" % len(o)))
    elif cmd == "classes":
        for k in pre:
            if sys.argv[3].lower() in k.get("CFBundleIdentifier", "").lower():
                print("=====", k.get("CFBundleIdentifier"))
                print(json.dumps(k.get("IOKitPersonalities"), indent=1, default=lambda o: "<data %d bytes>" % len(o)))
    elif cmd == "save":
        json.dump(pre, open(sys.argv[3], "w"), default=lambda o: "<data %d bytes>" % len(o))
        print("saved", len(pre))
    elif cmd == "find":
        for k in pre:
            if sys.argv[3].lower() in json.dumps(k, default=str).lower():
                print(k.get("CFBundleIdentifier"))

main()
