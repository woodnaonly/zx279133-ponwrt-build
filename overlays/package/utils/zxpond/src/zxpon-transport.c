/* SPDX-License-Identifier: GPL-2.0-only */
#include <errno.h>
#include <linux/genetlink.h>
#include <linux/netlink.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#include "zxpon.h"
#include "zxpon-transport.h"

static int receive_kernel(int fd, void *buffer, size_t capacity, unsigned int sequence)
{
	struct sockaddr_nl peer = {0};
	socklen_t size = sizeof(peer);
	struct nlmsghdr *message = buffer;
	int length = recvfrom(fd, buffer, capacity, 0, (struct sockaddr *)&peer, &size);
	if (length < 0) return -errno;
	if (size < sizeof(peer) || peer.nl_family != AF_NETLINK || peer.nl_pid ||
	    (size_t)length > capacity || length < (int)NLMSG_HDRLEN || message->nlmsg_len < NLMSG_HDRLEN ||
	    message->nlmsg_len > (unsigned int)length || message->nlmsg_seq != sequence) return -EPROTO;
	return (int)message->nlmsg_len;
}

int zx_genl_command(unsigned int command, const void *attributes, size_t length)
{
	struct sockaddr_nl kernel = {.nl_family = AF_NETLINK};
	struct timeval timeout = {.tv_sec = 1};
	union {
		struct nlmsghdr align;
		unsigned char bytes[4096];
	} reply;
	struct { struct nlmsghdr nh; struct genlmsghdr gh; unsigned char attr[256]; } request = {0};
	struct nlattr *attribute;
	struct nlmsghdr *message = &reply.align;
	uint16_t family = 0;
	int fd, received, remaining, result = -ENOENT;
	if (length > sizeof(request.attr)) return -EMSGSIZE;
	fd = socket(AF_NETLINK, SOCK_RAW | SOCK_CLOEXEC, NETLINK_GENERIC);
	if (fd < 0) return -errno;
	if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout))) { result = -errno; goto done; }
	request.nh.nlmsg_type = GENL_ID_CTRL;
	request.nh.nlmsg_flags = NLM_F_REQUEST;
	request.nh.nlmsg_seq = 1;
	request.gh.cmd = CTRL_CMD_GETFAMILY; request.gh.version = 1;
	attribute = (struct nlattr *)request.attr;
	attribute->nla_type = CTRL_ATTR_FAMILY_NAME;
	attribute->nla_len = NLA_HDRLEN + sizeof("zxpon");
	memcpy(request.attr + NLA_HDRLEN, "zxpon", sizeof("zxpon"));
	request.nh.nlmsg_len = NLMSG_LENGTH(GENL_HDRLEN) + NLA_ALIGN(attribute->nla_len);
	if (sendto(fd, &request, request.nh.nlmsg_len, 0, (struct sockaddr *)&kernel, sizeof(kernel)) < 0) { result = -errno; goto done; }
	received = receive_kernel(fd, reply.bytes, sizeof(reply), 1);
	if (received < 0) { result = received; goto done; }
	if (message->nlmsg_type == NLMSG_ERROR || received < (int)NLMSG_LENGTH(GENL_HDRLEN)) goto done;
	remaining = received - (int)NLMSG_LENGTH(GENL_HDRLEN);
	attribute = (struct nlattr *)(reply.bytes + NLMSG_LENGTH(GENL_HDRLEN));
	while (remaining >= (int)NLA_HDRLEN && attribute->nla_len >= NLA_HDRLEN && attribute->nla_len <= remaining) {
		if (attribute->nla_type == CTRL_ATTR_FAMILY_ID && attribute->nla_len == NLA_HDRLEN + sizeof(family)) {
			memcpy(&family, (char *)attribute + NLA_HDRLEN, sizeof(family)); break;
		}
		remaining -= NLA_ALIGN(attribute->nla_len);
		attribute = (struct nlattr *)((char *)attribute + NLA_ALIGN(attribute->nla_len));
	}
	if (!family) goto done;
	memset(&request, 0, sizeof(request));
	request.nh.nlmsg_type = family;
	request.nh.nlmsg_flags = NLM_F_REQUEST | NLM_F_ACK;
	request.nh.nlmsg_seq = 2;
	request.gh.cmd = command; request.gh.version = 1;
	request.nh.nlmsg_len = NLMSG_LENGTH(GENL_HDRLEN) + (unsigned int)length;
	if (length) memcpy(request.attr, attributes, length);
	if (sendto(fd, &request, request.nh.nlmsg_len, 0, (struct sockaddr *)&kernel, sizeof(kernel)) < 0) { result = -errno; goto done; }
	received = receive_kernel(fd, reply.bytes, sizeof(reply), 2);
	if (received < 0) { result = received; goto done; }
	if (message->nlmsg_type != NLMSG_ERROR || received < (int)NLMSG_LENGTH(sizeof(struct nlmsgerr))) result = -EPROTO;
	else result = ((struct nlmsgerr *)NLMSG_DATA(message))->error;
done:
	memset(&request, 0, sizeof(request));
	close(fd);
	return result;
}

int zx_transport_send(const uint8_t *frame, size_t length)
{
	struct { struct nlattr attribute; uint8_t frame[48]; } request = {0};
	if (!frame || length != sizeof(request.frame)) return -EMSGSIZE;
	if (frame[3] != 0x0a || (frame[2] & 0xc0) || frame[40] || frame[41] || frame[42] || frame[43] != 0x28) return -EBADMSG;
	request.attribute.nla_type = ZXPON_A_OMCI_FRAME;
	request.attribute.nla_len = NLA_HDRLEN + sizeof(request.frame);
	memcpy(request.frame, frame, length);
	return zx_genl_command(ZXPON_CMD_OMCI_TX, &request, sizeof(request));
}
