/* SPDX-License-Identifier: GPL-2.0-only */
#include "internal.h"
int bw_identity_valid(const struct bw_identity *id) {
    return id&&bw_hex64(id->vendor_abi_sha256)&&bw_hex64(id->installed_sha256)&&
           bw_string(id->vendor_abi_sha256,BW_VENDOR_ABI_SHA256);
}
int bw_legacy_identity(const char *base,struct bw_identity *out) {
    if(!out||!bw_hex64(base)||!bw_string(base,BW_VENDOR_ABI_SHA256))return -1;
    bw_copy(out->vendor_abi_sha256,BW_VENDOR_ABI_SHA256,65);
    bw_copy(out->installed_sha256,base,65);return 0;
}
