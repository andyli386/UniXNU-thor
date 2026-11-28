#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Put the packaged v237 image's SHA-256 into the boot script and the record (run after every repackage).
set -euo pipefail
out=${THOR_ROOT}/thor-out
sha=$(/opt/homebrew/bin/python3.13 -c "import json;print(json.load(open('$out/v237-unified/unified-verification.json'))['image_sha256'])")
actual=$(shasum -a 256 "$out/v237-unified/boot-thor-unixnu-v237-unified.img" | cut -d' ' -f1)
[ "$sha" = "$actual" ] || { echo "image differs from its receipt"; exit 2; }
/opt/homebrew/bin/python3.13 - "$out" "$sha" <<'PY'
import re, sys
from pathlib import Path
out, sha = Path(sys.argv[1]), sys.argv[2]
p = out / 'v237-intake/boot-v237.sh'
t = p.read_text()
t, n = re.subn(r'= [0-9a-f]{64} \] \|\| \{ echo "bad image"', '= %s ] || { echo "bad image"' % sha, t)
assert n == 1
p.write_text(t)
(out / 'v237-intake/image-sha256.txt').write_text(sha + '  v237-unified/boot-thor-unixnu-v237-unified.img\n')
PY
echo "v237 image sha $sha"
