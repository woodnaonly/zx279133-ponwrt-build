/*
 * rdreg - read a physical register word through /dev/mem.
 *
 * This exists because /dev/mem read() returns EFAULT for MMIO on this SoC while mmap of the
 * same page works, and the shipped busybox has no `devmem` applet (measured 2026-10-19 on the
 * run-62 failsafe shell: `devmem 0x19140000` answers "applet not found", `grep -c devmem
 * /bin/busybox` is 0, and `hexdump -n 4 -s 0x19140000 /dev/mem` answers "Bad address"). Without
 * it the only register values obtainable from inside the running kernel are the handful the
 * driver volunteers through ethtool -S, so every new address costs a 70-minute build.
 *
 *	usage: rdreg <hex-address> [count]
 *
 * count words are printed as "0xADDR: 0xVALUE", one per line, little-endian as the CPU sees
 * them. Read-only: there is deliberately no write path here.
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	unsigned long addr;
	uint32_t *base;
	long page, off;
	int fd, i, count = 1;
	char *end;

	if (argc < 2 || argc > 3) {
		fprintf(stderr, "usage: %s <hex-address> [count]\n", argv[0]);
		return 2;
	}
	if (argc == 3) {
		count = (int)strtol(argv[2], NULL, 0);
		if (count < 1)
			count = 1;
	}

	errno = 0;
	addr = strtoul(argv[1], &end, 0);
	if (errno || end == argv[1] || *end != '\0') {
		fprintf(stderr, "rdreg: %s is not an address\n", argv[1]);
		return 2;
	}

	page = sysconf(_SC_PAGESIZE);
	if (page <= 0)
		page = 4096;
	off = (long)(addr & (unsigned long)(page - 1));
	if (off & 3) {
		fprintf(stderr, "rdreg: 0x%lx is not word aligned\n", addr);
		return 2;
	}
	/* One page is enough for anything a register dump asks for; a request that would run
	 * past it is refused rather than silently mapping short. */
	if (off + 4 * (long)count > page) {
		fprintf(stderr, "rdreg: %d words would cross the page at 0x%lx\n",
			count, addr);
		return 2;
	}

	fd = open("/dev/mem", O_RDONLY | O_SYNC);
	if (fd < 0) {
		fprintf(stderr, "rdreg: open /dev/mem: %s\n", strerror(errno));
		return 1;
	}

	base = mmap(NULL, page, PROT_READ, MAP_SHARED, fd, (off_t)(addr & ~(unsigned long)(page - 1)));
	if (base == MAP_FAILED) {
		fprintf(stderr, "rdreg: mmap 0x%lx: %s\n", addr, strerror(errno));
		close(fd);
		return 1;
	}

	for (i = 0; i < count; i++)
		printf("0x%08lx: 0x%08x\n", addr + (unsigned long)(4 * i),
		       base[off / 4 + i]);

	munmap(base, page);
	close(fd);
	return 0;
}
