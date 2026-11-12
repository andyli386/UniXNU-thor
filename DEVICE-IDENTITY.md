# Thor device identity (<device-serial>)

## Software platform UUID

```
cec07c12-bd31-53ef-91a1-84ce8f3ac203
```

- Deterministic: `uuid5(NAMESPACE_OID, "thor-<device-serial>")` — the fastboot
  serial of this phone. Never regenerate or rotate.
- Consumed by the `pong_platform_uuid=` boot argument; `ArmDTPlatform`
  validates it (exactly 36 chars, `uuid_parse`, non-null) and publishes
  the `IOPlatformUUID` resource, which `gethostuuid()`/`timed` wait for
  with no deadline. Without it the CoreLocation migration plugin freezes
  the whole bootstrap chain (v63 field evidence, 2026-11-12).
- `local.toml` (`[pong_build].platform_uuid`) carries this value for full
  rebuilds, but local.toml is machine-local and gitignored — this file is
  the tracked record.
- The thor boot images do NOT go through
  `build-ipados18-peripheral-boot.py`; the value is written directly into
  the image's `unixnu.toml` args. When packing a new boot image, verify
  the final FAT toml actually contains:
  `pong_platform_uuid=cec07c12-bd31-53ef-91a1-84ce8f3ac203`
  (args budget: 860 of 1024 bytes as of v64).
