#!/bin/sh
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [ ! -x "$ROOT/toolchains/python313/bin/python3" ]; then
    ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
fi
exec "$ROOT/toolchains/python313/bin/python3" "$ROOT/repos/UniXNU-thor/tools/thorctl.py" --workspace "$ROOT" "$@"
