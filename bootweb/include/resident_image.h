/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_RESIDENT_IMAGE_H
#define BW_RESIDENT_IMAGE_H
#include "bootweb.h"
#define BW_RESIDENT_SLOT 0x100000u
#define BW_RESIDENT_DESCRIPTOR 0x15ff00u
#define BW_RESIDENT_DESCRIPTOR_BYTES 256u
/* Validates the complete fixed-physical stage2. The original ABI hash is
 * reconstructed with ONLY the audited hook and erased tail normalized. */
BW_API int bw_resident_image_check(const void *,size_t,char installed[65]);
/* Returns the installed descriptor's profile only AFTER complete validation.
 * A RAM payload must never label an older installed image as its own version. */
BW_API const char *bw_resident_image_version(const void *,size_t);
#endif
