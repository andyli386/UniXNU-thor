#!/usr/bin/env python3.13
"""v248: the witness changes, applied to the kernel tree of a working copy (every replacement must match once).

    python3.13 -I <this> <work>/build-tree/.cache/xnu-work/osfmk/kern/thor_witness_v235.h

  1. the first page of a panic (drawn in the panic hook, before the kernel's own panic path) names the fault:
     pc, lr, sp, fp with their symbols, esr and far, the thread, and the callers of panic.  Until now the page
     held only the unformatted panic string, and the panic of v247 (2026-11-28) stopped before the panic log;
  2. once that page is drawn, the monitor no longer draws a hang report over it;
  3. the version line.
"""
import sys
from pathlib import Path

path = Path(sys.argv[1])
t = path.read_text()


def rep(old, new, count=1):
    global t
    assert t.count(old) == count, (t.count(old), old[:80])
    t = t.replace(old, new)


# ---- 1. what sleh.c hands over
rep('''static uint64_t thor_witness_fault_pc, thor_witness_fault_lr, thor_witness_fault_sp, thor_witness_fault_fp;
''', '''static uint64_t thor_witness_fault_pc, thor_witness_fault_lr, thor_witness_fault_sp, thor_witness_fault_fp;
static uint64_t thor_witness_fault_esr, thor_witness_fault_far, thor_witness_fault_cpsr;   /* v248: of the same exception */
static volatile unsigned thor_witness_panic_noted;                                      /* v248: the panic page is on the panel */
''')

rep('''		thor_witness_fault_sp = saved->ss_64.sp;
		thor_witness_fault_fp = saved->ss_64.fp;
	}
}
''', '''		thor_witness_fault_sp = saved->ss_64.sp;
		thor_witness_fault_fp = saved->ss_64.fp;
		thor_witness_fault_esr = saved->ss_64.esr;
		thor_witness_fault_far = saved->ss_64.far;
		thor_witness_fault_cpsr = saved->ss_64.cpsr;
	}
}
''')

# ---- 2. the monitor leaves the panic page alone
rep('''		const uint64_t now = mach_absolute_time();
		const uint64_t uptime = thor_witness_micros(now);
		const uint64_t reader_raw = __atomic_load_n(&thor_witness_reader_abs, __ATOMIC_RELAXED);
''', '''		const uint64_t now = mach_absolute_time();
		const uint64_t uptime = thor_witness_micros(now);
		const uint64_t reader_raw = __atomic_load_n(&thor_witness_reader_abs, __ATOMIC_RELAXED);
		if (__atomic_load_n(&thor_witness_panic_noted, __ATOMIC_RELAXED)) {
			/* v248: a panic page is on the panel; no hang report is drawn over it */
			delay_for_interval(THOR_WITNESS_PERIOD_MS, NSEC_PER_MSEC);
			continue;
		}
''')

# ---- 3. the panic page
start = t.index('/* Called from PE_panic_hook: the panic string is known, the log is not written yet.')
end = t.index('/* Called from panic_spin_forever(): the panic log is complete.')
assert start < end
new_note = r'''#define THOR_WITNESS_NOTE_BYTES   4096u

/* One line into the panic note, within its room. */
static size_t thor_witness_note_line(char *note, size_t length, const char *format, ...) __printflike(3, 4);

static size_t
thor_witness_note_line(char *note, size_t length, const char *format, ...)
{
	va_list args;
	int n;
	if (length + THOR_WITNESS_LINE > THOR_WITNESS_NOTE_BYTES) {
		return length;
	}
	va_start(args, format);
	n = vsnprintf(note + length, THOR_WITNESS_NOTE_BYTES - length, format, args);
	va_end(args);
	if (n > 0) {
		const size_t room = THOR_WITNESS_NOTE_BYTES - length;
		length += (size_t)n < room ? (size_t)n : room - 1;
	}
	return length;
}

/* Called from PE_panic_hook: the panic string is known, the log is not written yet.  This is the
 * first code of ours that runs on the panicking thread's stack inside the panic, so the frame
 * records from here lead straight to whoever called panic(); they are read now.
 *
 * v248: this page is the only one that is certain to be shown.  On 2026-11-28 (v247) the panic of
 * 43.6 s stopped in the kernel's own panic path: the panic log never came, no record was stored,
 * and the page held nothing but the unformatted panic string.  So the page now says what is known
 * here: the faulting pc, lr, sp and fp with their symbols (sleh.c handed them over), esr and far,
 * the thread, and the callers of panic.  No lock, no allocation, no I/O; the record is still left
 * to panic_spin_forever, where the other CPUs are halted and the storage is not in use. */
void
thor_witness_panic_note(const char *str)
{
	static char note[THOR_WITNESS_NOTE_BYTES];
	static char name[THOR_WITNESS_NAMELEN];
	const thread_t thread = current_thread();
	const task_t task = get_threadtask(thread);
	const uint64_t low = (uint64_t)thread->kernel_stack;
	size_t length = 0;
	unsigned c, rows = 2;
	if (!str) {
		return;
	}
	if (low && !thor_witness_panic_chain_count) {
		thor_witness_panic_chain_count = thor_witness_chain(low, low + kernel_stack_size,
		    (uint64_t)__builtin_frame_address(0), thor_witness_panic_chain, THOR_WITNESS_CHAIN_MAX);
	}
	length = thor_witness_note_line(note, length, "THOR PANIC V248: at the panic hook (kernel time %llu us)\n",
	    (unsigned long long)thor_witness_micros(mach_absolute_time()));
	if (task) {
		name[0] = 0;
		task_procname(task, name, (int)sizeof name);
		name[sizeof name - 1] = 0;
	}
	length = thor_witness_note_line(note, length, " thread %p of pid %d %s\n", thread,
	    task ? task_pid(task) : -1, task ? name : "-");
	if (thor_witness_fault_pc) {
		char text[160], text2[160];
		thor_witness_describe(thor_witness_fault_pc, text, sizeof text);
		thor_witness_describe(thor_witness_fault_lr, text2, sizeof text2);
		length = thor_witness_note_line(note, length, " fault pc=%llx %s\n",
		    (unsigned long long)thor_witness_fault_pc, text);
		length = thor_witness_note_line(note, length, "       lr=%llx %s\n",
		    (unsigned long long)thor_witness_fault_lr, text2);
		length = thor_witness_note_line(note, length, "       sp=%llx fp=%llx esr=%llx far=%llx\n",
		    (unsigned long long)thor_witness_fault_sp, (unsigned long long)thor_witness_fault_fp,
		    (unsigned long long)thor_witness_fault_esr, (unsigned long long)thor_witness_fault_far);
	}
	length = thor_witness_note_line(note, length, " callers of panic, newest first (%u):\n",
	    thor_witness_panic_chain_count);
	for (c = 0; c < thor_witness_panic_chain_count; c++) {
		char text[160];
		thor_witness_describe(thor_witness_panic_chain[c], text, sizeof text);
		length = thor_witness_note_line(note, length, "  %u: %llx %s\n", c,
		    (unsigned long long)thor_witness_panic_chain[c], text);
	}
	{
		char line[THOR_WITNESS_LINE];
		size_t i;
		for (i = 0; i < sizeof line - 1 && str[i] && str[i] != '\n'; i++) {
			line[i] = str[i] >= 32 && str[i] < 127 ? str[i] : '?';
		}
		line[i] = 0;
		length = thor_witness_note_line(note, length, " panic string: %s\n", line);
	}
	note[length < THOR_WITNESS_NOTE_BYTES ? length : THOR_WITNESS_NOTE_BYTES - 1] = 0;
	__atomic_store_n(&thor_witness_panic_noted, 1u, __ATOMIC_RELAXED);
	{
		uint32_t *base;
		unsigned columns, panel_rows, rowpixels;
		if (!thor_witness_panel(&base, &columns, &panel_rows, &rowpixels)) {
			return;
		}
		rows = thor_witness_rows(note, length, columns - 2);
	}
	thor_witness_page("KERNEL PANIC", note, length, 0, rows, 0, 1);
}

'''
t = t[:start] + new_note + t[end:]

# ---- 4. the version line
start = t.index('\tprintf("THOR WITNESS V247: every process that dies by a signal prints one line')
end_marker = '"v235 witness\\n");\n'
end = t.index(end_marker, start) + len(end_marker)
assert start < end
t = t[:start] + '''	printf("THOR WITNESS V248: the first page of a panic names the fault (pc, lr, sp, fp with their symbols, esr, far), the "
	    "thread and the callers of panic, drawn in the panic hook before the kernel's own panic path (v247: only the unformatted "
	    "panic string was shown, and the panic of 2026-11-28 stopped before the panic log); the camera inventory is off, /chosen has "
	    "no thor-camera (v247 panicked 43.6 s after boot, when its inventory was due); carried: every death by a signal prints one "
	    "line (PONG DEATH), the log capture library v247, the display library that answers the panel's size also for a display with "
	    "no size of its own, the UI adapter in PosterBoard, the Wi-Fi station's retries, everything of v247 to v239, the v236 GPU "
	    "timing hook, the v235 witness\\n");
''' + t[end:]

path.write_text(t)
print('patched', path, len(t), 'bytes')
