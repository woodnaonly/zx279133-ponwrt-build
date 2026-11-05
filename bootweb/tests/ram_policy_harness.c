/* SPDX-License-Identifier: GPL-2.0-only
 * Host-only exports of the exact native adapter's pure read policy.
 * This file is NEVER linked into either native artifact. */
#if !defined(BW_TEST_BASE) || !defined(BW_RAM_READONLY)
#error "host-only synthetic RAM policy tests"
#endif
#include "ram_readonly.h"
BW_API int bw_test_ram_read_allowed(uint32_t off,size_t n) {return bw_ram_read_allowed(off,n);}
BW_API int bw_test_ram_ecc_result(int rc,size_t done,size_t wanted) {return bw_ram_ecc_result(rc,done,wanted);}
