/*
 * ponctl - userspace handle for the ZTE/Sanechips PON stack on ZX279133.
 *
 * Why this exists: the 4.19 BSP loads the whole PON stack but ships no client, so the
 * ONU is headless. Its two doors were recovered from the binaries (pon/INVENTORY.md
 * sections 4c, 4e, 4f): the /dev/gpondrv_dev ioctl macros, and the netlink protocols
 * 25-28 that the vendor kernel creates in MonitorInit.
 *
 * Built for aarch64 in CI, never locally:
 *   aarch64-linux-gnu-gcc -static -O2 -Wall -o ponctl ponctl.c
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <linux/netlink.h>
#include <linux/if_packet.h>
#include <linux/if_ether.h>

#define DEV "/dev/gpondrv_dev"
#define DEV_MAJOR 102
/* gpondrv_devIoctl copies exactly this much in and back out. */
#define IOC_SIZE 136

struct ioc {
	unsigned int flag;   /* +0: if the handler leaves it nonzero nothing is copied back */
	unsigned int macro;  /* +4: the command id */
	unsigned char p[IOC_SIZE - 8];
};

static void hexdump(const unsigned char *b, int n)
{
	int i;

	for (i = 0; i < n; i += 16) {
		int j;

		printf("  %04x  ", i);
		for (j = 0; j < 16 && i + j < n; j++)
			printf("%02x ", b[i + j]);
		for (; j < 16; j++)
			printf("   ");
		printf(" ");
		for (j = 0; j < 16 && i + j < n; j++)
			putc(b[i + j] >= 32 && b[i + j] < 127 ? b[i + j] : '.', stdout);
		printf("\n");
	}
}

/* The ioctl() cmd argument is ignored by the driver; 0 is as good as any. */
static int call(int fd, unsigned int macro, const void *in, int inlen, struct ioc *out)
{
	struct ioc d;

	memset(&d, 0, sizeof d);
	d.macro = macro;
	if (in && inlen > 0) {
		if (inlen > (int)sizeof d.p)
			return -EINVAL;
		memcpy(d.p, in, inlen);
	}
	if (ioctl(fd, 0, &d) < 0)
		return -errno;
	if (out)
		*out = d;
	return 0;
}

static int open_dev(void)
{
	int fd = open(DEV, O_RDWR);

	if (fd >= 0)
		return fd;
	/* The image registers major 102 but creates no node, so make it on demand. */
	if (mknod(DEV, S_IFCHR | 0600, makedev(DEV_MAJOR, 0)) < 0 && errno != EEXIST) {
		fprintf(stderr, "mknod %s: %s\n", DEV, strerror(errno));
		return -1;
	}
	fd = open(DEV, O_RDWR);
	if (fd < 0)
		fprintf(stderr, "open %s: %s\n", DEV, strerror(errno));
	return fd;
}

/*
 * Read-only macros. The first three payload layouts came straight out of the adapter
 * disassembly: 15 writes one byte (operation state), 16 writes a u32 ONU id
 * (0x3ff = unassigned), 1007 writes one byte (loss-of-signal latch). The rest fill
 * structures that are not decoded yet, so they are shown as raw bytes - enough to
 * notice them change, not enough to mislead.
 */
static const struct {
	unsigned int macro;
	const char *name;
	int bytes;          /* 1 = u8, 4 = u32, otherwise how many bytes to hexdump */
} READONLY[] = {
	{ 15, "opstate", 1 },
	{ 16, "onu_id", 4 },
	{ 1007, "los", 1 },
	{ 1010, "fec_state", 64 },
	{ 1011, "enc_state", 64 },
	{ 1013, "response_time", 32 },
	{ 1014, "eq_delay", 32 },
	{ 1001, "pon_stats", 128 },
	{ 0, NULL, 0 },
};

static int cmd_status(int fd)
{
	int i, bad = 0;

	for (i = 0; READONLY[i].name; i++) {
		struct ioc d;
		int rc = call(fd, READONLY[i].macro, NULL, 0, &d);

		if (rc) {
			printf("%-16s error %d\n", READONLY[i].name, rc);
			bad = bad ? bad : rc;
			continue;
		}
		if (READONLY[i].bytes == 1)
			printf("%-16s %u\n", READONLY[i].name, d.p[0]);
		else if (READONLY[i].bytes == 4)
			printf("%-16s %u%s\n", READONLY[i].name, *(unsigned int *)d.p,
			       *(unsigned int *)d.p == 0x3ff ? "  (unassigned)" : "");
		else {
			printf("%-16s raw (layout not decoded)\n", READONLY[i].name);
			hexdump(d.p, READONLY[i].bytes);
		}
	}
	return bad;
}

/*
 * The vendor kernel's Monitor subsystem creates netlink protocols 25 GDeviceStateSocket,
 * 26 GOmciMsgSocket, 27 GOmciMsgSocket1 and 28. OMCI messages leave through
 * netlink_broadcast(sk, skb, portid=0, group=1, gfp); device-state events leave through
 * netlink_unicast(sk, skb, portid=0x48a3, 1) - so a listener on 25 must bind exactly
 * that portid, which is why pid is an argument instead of always 0.
 *
 * Frame: struct nlmsghdr, then a 4 x u16 monitor header, then the payload.
 */
static int cmd_listen(int proto, unsigned int pid, unsigned int group)
{
	int fd;
	socklen_t alen;
	unsigned char buf[4096];
	struct sockaddr_nl me, from;

	fd = socket(AF_NETLINK, SOCK_RAW, proto);
	if (fd < 0) {
		fprintf(stderr, "socket(AF_NETLINK, proto=%d): %s\n", proto, strerror(errno));
		return 1;
	}
	memset(&me, 0, sizeof me);
	me.nl_family = AF_NETLINK;
	me.nl_pid = pid;
	me.nl_groups = group ? (1u << (group - 1)) : 0;
	if (bind(fd, (struct sockaddr *)&me, sizeof me) < 0) {
		fprintf(stderr, "bind(pid=%u groups=0x%x): %s\n", me.nl_pid, me.nl_groups,
			strerror(errno));
		close(fd);
		return 1;
	}
	alen = sizeof from;
	if (getsockname(fd, (struct sockaddr *)&from, &alen) == 0)
		fprintf(stderr, "listening: proto=%d bound pid=%u groups=0x%x\n", proto,
			from.nl_pid, from.nl_groups);
	for (;;) {
		struct nlmsghdr *nh = (struct nlmsghdr *)buf;
		ssize_t n = read(fd, buf, sizeof buf);

		if (n < 0) {
			if (errno == EINTR)
				break;
			fprintf(stderr, "read: %s\n", strerror(errno));
			return 1;
		}
		for (; NLMSG_OK(nh, (unsigned int)n); nh = NLMSG_NEXT(nh, n)) {
			int plen = (int)nh->nlmsg_len - NLMSG_LENGTH(0);
			unsigned short *mon = (unsigned short *)NLMSG_DATA(nh);

			printf("nlmsg type=%u flags=%u seq=%u pid=%u len=%u\n",
			       nh->nlmsg_type, nh->nlmsg_flags, nh->nlmsg_seq, nh->nlmsg_pid,
			       nh->nlmsg_len);
			if (plen >= 8) {
				printf("  monitor hdr: %u %u %u %u\n", mon[0], mon[1], mon[2],
				       mon[3]);
				if (plen > 8)
					hexdump((unsigned char *)mon + 8,
						 plen - 8 > 96 ? 96 : plen - 8);
			} else {
				hexdump((unsigned char *)mon, plen > 96 ? 96 : plen);
			}
		}
		fflush(stdout);
	}
	close(fd);
	return 0;
}

/*
 * gpondrvCfgXGRegInfoAdapter (macro 14) is the only writer of identity in this build:
 * sn[8] at payload +0, one byte it never reads at +8, regid[36] at +9, and an all-zero
 * field is skipped. Password is unreachable - zxic_api_gp_set_pwd is exported but
 * called by nothing.
 */
static int cmd_sn(int fd, const char *sn, const char *loid)
{
	unsigned char p[45];
	struct ioc d;
	int rc;

	if (!sn && !loid) {
		fprintf(stderr, "nothing to write\n");
		return 1;
	}
	memset(p, 0, sizeof p);
	if (sn) {
		int i;

		if (strlen(sn) != 12) {
			fprintf(stderr, "sn must be 12 characters: 4 vendor then 8 hex, e.g. ZTEG01234567\n");
			return 1;
		}
		for (i = 0; i < 4; i++)
			p[i] = (unsigned char)sn[i];
		for (i = 0; i < 4; i++) {
			char b[3];

			b[0] = sn[4 + 2 * i];
			b[1] = sn[5 + 2 * i];
			b[2] = 0;
			p[4 + i] = (unsigned char)strtoul(b, NULL, 16);
		}
	}
	if (loid) {
		if (strlen(loid) > 35) {
			fprintf(stderr, "loid too long (35 characters max)\n");
			return 1;
		}
		memcpy(p + 9, loid, strlen(loid) + 1);
	}
	rc = call(fd, 14, p, (int)sizeof p, &d);
	printf("write sn/loid (14) rc=%d\n", rc);
	if (sn)
		printf("  sn   %.4s%02x%02x%02x%02x\n", sn, p[4], p[5], p[6], p[7]);
	if (loid)
		printf("  loid %s\n", loid);
	return rc != 0;
}

static int parse_hex(const char *s, unsigned char *out, int max)
{
	int n = 0;

	while (*s && n < max) {
		char *e;
		unsigned long v = strtoul(s, &e, 16);

		if (e == s || v > 255)
			return -1;
		out[n++] = (unsigned char)v;
		s = e;
		while (*s == ' ' || *s == ':')
			s++;
	}
	return n;
}

/*
 * ponctl tx <if> <hex>: AF_PACKET/SOCK_RAW send, used to answer "is the `omci` netdev a
 * working transmit path?".  It is not, and this is how that was measured rather than
 * assumed: RX is traced (netdriver.ko's omci_recv forwards to the Monitor netlink) but TX
 * is absent everywhere - zte_xgpon's fi_Configure_Ponmac_Send_Omci_Msg is a `return 0`
 * stub, no gpondrv_dev macro sends a PDU, and swport_dev_xmit_fin drops every frame
 * because the netdev_priv + 0x8c8 back-pointer it needs is written by nothing in the
 * image.  With no fibre the frame cannot reach an OLT either way, so the evidence is
 * /proc/net/dev tx_errors (+1 per frame) and the stack's own send_omci_cnt (always 0).
 * Bytes must be space or colon separated. pon/INVENTORY.md 4h has the full result.
 */
static int cmd_tx(const char *ifname, const char *hex)
{
	unsigned char frame[256];
	struct sockaddr_ll to;
	int fd, n, ifi, rc, type;

	n = hex ? parse_hex(hex, frame, (int)sizeof frame) : 0;
	if (n < 14) {
		fprintf(stderr, "need at least an Ethernet header (14 bytes) in hex\n");
		return 1;
	}
	fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
	if (fd < 0) {
		fprintf(stderr, "socket(AF_PACKET): %s\n", strerror(errno));
		return 1;
	}
	memset(&to, 0, sizeof to);
	to.sll_family = AF_PACKET;
	type = (frame[12] << 8) | frame[13];
	to.sll_protocol = htons(type);
	to.sll_halen = 6;
	memcpy(to.sll_addr, frame, 6);
	{
		char path[64];
		FILE *f;

		snprintf(path, sizeof path, "/sys/class/net/%s/ifindex", ifname);
		f = fopen(path, "r");
		if (!f) {
			fprintf(stderr, "%s: %s\n", path, strerror(errno));
			close(fd);
			return 1;
		}
		if (fscanf(f, "%d", &ifi) != 1)
			ifi = 0;
		fclose(f);
	}
	to.sll_ifindex = ifi;
	if (sendto(fd, frame, n, 0, (struct sockaddr *)&to, sizeof to) < 0) {
		printf("tx on %s (ifindex %d): FAILED %s\n", ifname, ifi, strerror(errno));
		close(fd);
		return 1;
	}
	printf("tx on %s (ifindex %d): %d bytes accepted\n", ifname, ifi, n);
	rc = 0;
	close(fd);
	return rc;
}

static int cmd_raw(unsigned int macro, const char *hex)
{
	unsigned char p[128];
	int fd, n = 0, rc;
	struct ioc d;

	if (hex) {
		n = parse_hex(hex, p, (int)sizeof p);
		if (n < 0) {
			fprintf(stderr, "bad payload\n");
			return 1;
		}
	}
	fd = open_dev();
	if (fd < 0)
		return 1;
	/* Some handlers delete live T-CONT and GEM entries, so this path prints exactly
	 * what it is about to send and makes no promise of safety. */
	printf("macro %u payload %d bytes:\n", macro, n);
	hexdump(p, n);
	rc = call(fd, macro, p, n, &d);
	printf("rc=%d\n", rc);
	hexdump(d.p, 128);
	close(fd);
	return 0;
}

int main(int argc, char **argv)
{
	const char *what = argc > 1 ? argv[1] : "";
	int fd, rc;

	if (!strcmp(what, "listen"))
		return cmd_listen(argc > 2 ? atoi(argv[2]) : 26,
				argc > 3 ? (unsigned)strtoul(argv[3], NULL, 0) : 0,
				argc > 4 ? (unsigned)strtoul(argv[4], NULL, 0) : 1);
	if (!strcmp(what, "tx"))
		return argc < 4 ? 2 : cmd_tx(argv[2], argv[3]);
	if (!strcmp(what, "raw"))
		return argc < 3 ? 2 : cmd_raw((unsigned)strtoul(argv[2], NULL, 0),
					     argc > 3 ? argv[3] : NULL);
	if (strcmp(what, "status") && strcmp(what, "sn")) {
		printf("usage:\n"
		       "  ponctl status                             read ONU state over /dev/gpondrv_dev\n"
		       "  ponctl sn <12charSN> [loid]               program identity (macro 14)\n"
		       "  ponctl listen <25|26|27|28> [pid] [group] the vendor kernel's Monitor netlink\n"
		       "  ponctl tx <if> <hex frame>                send raw bytes on a PON netdev\n"
		       "  ponctl raw <macro> [hex payload]          unsanitized ioctl\n");
		return 2;
	}
	fd = open_dev();
	if (fd < 0)
		return 1;
	rc = !strcmp(what, "status") ? cmd_status(fd)
		: cmd_sn(fd, argc > 2 ? argv[2] : NULL, argc > 3 ? argv[3] : NULL);
	close(fd);
	return rc ? 1 : 0;
}
