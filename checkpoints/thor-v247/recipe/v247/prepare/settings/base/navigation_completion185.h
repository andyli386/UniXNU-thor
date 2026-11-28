#pragma once
#include <objc/objc.h>
#include <stdbool.h>

enum navigation_outcome {
    NAV_WAITING = 1, NAV_NOT_ANIMATED, NAV_NO_COORDINATOR, NAV_OTHER_TARGET,
    NAV_DECLINED, NAV_FINISHED, NAV_CANCELLED
};
typedef void (*navigation_observer)(void *, enum navigation_outcome);
void observe_navigation_completion(id owner, id target, bool pushing, bool animated,
                                  void *context, navigation_observer observer);
