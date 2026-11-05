/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_INSTALL_IMAGE_H
#define BW_INSTALL_IMAGE_H
#include "bootweb.h"
struct bw_install_parts {
    const void *prefix,*tail,*descriptor,*suffix;
    size_t prefix_bytes,tail_bytes,descriptor_bytes,suffix_bytes;
    const char *original_sha256,*candidate_sha256,*package_sha256;
};
/* Pure RAM preparation. No hardware callback, capability or write permission.
 * The original is preserved independently; only the fixed hook/tail/descriptor
 * can change. The complete reconstructed package is revalidated by the core. */
BW_API int bw_install_prepare(const struct bw_install_parts *,const void *original,size_t,
                              void *package,size_t capacity,size_t *package_bytes,
                              char error[BW_ERROR_SIZE]);
#endif
