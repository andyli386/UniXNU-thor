# FULLSEQ one-use controller

`tools/thorctl/fullseq_controller.py` is the single reviewed controller template for FULLSEQ runs. It contains the preparation, read-only check, one-use `fastboot boot`, and Android monitor paths. A run directory receives a sealed copy of this file plus `sealed-inputs.json`; the copy is evidence for that run and is never edited after preparation.

Prepare a new run by invoking the canonical template directly. The arguments are the run-specific artifacts; the controller logic is not regenerated:

```bash
python3 tools/thorctl/fullseq_controller.py prepare \
  --root /Users/<user>/projects/thor-unixnu \
  --run-id thor-YYYYMMDD-ios-fullseqNN-purpose \
  --offline-proof runs/<run-id>/l2/package/independent-offline.json \
  --manifest runs/<run-id>/l2/package/manifest.json \
  --firmware-release runs/<run-id>/l2/firmware-release.json \
  --source worktrees/uefi/<worktree> \
  --fd runs/<run-id>/l2/package/SILICIUM_UEFI.fd \
  --image runs/<run-id>/l2/package/thor-boot.img \
  --device-proof runs/<run-id>/l2/trusted-device-baseline.json \
  --pins runs/<run-id>/l2/controller-pins.json \
  --adb "$ANDROID_HOME/platform-tools/adb" \
  --fastboot "$ANDROID_HOME/platform-tools/fastboot"
```

`prepare` performs no phone I/O. It refuses a dirty source worktree, a consumed image, an existing `l3`, or a mismatched SHA. The user still starts the recording and invokes the sealed run copy for the single explicitly authorized attempt:

```bash
python3 runs/<run-id>/l2/controller/controller.py execute \
  --preflight runs/<run-id>/l2/controller/controller-preflight.json \
  --recording-confirmed \
  --confirm-run-id thor-YYYYMMDD-ios-fullseqNN-purpose \
  --confirm-image-sha256 <sha256>
```

The template is versioned with the controller tooling. The run copy is intentionally duplicated because its SHA is part of the immutable execution receipt; duplicating that sealed artifact is different from rewriting the controller implementation each round.

For the conventional layout, the path list can be reduced to:

```bash
python3 tools/thorctl/prepare_fullseq_controller.py \
  --root /Users/<user>/projects/thor-unixnu \
  --run-id thor-YYYYMMDD-ios-fullseqNN-purpose \
  --source worktrees/uefi/<worktree>
```
