# v237: review of the modem server's first run on the device

2026-11-26. `v237-intake/auto-boot.sh` waits for the marker `review.ok` before it temp-boots v237.
This file records why the marker was written.

## What is new on the device

Up to v236 the modem trial ended its server at once: `THOR SERVE V228: smem partition rc=-2`
(`ThorMssServe.inc`, `linkStart()`).  v237 passes the modem's SMEM host id (1), so everything after
that lookup runs on the phone for the first time: the GLINK transport over the SMEM FIFOs, the QRTR
node, the memory-assignment SMC for the rmtfs buffer, the rmtfs file service, two read-only QMI
queries.  A panic there would kill the image about 100 s after boot, so the path was read before
the image is allowed to boot.

## Read by me (main session)

`QcomGlink.c` (whole file), `ThorMssServe.inc` (`linkStart`, `rfsSetup`, `assignBuffer`, `run`,
`pdmHandle`), `QcomSmem.c` (`qcom_smem_partition`, `walk_uncached`, `qcom_smem_find`,
`find_mss_*`), `ThorPasProbe.h`, the call site `ThorPas4Kernel.inc:250`.

- **Lengths from the modem are bounded before use.** `qcom_glink_rx_avail` / `qcom_glink_tx_avail`
  refuse head or tail values outside the FIFO; `handle_data` drops a chunk larger than the receive
  FIFO or one that does not fit the advertised intent (`QcomGlink.c:199-247`); an OPEN name is cut to
  `QCOM_GLINK_NAME` (`:284-290`); an INTENT list longer than the FIFO is refused (`:329`);
  an unknown command stops the transport (`QcomGlinkCorrupt`, `:396`) and `run()` leaves its loop on
  that (`ThorMssServe.inc`, `glink-corrupt`).
- **Every loop is bounded.** `qcom_glink_poll` handles at most one FIFO's worth of minimal messages
  per call; `run()` sleeps 2 ms per pass and ends at `kWatchMs` (24 s) or earlier
  (`ThorMssServe.inc:563-623`); the SMEM walks advance by at least one entry per step and stop on a
  bad canary (`QcomSmem.c:50-78`).
- **Every offset into shared memory is validated by its caller before it is read** (the accessor
  `qcom_smem_read32` itself is a raw read, `QcomSmem.c:13`): the table's count is bounded, a
  partition must fit below the table and carry a valid header and free marks
  (`qcom_smem_partition`, `QcomSmem.c:25-47`); the item walks check each entry against the
  partition's end and its canary (`:50-78`, `:96-160`).
- **Both locks are bounded**: the hardware mutex is tried 1000 times with 10 µs between tries under
  a local sleep lock and then given up (`PongPlatformResources.cpp:1179-1198`); the secure-call lock
  is a plain sleep mutex shared with the other SCM callers and released on every path
  (`assignBuffer`, `ThorMssServe.inc:360-383`).
- **The service's own parsing checks its bounds**: `pdmHandle` returns on a short message, checks
  the TLV lengths against the message, builds its reply in a fixed 416-byte buffer whose largest
  possible content is about 120 bytes (`ThorMssServe.inc:152-200`).
- **Failure paths do not dereference anything**: `linkStart` returns before `run()` continues when
  the transport is not up; `rfsSetup` logs and returns when a map fails (the service then refuses
  rmtfs requests instead of answering); `assignBuffer` logs the SMC result and only sets `gRfsReady`
  on 0.  The SMC retry loops are bounded (20 busy retries, 100001 resumes).
- **The probe's result is unchanged**: `ThorPas4Kernel.inc:250` returns true after `run()` whatever
  it did, so the audio processor still starts; only later (by up to 24 s).
- **Concurrency**: nothing runs on another thread or an interrupt; the IPCC doorbell is send-only
  and the server polls.  All state is file-static and touched from the one worker thread.
- What the observe pass saw on the real device matches what `linkStart` assumes: items 478/479/480
  exist in the APPS-to-modem partition (`part_off=0xdf000`), the modem wrote 8 bytes (its VERSION)
  and the descriptor layout is head/tail pairs for the two FIFOs (`v235-intake/console-v235-kernel.txt`,
  `THOR MSS GLINK OBS`).

Not checked by me: the SMC argument marshalling against Qualcomm's calling convention beyond the
comments in the file (an error there makes the secure world refuse the call, which is logged as a
non-zero `x0`), and protocol-level behaviour (whether the modem accepts this server's answers).

## Independent review

(read-only `Explore` agent, asked for null dereferences, unbounded copies, unbounded loops, the SMC
call, the lock, the time budget)

Verdict of the review: **safe to run** — "every identified failure ends the trial cleanly".  It
independently ruled out null maps (the caller guarantees them before `run`), modem lengths reaching a
fixed buffer (the QRTR receive path, `pdmHandle`, `rfsHandle`: every offset is checked in 64 bits),
FIFO cursors outside the FIFO, use of the rmtfs buffer before the assignment succeeded, divisions,
and unbounded loops (poll, flush, the SMC retries, the SMEM walks).  It confirmed that the SMC
marshalling matches Linux's `__qcom_scm_assign_mem` (a refusal comes back as an error code; the
secure world does not fault the kernel), that the two locks are never nested in this path, that all
state is touched from the one worker thread (no receive interrupt exists), and that `run()` returns
within about 27–28 s.

It found one read a corrupt shared-memory free mark could send past the mapping, and two smaller
points; all three are now in the image (the image was rebuilt and its checksum re-recorded):

| Guard | Where | Change |
| --- | --- | --- |
| A | `QcomSmem.c`, `walk_uncached` | a live free mark outside the partition is refused (`QcomSmemCorrupt`) instead of being walked to |
| B | `ThorMssServe.inc`, `assignBuffer` | the secure-call gate is released around the 30 ms busy-retry sleep, so other callers are not held out |
| C | `ThorMssServe.inc`, `pdmHandle` | the reply builder checks each TLV against the size of its buffer |

One functional point came with it and is also in: the trial used to end with "modem-smp2p-gone" on
*any* error of the modem-entry sample, including the "unstable" result that a sample returns
whenever the modem allocates shared memory during it.  Now only a missing entry ends the trial; an
unstable or unpublished sample skips that pass and is counted
(`THOR SERVE V237: modem entry samples read=N skipped=M`).

The modem server's host test (44 checks, address and undefined-behaviour sanitisers) passes against
the v237 sources: `v237-intake/serve-tests/run.sh` (copy of v228's test with the v237 driver path).

Not checked by the reviewer: the body of `pong_ufs_factory_nv`, `initLock`, the DMA lease
reservation itself, and the bodies of `ThorUimStatus::cards` and `ThorPdLocator::providers`.

**Decision:** the marker `review.ok` is written and v237 boots when the phone is back in fastboot.
If it should still die in the trial (panic on thread `thor_pas4` about 100 s after boot), the next
image turns the server off again with the device-tree property `thor-rmtfs-serve=0`; the call site
checks it (`ThorPas4Kernel.inc:250`).

