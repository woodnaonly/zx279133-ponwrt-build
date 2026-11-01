/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef ZXPON_BACKEND_H
#define ZXPON_BACKEND_H
#include <stddef.h>
#include <stdint.h>
#include <zxpon-omci.h>
int zx_backend_apply(void *context, const uint8_t *paths, size_t length);
int zx_backend_dispatch(struct omci_agent *agent, const uint8_t *input, size_t length, uint8_t output[2048]);
#endif
