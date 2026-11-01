/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef ZXPON_TRANSPORT_H
#define ZXPON_TRANSPORT_H
#include <stddef.h>
#include <stdint.h>
/* Sends an already sealed baseline PDU. A zero result is an IDM completion
 * ACK, never an OLT acceptance or registration result. No Ethernet fallback.
 */
int zx_transport_send(const uint8_t *frame, size_t length);
int zx_genl_command(unsigned int command, const void *attributes, size_t length);
#endif
