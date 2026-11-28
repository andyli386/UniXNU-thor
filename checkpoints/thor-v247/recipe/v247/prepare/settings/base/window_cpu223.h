#ifndef THOR_WINDOW_CPU223_H
#define THOR_WINDOW_CPU223_H
#include <stdint.h>
#include <stdbool.h>
#define THOR_WINDOW_LOCAL __attribute__((visibility("hidden")))
struct window_cpu_receipt {
    uint64_t touch_us, window_us;
    int64_t cpu_us, send_wall_us, send_cpu_us;
    unsigned in_window;
};
THOR_WINDOW_LOCAL bool install_window_cpu223(void);
THOR_WINDOW_LOCAL void match_window_cpu223(uint64_t push_us, struct window_cpu_receipt *result);
THOR_WINDOW_LOCAL void stop_window_cpu223(void);
THOR_WINDOW_LOCAL uint64_t settings_trace_uptime223(void);
#endif
