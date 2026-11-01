/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "zxpon-backend.h"

int main(void)
{
	struct omci_identity identity = {.serial = {'T','E','S','T',1,2,3,4}};
	struct omci_agent *agent = zxpon_omci_new(&identity);
	uint8_t frame[2049] = {0}, response[2048], paths[65536];
	assert(agent);
	for (size_t i = 0; i < 2049; i++) {
		memset(response, 0xa5, sizeof(response));
		assert(zx_backend_dispatch(agent, frame, i, response) < 0);
		assert(response[0] == 0xa5);
	}
	/* Baseline GET ONU-G serial, exact TCI and no fabricated missing-node status. */
	const uint8_t get[44] = {0x12,0x34,0x49,0x0a,0x01,0x00,0,0,0x20,0, [43] = 0x28};
	assert(zx_backend_dispatch(agent, get, sizeof(get), response) > 0);
	assert(response[0] == 0x12 && response[1] == 0x34 && response[2] == 0x29 && !response[8]);
	assert(!memcmp(response + 11, identity.serial, 8));
	memcpy(frame, get, sizeof(get)); frame[43] = 0;
	assert(zx_backend_dispatch(agent, frame, 44, response) == -EBADMSG);
	frame[43] = 0x28; frame[2] = 0x29;
	assert(zx_backend_dispatch(agent, frame, 44, response) == -EBADMSG);
	/* Empty graph has no side effects; unsupported hardware graph is rejected. */
	int length = zxpon_omci_paths(agent, paths);
	assert(length == 12 && !zx_backend_apply(NULL, paths, (size_t)length));
	paths[2] = 1;
	assert(zx_backend_apply(NULL, paths, (size_t)length) == -EOPNOTSUPP);
	assert(zx_backend_apply(NULL, paths, 0) == -EOPNOTSUPP);
	/* A disconnected GEM is not an excuse to ACK a hardware mutation. */
	uint8_t create[44] = {0,2,0x44,0x0a,1,0x0c,0,1, [43] = 0x28};
	assert(zx_backend_dispatch(agent, create, sizeof(create), response) == 44);
	assert(response[8] == 2);
	uint8_t find[44] = {0,3,0x49,0x0a,1,0x0c,0,1,0x80,0, [43] = 0x28};
	assert(zx_backend_dispatch(agent, find, sizeof(find), response) == 44);
	assert(response[8] == 5); /* No instance was committed. */
	zxpon_omci_free(agent);
	puts("PASS: baseline GET, 2049 malformed/oversize frames and fail-closed hardware boundary");
	return 0;
}
