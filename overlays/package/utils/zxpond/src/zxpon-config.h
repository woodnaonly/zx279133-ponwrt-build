/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef ZXPON_CONFIG_H
#define ZXPON_CONFIG_H
#include <stdint.h>
#include <zxpon-omci.h>
struct zx_config {
	struct omci_identity identity;
	uint8_t regid[36];
	int enabled, has_identity;
};
int zx_hex(const char *text, uint8_t *output, unsigned int width);
int zx_serial(const char *text, uint8_t output[8]);
int zx_registration(const char *text, const char *format, uint8_t output[36]);
int zx_migrate(const char *destination, const char *legacy);
int zx_config_load(struct zx_config *config, const char *directory);
#endif
