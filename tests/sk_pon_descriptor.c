/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdio.h>
#include "zx279133-omci-wire.h"
int main(void)
{
	assert(zx279133_omci_length_word(48) == 0x0f000060);
	assert(zx279133_omci_control_word() == 0x00400001);
	assert(zx279133_omci_gem_word(0x1234) == 0x1e071234);
	assert(zx279133_omci_gem_word(0) == 0x1e070000);
	/* No ETH_ZLEN padding, Ethernet port, or data-side 0x08000000 word. */
	assert((zx279133_omci_length_word(48) & 0x7ffe) == 96);
	assert((zx279133_omci_control_word() & 0x3f0000) == 0);
	puts("PASS: vendor-traced OMCI queue-0 length/control/GEM descriptor words");
	return 0;
}
