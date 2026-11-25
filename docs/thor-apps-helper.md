# Thor system-app helper

The Thor v156 image retains the v129 install helper. It validates each bundle and calls the system app migrator while tracking native coordinator results. The source targets iPadOS 18.7.10 build 22H374 and uses a bounded inventory of system bundles approved for that build.

Build on macOS with an iPhoneOS SDK and a local inventory prepared from the verified system-app source of the matching restore. An inventory entry is a C initializer for the bundle directory name, `CFBundleIdentifier`, `CFBundleExecutable`, and the 32 bytes of the executable's SHA-256 digest. Keep this release inventory and the restore inputs local.

```sh
python3 scripts/build-thor-apps-install.py \
  --inventory /path/to/app-inventory.inc \
  --output build/thor-apps-install
```

The recipe compiles, ad-hoc signs and verifies the arm64 executable. The local development image stages its matched helper through the Thor kernel build. The SpringBoard runtime checks the helper's size and SHA-256, and the kernel checks its CodeDirectory hash before assigning the dedicated install sandbox role. Recompiled helpers require a matching runtime and kernel policy.

This source checkpoint covers the installer and host build recipe. Image staging, sandbox policy integration and device verification of the rebuilt helper are separate work.
