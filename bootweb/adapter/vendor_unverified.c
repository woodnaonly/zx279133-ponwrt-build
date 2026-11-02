/* SPDX-License-Identifier: GPL-2.0-only
 * INTENTIONAL HARD STOP. Neither the historical link addresses nor a JSON
 * profile can unlock this adapter. Replace only after measured relocation,
 * gd/x18, network DMA/cache, NAND ECC/BBT, watchdog and BTL rescue validation.
 * Never implement this using run_command("nand ...") or uploaded scripts. */
#include "platform.h"
int bw_vendor_attach(struct bw_platform *p,int argc,char *const argv[]) {
    (void)p;(void)argc;(void)argv;return -1;
}
