/* SPDX-License-Identifier: GPL-2.0-only */
/* Exercise the production netlink TX client without a device or privileges. */
#include <assert.h>
#include <errno.h>
#include <linux/genetlink.h>
#include <linux/netlink.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include "zxpon.h"
#include "zxpon-transport.h"

static unsigned int sequence, commands;
static int kernel_error, fault;
int __wrap_socket(int domain, int type, int protocol)
{
	assert(domain == AF_NETLINK && (type & SOCK_RAW) && protocol == NETLINK_GENERIC);
	return 1234;
}
int __wrap_setsockopt(int fd, int level, int option, const void *value, socklen_t size)
{
	assert(fd == 1234 && level == SOL_SOCKET && option == SO_RCVTIMEO && value && size);
	return 0;
}
int __wrap_close(int fd) { assert(fd == 1234); return 0; }
ssize_t __wrap_sendto(int fd, const void *buffer, size_t length, int flags,
	const struct sockaddr *address, socklen_t size)
{
	const struct nlmsghdr *message = buffer;
	const struct genlmsghdr *header = NLMSG_DATA(message);
	const struct sockaddr_nl *peer = (const struct sockaddr_nl *)address;
	assert(fd == 1234 && !flags && size == sizeof(*peer) && !peer->nl_pid);
	sequence = message->nlmsg_seq;
	if (sequence == 1) assert(message->nlmsg_type == GENL_ID_CTRL && header->cmd == CTRL_CMD_GETFAMILY);
	else {
		const struct nlattr *attribute = (const struct nlattr *)((const char *)header + GENL_HDRLEN);
		assert(sequence == 2 && message->nlmsg_type == 0x31 && header->cmd == ZXPON_CMD_OMCI_TX);
		assert(message->nlmsg_flags == (NLM_F_REQUEST | NLM_F_ACK));
		assert(attribute->nla_type == ZXPON_A_OMCI_FRAME && attribute->nla_len == NLA_HDRLEN + 48);
		commands++;
	}
	return (ssize_t)length;
}
ssize_t __wrap_recvfrom(int fd, void *buffer, size_t capacity, int flags,
	struct sockaddr *address, socklen_t *size)
{
	struct nlmsghdr *message = buffer;
	struct sockaddr_nl *peer = (struct sockaddr_nl *)address;
	assert(fd == 1234 && !flags && capacity >= 64 && *size >= sizeof(*peer));
	memset(buffer, 0, capacity);
	memset(peer, 0, sizeof(*peer));
	peer->nl_family = AF_NETLINK; *size = sizeof(*peer);
	message->nlmsg_seq = sequence;
	if (sequence == 1) {
		struct nlattr *attribute = (struct nlattr *)((char *)buffer + NLMSG_LENGTH(GENL_HDRLEN));
		uint16_t family = 0x31;
		message->nlmsg_type = GENL_ID_CTRL;
		attribute->nla_type = CTRL_ATTR_FAMILY_ID;
		attribute->nla_len = NLA_HDRLEN + sizeof(family);
		memcpy((char *)attribute + NLA_HDRLEN, &family, sizeof(family));
		message->nlmsg_len = NLMSG_LENGTH(GENL_HDRLEN) + NLA_ALIGN(attribute->nla_len);
	} else {
		message->nlmsg_type = NLMSG_ERROR;
		message->nlmsg_len = NLMSG_LENGTH(sizeof(struct nlmsgerr));
		((struct nlmsgerr *)NLMSG_DATA(message))->error = kernel_error;
		if (fault == 1) message->nlmsg_seq++;
		if (fault == 2) peer->nl_pid = 42;
		if (fault == 3) message->nlmsg_len = 4097;
		if (fault == 4) { errno = EAGAIN; return -1; }
		/* A forged header length is not the number of bytes copied by recvfrom. */
		if (fault == 3) return NLMSG_LENGTH(sizeof(struct nlmsgerr));
		if (fault == 5) return (ssize_t)capacity + 1;
	}
	return (ssize_t)message->nlmsg_len;
}
int main(void)
{
	uint8_t frame[48] = {0,1,0x29,0x0a,1,0,0,0, [43] = 0x28};
	assert(zx_transport_send(NULL, 48) == -EMSGSIZE);
	assert(zx_transport_send(frame, 44) == -EMSGSIZE && !commands);
	frame[2] = 0x49;
	assert(zx_transport_send(frame, 48) == -EBADMSG && !commands);
	frame[2] = 0x29; frame[43] = 0;
	assert(zx_transport_send(frame, 48) == -EBADMSG && !commands);
	frame[43] = 0x28;
	assert(zx_transport_send(frame, 48) == 0 && commands == 1);
	for (int i = 1; i <= 5; i++) {
		fault = i;
		assert(zx_transport_send(frame, 48) == (i == 4 ? -EAGAIN : -EPROTO));
	}
	fault = 0;
	const int errors[] = {-ENOLINK, -ETIMEDOUT, -EOPNOTSUPP, -EBUSY};
	for (unsigned int i = 0; i < sizeof(errors) / sizeof(errors[0]); i++) {
		kernel_error = errors[i];
		assert(zx_transport_send(frame, 48) == errors[i]);
	}
	puts("PASS: production OMCI TX, exact frame validation, ACK/error/timeout propagation, kernel origin and sequence checks");
	return 0;
}
