/* PongPower provides the real SMMU lease; without it (PONG_POWER=n),
 * these no-op stubs satisfy the kernel static linker. The NativeUfs
 * driver declares them weak_import but the kernel linker does not
 * honour weak imports for undefined symbols in .a files. */
#include <stdbool.h>

bool pong_smmu_lease_lock(void) { return true; }
void pong_smmu_lease_unlock(void) { }
