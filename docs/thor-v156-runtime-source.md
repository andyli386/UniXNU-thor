# Thor v156 runtime source checkpoint

These are the C source inputs to the locally compiled 22H374 runtime used
with Thor v156.

| File | Source stage | SHA-256 |
| --- | --- | --- |
| `ui_compat18-v156.c` | v156 SpringBoard and UI adapter | `73cba5699c736926550f45b87670122c1b5794556ac8a69587af33bcb292e9c4` |
| `settings-navigation-timing.c` | v155 Settings runtime carried into v156 | `b42e610e784b21e6dd77c283ceee809caab0d713b17a3fcf572e48cf415d1aab` |
| `touch.c` | v119 display-route touch runtime carried into v156 | `606a095e3935abe63394fdcad212c9236c9daacf676e893bd20bfbe40695157d` |

The files are byte-identical to those inputs. The adapter uses Mach-O build
UUIDs and checks instruction or Objective-C method signatures before selected
hooks, so it is tied to the matching 22H374 guest images. The three files
belong to this checkpoint under `userland/checkpoints/thor-v156`. The build
still needs the iPhoneOS SDK, `touch.h`, the matching `thor-app-exec.h`,
`settings_compat18.c`, `boot_snapshot18.c`, `sileo_spawn.c`, the v156
switcher source, Settings watchdog support and the matching Metal probe object.
The original build used matching Thor feature defines, CoreFoundation,
CoreGraphics, IOKit and Objective-C frameworks, and a locally signed injection
dylib with 4 KiB signature pages. On-device injection also needs the matching
XNU, launch and guest policy inputs.

The source checkpoint supports provenance and review. The separate opt-in
switcher branch integrates a smaller, build-checked tracing port with the
public device checkout.
