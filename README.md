# UniXNU-thor

Format 2 device repository for Xiaomi 12S Ultra (`thor`) on Qualcomm SM8475.
The selected OS profile is Darwin 22 / iOS 16.6.1 build 20G81, matching the
existing Pong kernel source pins while keeping Thor's UEFI and DT ownership
separate.

## Current migration boundary

The repository currently qualifies the ARM64 platform/CPU path, RAM-backed block
path, prelink tool ownership and the Thor firmware/DTS boundary. `drivers/ThorUfs`,
`drivers/ThorAdreno`, `drivers/ThorMetal` and `drivers/ThorDarwinBoot22` are
staged source migrations from Pong; they are not claimed as upstream canonical
components until their board and OS interfaces are reviewed.

Run the resolver from the sibling UniXNU checkout with Python 3.11 or newer:

```sh
cd ../UniXNU
PYTHON=python3.13
$PYTHON -B ./unixnu defconfig thor_defconfig -D ../UniXNU-thor --local-module soc:sm8475=../UniXNU-sm8475-common
$PYTHON -B ./unixnu status --json -D ../UniXNU-thor --local-module soc:sm8475=../UniXNU-sm8475-common
$PYTHON -B ./unixnu fetch -D ../UniXNU-thor --local-module soc:sm8475=../UniXNU-sm8475-common
$PYTHON -B ./unixnu tree -D ../UniXNU-thor --local-module soc:sm8475=../UniXNU-sm8475-common
$PYTHON scripts/porting-record.py check --device ../UniXNU-thor
```

The subsequent build order is `ipsw fetch`, `kernel`, `package`, `firmware`,
`payload`, and `bootimg`. Private IPSW and firmware inputs belong in `inputs/`
and `unixnu.local.toml`; no harness, run receipt, or old image is a source.
