# Reading Apple's 22H374 binaries on this Mac (host-only analysis)

Used for v241 (wallpaper root cause, Wi-Fi and cellular research).  Everything is read-only.

## On-disk binaries of the OS volume (launchd, wifid, configd, PosterBoard, SpringBoard, ...)
    import osimg                                   # session-tools
    osimg.data("/usr/sbin/wifid") -> bytes;  osimg.node(path);  osimg.listing(dir)
    python3 calls.py <binary> <hex addr> [--raw]    annotated calls, strings and skipping branches of one function
    python3 xref.py <binary> "<c-string>"           functions that reference a string (also through constant CFStrings)
    from macho import Macho                          .starts, .func(addr), .stub(addr), .cstr(addr), .names
LC_MAIN.entryoff is `main` itself in these binaries.

## The dyld shared cache (frameworks)
Do not copy it (3.8 GB).  Attach the SystemCryptex image read-only:
    mkdir -p <mnt> && hdiutil attach -readonly -noverify -noautofsck -nobrowse -mountpoint <mnt> \
        thor-out/ipsw-components/components/094-33089-038.dmg
    C=<mnt>/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64        (60 sub-caches, .symbols)
    ipsw dyld str "$C" "text"                                   fast: address "string" image=Name
    ipsw dyld imports "$C" WallpaperKit                         images that link a framework (full path if ambiguous)
    ipsw dyld disass --cache <a2s> --image <Name> "$C" > out    named disassembly; or --vaddr 0x.. --count N
    ipsw dyld extract --force --stubs --cache <a2s> -o <dir> "$C" <path>      one image (~50 MB: delete when done)
    ipsw dyld objc sel -i <Name> "$C" > sel.txt                 selector addresses of one image (for objc_methods.py)
    python3 objc_methods.py <extracted dylib> --sel sel.txt 'pattern'         ObjC method addresses
Build the symbol cache once (1.2 GB) with the first command that takes --cache; pass it afterwards.
ipsw is picky about argument order: `ipsw dyld <cmd> <DSC> <args> [flags]`.  Detach when finished (hdiutil detach <mnt>).

## Facts learned this way
- Interposing (`__DATA,__interpose` in a DYLD_INSERT_LIBRARIES library) also catches calls made from shared-cache
  images (checked on this Mac); an interpose entry must bind to its target by install name (not a flat-namespace bind).
- WallpaperKit: catalogs are named `~<lowercase GSDeviceName>`; the logicalScreenClass check can be switched off only
  through the two `-[NSUserDefaults(WKAdditions) wk_ignore*ForWallpaperBundle]` getters; without a lookup file it
  ends in a "Solid Black" bundle.
- WallpaperKit is linked by SpringBoard.framework, CoverSheet (chronod) and CollectionsPoster.
- Helper scripts of the two research agents are in `session-tools/apple-research/` (`kexts.py`: tolerant parser of the
  prelink plist of Apple's kernelcache; `iocalls.py`: IOConnectCall selectors of an extracted dylib; `callers.py`,
  `wf.py`/`xrim.py`/`fn.py`/`lit.py`/`imports.py`: index and annotate a stripped executable such as wifid).
