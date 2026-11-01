/* SPDX-License-Identifier: GPL-2.0-only */
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "zxpon-config.h"

static int nibble(unsigned char ch)
{
	if (ch >= '0' && ch <= '9') return ch - '0';
	if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
	if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
	return -1;
}

int zx_hex(const char *text, uint8_t *output, unsigned int width)
{
	unsigned int i;
	if (!text || strlen(text) != width * 2) return -EINVAL;
	for (i = 0; i < width; i++)
		if (nibble(text[i * 2]) < 0 || nibble(text[i * 2 + 1]) < 0) return -EINVAL;
	for (i = 0; i < width; i++)
		output[i] = (uint8_t)((nibble(text[i * 2]) << 4) | nibble(text[i * 2 + 1]));
	return 0;
}

static int text_field(const char *text, void *output, size_t width)
{
	size_t i, length = text ? strlen(text) : 0;
	if (length > width) return -EINVAL;
	for (i = 0; i < length; i++)
		if ((unsigned char)text[i] < 32 || (unsigned char)text[i] == 127) return -EINVAL;
	memset(output, 0, width);
	if (length) memcpy(output, text, length);
	return 0;
}

int zx_serial(const char *text, uint8_t output[8])
{
	unsigned int i;
	if (!text) return -EINVAL;
	if (strlen(text) == 16) return zx_hex(text, output, 8);
	if (strlen(text) != 12) return -EINVAL;
	for (i = 0; i < 4; i++)
		if ((unsigned char)text[i] < 32 || (unsigned char)text[i] > 126) return -EINVAL;
	if (zx_hex(text + 4, output + 4, 4)) return -EINVAL;
	memcpy(output, text, 4);
	return 0;
}

int zx_registration(const char *text, const char *format, uint8_t output[36])
{
	memset(output, 0, 36);
	if (format && strcmp(format, "hex") && strcmp(format, "text")) return -EINVAL;
	if (!text || !text[0]) return 0;
	if (format && !strcmp(format, "text")) return text_field(text, output, 36);
	return zx_hex(text, output, 36);
}

/* Never source the legacy file as shell. Only exact, validated hex values enter UCI. */
int zx_migrate(const char *destination, const char *legacy)
{
	struct stat existing;
	char line[256], serial[17] = "", registration[73] = "", temporary[512];
	uint8_t bytes[36];
	FILE *input, *output;
	int descriptor, result = 0;
	if (lstat(destination, &existing) == 0) {
		if (!S_ISREG(existing.st_mode)) return -EINVAL;
		descriptor = open(destination, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
		if (descriptor < 0) return -errno;
		if (fstat(descriptor, &existing) || !S_ISREG(existing.st_mode)) result = -EINVAL;
		else if (fchmod(descriptor, 0600)) result = -errno;
		close(descriptor);
		return result;
	}
	if (errno != ENOENT) return -errno;
	descriptor = open(legacy, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
	if (descriptor < 0 && errno != ENOENT) return -errno;
	input = NULL;
	if (descriptor >= 0) {
		if (fstat(descriptor, &existing) || !S_ISREG(existing.st_mode)) { close(descriptor); return -EINVAL; }
		if (fchmod(descriptor, 0600)) { result = -errno; close(descriptor); return result; }
		input = fdopen(descriptor, "r");
		if (!input) { result = -errno; close(descriptor); return result; }
	}
	if (input) {
		while (fgets(line, sizeof(line), input)) {
			char *p = line;
			if (!strchr(line, '\n') && !feof(input)) {
				int ch;
				while ((ch = fgetc(input)) != '\n' && ch != EOF) {}
				continue;
			}
			while (*p == ' ' || *p == '\t') p++;
			p[strcspn(p, "\r\n")] = 0;
			if (!strncmp(p, "SN=", 3) && !zx_hex(p + 3, bytes, 8)) strcpy(serial, p + 3);
			if (!strncmp(p, "REGID=", 6) && !zx_hex(p + 6, bytes, 36)) strcpy(registration, p + 6);
		}
		result = ferror(input) ? -EIO : 0;
		fclose(input);
		if (result) return result;
	}
	if (snprintf(temporary, sizeof(temporary), "%s.XXXXXX", destination) >= (int)sizeof(temporary)) return -ENAMETOOLONG;
	descriptor = mkstemp(temporary);
	if (descriptor < 0) return -errno;
	output = fdopen(descriptor, "w");
	if (!output) { result = -errno; close(descriptor); unlink(temporary); return result; }
	if (fprintf(output, "config pon 'pon'\n\toption enabled '%d'\n\toption mode 'xgpon'\n"
		"\toption serial_number '%s'\n\toption registration_id_format 'hex'\n"
		"\toption registration_id '%s'\n\toption omcc_version '0xb0'\n"
		"\toption disable_enhanced_security '0'\n", serial[0] && registration[0] ? 1 : 0,
		serial, registration) < 0 || fflush(output) || fsync(descriptor)) result = -EIO;
	if (fclose(output) && !result) result = -EIO;
	/* link() publishes atomically without overwriting a concurrently created config. */
	if (!result && link(temporary, destination) && errno != EEXIST) result = -errno;
	unlink(temporary);
	memset(bytes, 0, sizeof(bytes));
	memset(registration, 0, sizeof(registration));
	return result;
}

#ifndef ZX_CONFIG_STANDALONE
#include <uci.h>
static const char *option(struct uci_context *ctx, struct uci_section *section, const char *key)
{
	return uci_lookup_option_string(ctx, section, key);
}

int zx_config_load(struct zx_config *config, const char *directory)
{
	struct uci_context *ctx = uci_alloc_context();
	struct uci_package *package = NULL;
	struct uci_section *section;
	const char *value;
	int result = -EINVAL;
	memset(config, 0, sizeof(*config));
	if (!ctx) return -ENOMEM;
	if (uci_set_confdir(ctx, directory) != UCI_OK) goto done;
	if (uci_load(ctx, "pon", &package) != UCI_OK || !(section = uci_lookup_section(ctx, package, "pon"))) goto done;
	if (strcmp(section->type, "pon")) goto done;
	value = option(ctx, section, "enabled");
	if (value && strcmp(value, "0") && strcmp(value, "1")) goto done;
	config->enabled = value && !strcmp(value, "1");
	value = option(ctx, section, "mode");
	if (value && strcmp(value, "xgpon")) goto done;
	value = option(ctx, section, "serial_number");
	if (value && value[0]) {
		if (zx_serial(value, config->identity.serial)) goto done;
		config->has_identity = 1;
	}
	if (zx_registration(option(ctx, section, "registration_id"), option(ctx, section, "registration_id_format"), config->regid)) goto done;
	config->identity.omcc_version = 0xb0;
	value = option(ctx, section, "omcc_version");
	if (value && strcmp(value, "0xb0") && strcmp(value, "0x86")) goto done;
	if (value && !strcmp(value, "0x86")) config->identity.omcc_version = 0x86;
	value = option(ctx, section, "disable_enhanced_security");
	if (value && strcmp(value, "0") && strcmp(value, "1")) goto done;
	config->identity.disable_enhanced_security = value && !strcmp(value, "1");
#define FIELD(key, member, fallback) do { \
	value = option(ctx, section, key); \
	if (text_field(value && value[0] ? value : fallback, config->identity.member, sizeof(config->identity.member))) goto done; \
} while (0)
	FIELD("equipment_id", equipment, "SK-D840N");
	FIELD("hardware_version", hardware, "ZX279133");
	FIELD("software_version", software, "zxpon-1.0");
	FIELD("loid", loid, "");
	FIELD("loid_password", password, "");
	FIELD("operator_id", operator_id, "CTC");
#undef FIELD
	value = option(ctx, section, "omci_psk");
	if (value && value[0]) {
		if (zx_hex(value, config->identity.psk, 16)) goto done;
		config->identity.has_psk = 1;
	}
	result = 0;
done:
	uci_free_context(ctx);
	if (result) memset(config, 0, sizeof(*config));
	return result;
}
#endif
