/* SPDX-License-Identifier: GPL-2.0-only */
#include <errno.h>
#include "zxpon-backend.h"

int zx_backend_apply(void *context, const uint8_t *paths, size_t length)
{
	static const uint8_t empty[12] = {4, 0, 0, 0, 0, 0, 0, 0, 12, 0, 0, 0};
	size_t i;
	(void)context;
	/* Only a genuinely empty graph has no hardware side effect. */
	if (length != sizeof(empty)) return -EOPNOTSUPP;
	for (i = 0; i < length; i++) if (paths[i] != empty[i]) return -EOPNOTSUPP;
	return 0;
}

int zx_backend_dispatch(struct omci_agent *agent, const uint8_t *input, size_t length, uint8_t output[2048])
{
	uint8_t broadcast[18];
	int result = 0;
	if (!agent || !input || !output || length > 2048) return -EINVAL;
	/* The reusable codec also decodes unflagged fixtures. The production ONU
	 * boundary accepts requests only, not replayed responses or bad trailers.
	 */
	if (length < 4 || (input[2] & 0xe0) != 0x40) return -EBADMSG;
	if (input[3] == 0x0a &&
	    ((length != 44 && length != 48) || input[40] || input[41] || input[42] || input[43] != 0x28))
		return -EBADMSG;
	/* Incomplete hardware MEs can still serialize as an empty graph. Reject
	 * mutations before staging them instead of ACKing a T-CONT/GEM/key/port
	 * update merely because the graph has not become connected yet. Only an
	 * ONU-data MIB reset is software-only on this non-provisioning backend.
	 */
	if (length >= 8) {
		unsigned int action = input[2] & 0x1f;
		if (action == 4 || action == 6 || action == 8 || action == 0x1d ||
		    (action == 0x0f && (input[4] || input[5] != 2)))
			result = -EOPNOTSUPP;
	}
	if (zxpon_omci_broadcast(agent, input, length, broadcast)) result = -EOPNOTSUPP;
	return zxpon_omci_dispatch_apply(agent, input, length, output, &result, zx_backend_apply, NULL);
}
