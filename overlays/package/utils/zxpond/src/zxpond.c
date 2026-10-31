#include <arpa/inet.h>
#include <errno.h>
#include <linux/genetlink.h>
#include <linux/if_packet.h>
#include <linux/netlink.h>
#include <net/if.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

#include "zxpon.h"

#define ID_PATH "/etc/pon/identity"

struct nlbuf {
	struct nlmsghdr nh;
	struct genlmsghdr gh;
	unsigned char attr[256];
};

static int nlsock(void)
{
	struct sockaddr_nl addr;
	int fd = socket(AF_NETLINK, SOCK_RAW, NETLINK_GENERIC);

	if (fd < 0)
		return -1;
	memset(&addr, 0, sizeof(addr));
	addr.nl_family = AF_NETLINK;
	if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
		close(fd);
		return -1;
	}
	return fd;
}

static int nl_send(int fd, void *msg, unsigned len)
{
	struct sockaddr_nl addr;

	memset(&addr, 0, sizeof(addr));
	addr.nl_family = AF_NETLINK;
	return sendto(fd, msg, len, 0, (struct sockaddr *)&addr, sizeof(addr)) < 0 ? -1 : 0;
}

static int family_id(int fd)
{
	struct {
		struct nlmsghdr nh;
		struct genlmsghdr gh;
		struct nlattr ah;
		char name[8];
	} req;
	char buf[512];
	struct nlmsghdr *nh;
	int len;

	memset(&req, 0, sizeof(req));
	req.nh.nlmsg_len = sizeof(req);
	req.nh.nlmsg_type = GENL_ID_CTRL;
	req.nh.nlmsg_flags = NLM_F_REQUEST;
	req.nh.nlmsg_seq = 1;
	req.gh.cmd = CTRL_CMD_GETFAMILY;
	req.gh.version = 1;
	req.ah.nla_type = CTRL_ATTR_FAMILY_NAME;
	req.ah.nla_len = sizeof(req.ah) + 6;
	memcpy(req.name, "zxpon", 6);
	if (nl_send(fd, &req, sizeof(req)) < 0)
		return -1;
	len = recv(fd, buf, sizeof(buf), 0);
	if (len < 0)
		return -1;
	nh = (struct nlmsghdr *)buf;
	if (nh->nlmsg_type == NLMSG_ERROR)
		return -1;
	{
		struct nlattr *a = (struct nlattr *)(buf + NLMSG_LENGTH(GENL_HDRLEN));
		int remain = len - NLMSG_LENGTH(GENL_HDRLEN);

		while (remain >= (int)sizeof(*a) && a->nla_len >= sizeof(*a) &&
		       a->nla_len <= remain) {
			if (a->nla_type == CTRL_ATTR_FAMILY_ID)
				return *(uint16_t *)((char *)a + NLA_HDRLEN);
			remain -= NLA_ALIGN(a->nla_len);
			a = (struct nlattr *)((char *)a + NLA_ALIGN(a->nla_len));
		}
	}
	return -1;
}

static int genl_cmd(int fd, int fam, uint8_t cmd, const void *a, unsigned alen,
		    uint16_t atype)
{
	struct nlbuf req;
	unsigned char *p;
	struct nlattr *ah;

	memset(&req, 0, sizeof(req));
	req.nh.nlmsg_type = fam;
	req.nh.nlmsg_flags = NLM_F_REQUEST;
	req.nh.nlmsg_seq = 2;
	req.gh.cmd = cmd;
	req.gh.version = 1;
	p = req.attr;
	if (a && alen) {
		ah = (struct nlattr *)p;
		ah->nla_type = atype;
		ah->nla_len = NLA_HDRLEN + alen;
		memcpy(p + NLA_HDRLEN, a, alen);
		req.nh.nlmsg_len = NLMSG_LENGTH(GENL_HDRLEN) + NLA_ALIGN(ah->nla_len);
	} else {
		req.nh.nlmsg_len = NLMSG_LENGTH(GENL_HDRLEN);
	}
	return nl_send(fd, &req, req.nh.nlmsg_len);
}

static int genl_identity(int fd, int fam, const uint8_t sn[8], const uint8_t regid[36])
{
	struct nlbuf req;
	unsigned char *p;
	struct nlattr *ah;

	memset(&req, 0, sizeof(req));
	req.nh.nlmsg_type = fam;
	req.nh.nlmsg_flags = NLM_F_REQUEST;
	req.nh.nlmsg_seq = 3;
	req.gh.cmd = ZXPON_CMD_SET_IDENTITY;
	req.gh.version = 1;
	p = req.attr;
	ah = (struct nlattr *)p;
	ah->nla_type = ZXPON_A_SN;
	ah->nla_len = NLA_HDRLEN + 8;
	memcpy(p + NLA_HDRLEN, sn, 8);
	p += NLA_ALIGN(ah->nla_len);
	ah = (struct nlattr *)p;
	ah->nla_type = ZXPON_A_REGID;
	ah->nla_len = NLA_HDRLEN + 36;
	memcpy(p + NLA_HDRLEN, regid, 36);
	p += NLA_ALIGN(ah->nla_len);
	req.nh.nlmsg_len = NLMSG_LENGTH(GENL_HDRLEN) + (unsigned)(p - req.attr);
	return nl_send(fd, &req, req.nh.nlmsg_len);
}

static int hexval(int c)
{
	if (c >= '0' && c <= '9')
		return c - '0';
	if (c >= 'a' && c <= 'f')
		return c - 'a' + 10;
	if (c >= 'A' && c <= 'F')
		return c - 'A' + 10;
	return -1;
}

static int parse_hex(const char *s, uint8_t *out, unsigned need)
{
	unsigned i;

	for (i = 0; i < need; i++) {
		int hi, lo;

		if (!s[0] || !s[1])
			return -1;
		hi = hexval(s[0]);
		lo = hexval(s[1]);
		if (hi < 0 || lo < 0)
			return -1;
		out[i] = (uint8_t)((hi << 4) | lo);
		s += 2;
	}
	return *s ? -1 : 0;
}

static int load_identity(uint8_t sn[8], uint8_t regid[36])
{
	FILE *fp = fopen(ID_PATH, "r");
	char line[128];
	int got_sn = 0, got_id = 0;

	if (!fp)
		return -1;
	while (fgets(line, sizeof(line), fp)) {
		char *p = line;

		while (*p == ' ' || *p == '\t')
			p++;
		{ char *nl = strchr(p, '\n'); if (nl) *nl = 0; nl = strchr(p, '\r'); if (nl) *nl = 0; }
		if (!strncmp(p, "SN=", 3))
			got_sn = parse_hex(p + 3, sn, 8) == 0;
		else if (!strncmp(p, "REGID=", 6))
			got_id = parse_hex(p + 6, regid, 36) == 0;
	}
	fclose(fp);
	return (got_sn && got_id) ? 0 : -1;
}

static int omci_socket(void)
{
	int fd, ifindex, up;
	struct sockaddr_ll addr;
	struct ifreq ifr;

	ifindex = if_nametoindex("omci");
	if (!ifindex)
		return -1;
	up = socket(AF_INET, SOCK_DGRAM, 0);
	if (up >= 0) {
		memset(&ifr, 0, sizeof(ifr));
		snprintf(ifr.ifr_name, sizeof(ifr.ifr_name), "omci");
		if (ioctl(up, SIOCGIFFLAGS, &ifr) == 0) {
			ifr.ifr_flags |= IFF_UP;
			ioctl(up, SIOCSIFFLAGS, &ifr);
		}
		close(up);
	}
	fd = socket(AF_PACKET, SOCK_DGRAM, htons(0xbaba));
	if (fd < 0)
		return -1;
	memset(&addr, 0, sizeof(addr));
	addr.sll_family = AF_PACKET;
	addr.sll_protocol = htons(0xbaba);
	addr.sll_ifindex = ifindex;
	if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
		close(fd);
		return -1;
	}
	return fd;
}

int main(void)
{
	uint8_t sn[8], regid[36];
	struct zxpon onu;
	int famfd, fam, omci = -1, have_id;
	uint16_t mapped = 0;

	zxpon_init(&onu);
	have_id = load_identity(sn, regid) == 0;
	famfd = nlsock();
	fam = famfd >= 0 ? family_id(famfd) : -1;
	if (have_id && fam > 0)
		genl_identity(famfd, fam, sn, regid);
	else
		fprintf(stderr, "zxpond: no identity, staying at O1\n");
	for (;;) {
		uint8_t frame[48], rsp[48];
		uint16_t gem = 0;
		int had, has, n;

		if (omci < 0)
			omci = omci_socket();
		if (omci < 0) {
			sleep(1);
			continue;
		}
		n = recv(omci, frame, sizeof(frame), 0);
		if (n != 48)
			continue;
		had = zxpon_data_gem(&onu, &gem);
		mapped = gem;
		zxpon_rx_omci(&onu, frame);
		while (zxpon_take_omci(&onu, rsp))
			send(omci, rsp, 48, 0);
		has = zxpon_data_gem(&onu, &gem);
		if (fam > 0 && has && (!had || gem != mapped)) {
			genl_cmd(famfd, fam, ZXPON_CMD_MAP_GEM, &gem, sizeof(gem), ZXPON_A_GEM);
		}
	}
}