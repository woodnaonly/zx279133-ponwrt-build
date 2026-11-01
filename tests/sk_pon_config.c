/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "zxpon-config.h"

static void write_file(const char *path, const char *text)
{
	FILE *stream = fopen(path, "w");
	assert(stream && fputs(text, stream) >= 0 && !fclose(stream));
}
static void read_file(const char *path, char output[4096])
{
	FILE *stream = fopen(path, "r");
	assert(stream);
	size_t n = fread(output, 1, 4095, stream);
	assert(!ferror(stream)); output[n] = 0; fclose(stream);
}
int main(void)
{
	uint8_t bytes[36];
	assert(!zx_serial("TEST01020304", bytes));
	assert(!memcmp(bytes, "TEST\1\2\3\4", 8));
	assert(!zx_serial("5445535401020304", bytes));
	assert(zx_serial("TEST0102030", bytes) == -EINVAL);
	assert(zx_serial("TEST0102030z", bytes) == -EINVAL);
	assert(zx_serial("TES\n01020304", bytes) == -EINVAL);
	assert(!zx_registration("", "hex", bytes));
	for (unsigned int i = 0; i < 36; i++) assert(!bytes[i]);
	assert(!zx_registration("123456789012345678901234567890123456", "text", bytes));
	assert(zx_registration("1234567890123456789012345678901234567", "text", bytes) == -EINVAL);
	assert(zx_registration("abc\n", "text", bytes) == -EINVAL);
	assert(zx_registration("abc", "auto", bytes) == -EINVAL);
	assert(!zx_registration("0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f2021222324", "hex", bytes));
	for (unsigned int i = 0; i < 36; i++) assert(bytes[i] == i + 1);
	assert(zx_registration("aa", "hex", bytes) == -EINVAL);

	char temporary[] = "/tmp/sk-pon-config-XXXXXX", config[256], legacy[256], content[4096], original[4096];
	struct stat st;
	assert(mkdtemp(temporary));
	snprintf(config, sizeof(config), "%s/pon", temporary);
	snprintf(legacy, sizeof(legacy), "%s/identity", temporary);
	write_file(legacy, "# comments\nSN=5445535401020304\nREGID=0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f2021222324\n");
	read_file(legacy, original);
	assert(!zx_migrate(config, legacy));
	assert(!stat(config, &st) && (st.st_mode & 0777) == 0600);
	assert(!stat(legacy, &st) && (st.st_mode & 0777) == 0600);
	read_file(config, content);
	assert(strstr(content, "option enabled '1'") && strstr(content, "5445535401020304"));
	read_file(legacy, content); assert(!strcmp(content, original));
	write_file(config, "# user's existing UCI file, preserve it byte for byte\n");
	assert(!chmod(config, 0644) && !zx_migrate(config, legacy));
	read_file(config, content); assert(!strcmp(content, "# user's existing UCI file, preserve it byte for byte\n"));
	assert(!stat(config, &st) && (st.st_mode & 0777) == 0600);
	assert(!unlink(config));
	write_file(legacy, "SN=$(touch never-execute)\nREGID='shell data'\n");
	assert(!zx_migrate(config, legacy));
	read_file(config, content); assert(strstr(content, "option enabled '0'") && !strstr(content, "touch"));
	assert(!unlink(config) && !unlink(legacy));
	assert(!zx_migrate(config, legacy));
	read_file(config, content); assert(strstr(content, "option serial_number ''"));
	assert(!unlink(config));
	assert(!symlink("missing", config));
	assert(zx_migrate(config, legacy) == -EINVAL);
	assert(!unlink(config));
	assert(!symlink("missing", legacy));
	assert(zx_migrate(config, legacy) < 0 && access(config, F_OK));
	assert(!unlink(legacy) && !rmdir(temporary));
	puts("PASS: exact SN/REGID bytes, validation, safe one-time migration, permissions, no shell/symlink following");
	return 0;
}
