/*
 * wrreg - write one physical register word through mmap of /dev/mem.
 *
 * Why this exists next to rdreg: reading the traffic-proven difference and *acting on it* are
 * two different builds apart, and a board window costs ten minutes of a human's attention while
 * a build costs eighty. The register this is written for is nppt+0x14/0x18: U-Boot's snapshot
 * taken in the cycle that moved 11,464,708 bytes through jack 4 holds 0x19008000=0x68000000,
 * 0x19008014=0x06111113, 0x19008018=0x01111111, while all three read 0 before that transfer.
 * Nothing in zx279133-np.c writes the direct SPA block at all - the driver only uses the
 * indirect path (CMD 0x8054 / STATUS 0x8058 / DATA 0x805c). If mainline leaves those three at
 * zero, the fix is one writel, and the way to know whether that is the fix before spending a
 * build is to write them from the console and watch the far end's byte counter.
 *
 *	usage: wrreg <hex-address> <hex-value>
 *
 * Refuses anything outside 0x14000000-0x1d000000, which is where this SoC keeps its network
 * blocks (mdio 0x14f02000, pps 0x18000000, nppt 0x19000000 with the XMAC windows inside it,
 * xpcs 0x1a000000/0x1b000000, serdes 0x16000000/0x16100000). It deliberately cannot reach the
 * SPI-NAND controller at 0x10d0f000, the watchdog/CRG at 0x10e10208, GPIO, or anything else in
 * the low 0x10-0x13 range, because a typo there is a reboot or a damaged flash rather than an
 * experiment. The old value is printed before the write so the round leaves its own audit.
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>

#define ALLOW_LO	0x14000000u
#define ALLOW_HI	0x1d000000u

int main(int argc, char **argv)
{
	unsigned long addr, value, page = 4096;
	volatile uint32_t *map;
	long off;
	char *end;
	int fd;

	if (argc != 3) {
		fprintf(stderr, "usage: %s <hex-address> <hex-value>\n", argv[0]);
		return 2;
	}
	errno = 0;
	addr = strtoul(argv[1], &end, 0);
	if (errno || end == argv[1] || *end != '\0') {
		fprintf(stderr, "wrreg: %s is not an address\n", argv[1]);
		return 2;
	}
	value = strtoul(argv[2], &end, 0);
	if (errno || end == argv[2] || *end != '\0') {
		fprintf(stderr, "wrreg: %s is not a value\n", argv[2]);
		return 2;
	}
	if (addr < ALLOW_LO || addr >= ALLOW_HI) {
		fprintf(stderr,
			"wrreg: %#lx is outside %#x-%#x (the network blocks). This tool will not "
			"touch the SPI-NAND controller, the watchdog/CRG or GPIO, because a typo "
			"there is a reboot or a damaged flash rather than an experiment.\n",
			addr, ALLOW_LO, ALLOW_HI);
		return 2;
	}
	if (addr & 3) {
		fprintf(stderr, "wrreg: %#lx is not word aligned\n", addr);
		return 2;
	}

	fd = open("/dev/mem", O_RDWR | O_SYNC);
	if (fd < 0) {
		fprintf(stderr, "wrreg: open /dev/mem: %s\n", strerror(errno));
		return 1;
	}
	off = (long)(addr & ~(page - 1));
	map = mmap(NULL, page, PROT_READ | PROT_WRITE, MAP_SHARED, fd, (off_t)off);
	if (map == MAP_FAILED) {
		fprintf(stderr, "wrreg: mmap %#lx: %s\n", addr, strerror(errno));
		close(fd);
		return 1;
	}

	map += (addr & (page - 1)) / 4;
	/* Cast, not luck. addr and value are unsigned long, which on this LP64 target is 64 bits,
	 * so "%#010x" under -Wall -Werror is a format error rather than a cosmetic choice. What
	 * actually guarantees the value fits is the range check above. */
	fprintf(stderr, "wrreg: %#010x was %#010x, writing %#010x\n",
		(unsigned)addr, *map, (unsigned)value);
	*map = (uint32_t)value;
	/* Read it straight back: some of these registers are write-gated and accept nothing,
	 * and "I wrote it" is not the same statement as "it is set". */
	fprintf(stderr, "wrreg: %#010x now %#010x\n", (unsigned)addr, *map);

	munmap((void *)(map - (addr & (page - 1)) / 4), page);
	close(fd);
	return 0;
}
