/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_LWIP_CC_H
#define BW_LWIP_CC_H
#include <stdint.h>
#include <stddef.h>
#define BYTE_ORDER LITTLE_ENDIAN
#define LWIP_PLATFORM_DIAG(message) do { } while(0)
#define LWIP_PLATFORM_ASSERT(message) bw_lwip_assert()
#define LWIP_NO_INTTYPES_H 1
#define LWIP_NO_CTYPE_H 1
#define LWIP_NO_UNISTD_H 1
#define X8_F "02x"
#define U16_F "u"
#define S16_F "d"
#define X16_F "x"
#define U32_F "u"
#define S32_F "d"
#define X32_F "x"
#define SZT_F "lu"
#define PACK_STRUCT_STRUCT __attribute__((packed))
uint32_t bw_lwip_random(void);
_Noreturn void bw_lwip_assert(void);
#endif
