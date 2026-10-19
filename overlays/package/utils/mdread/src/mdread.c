/*
 * mdread - perform one MDIO *read* transaction straight through the ZX279133 MDIO controller.
 *
 * Why not phylib: eth0 runs fixed-link, so nothing is ever attached to the RTL8226B at
 * 14f02000.mdio:05 and SIOCGMIIREG on that netdev has no phydev to ask. Why not U-Boot: the
 * PHY's state after a Linux boot can only be read before the next power cut, and the only way
 * back into U-Boot on this board IS a power cut, so the bootloader can never see what the
 * kernel left. That leaves poking the controller itself, which is what the in-kernel driver
 * (drivers/net/mdio/mdio-zx279133.c) does with five registers.
 *
 * The reference to diff against is staging/ctl/traffic-proven-phy-uboot.txt: U-Boot's own
 * `mii 45read` values, taken in the power cycle that moved 11 MB through jack 4, before any
 * Linux touched the chip. It says `1.0000`=2058 `1.0001`=0002 `1.0002`=001C `1.0020`=0000
 * `7.0000`=3000 `7.0001`=0008 `7.001c`=0000 `7.0020`=00A1 `7.0022`=0000 `1e.0000`=21FC, and
 * that file is explicit about the trap this tool has to avoid: plain C22 `mii read` returned
 * FFFF for all 32 registers *including the PHY ID*, so a C22 sweep here proves nothing about
 * the chip - the 45 blocks are the snapshot, and `c45` is the mode that matters.
 *
 *	usage: mdread c22 <addr> <reg> [count]
 *	       mdread c45 <addr> <devad> <reg> [count]
 *	       BASE=0x14f02000 (default) - the bus this board's PHY sits on
 *
 * Reads only: there is no write verb. It does have to write the controller's CONTROL/STATUS/
 * C45_REG registers to start a read transaction - that is how an MDIO read is issued - and it
 * deliberately mirrors the driver's own sequence, including clearing START afterwards.
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>

#define WRITE_DATA	0x04u
#define READ_DATA	0x08u
#define C45_REG		0x0cu
#define STATUS		0x10u
#define CONTROL		0x14u

#define STATUS_DONE	(1u << 0)
#define CTRL_REG_MASK	0x1fu
#define CTRL_PHY_SHIFT	5
#define CTRL_OP_SHIFT	10
#define CTRL_C22	(1u << 12)
#define CTRL_C45	(1u << 13)
#define CTRL_START	(1u << 14)

#define OP_C45_ADDR	0u
#define OP_C22_READ	2u
#define OP_C45_READ	3u

#define DEFAULT_BASE	0x14f02000u

static volatile uint32_t *regs;

static void poke(uint32_t off, uint32_t v)
{
	regs[off / 4] = v;
}

static uint32_t peek(uint32_t off)
{
	return regs[off / 4];
}

/* zx279133_mdio_transfer(), in userspace and without a wait_event: clear DONE, start the
 * operation, wait for DONE, then leave the controller as the driver leaves it. */
static int transfer(uint32_t control)
{
	int i;

	poke(STATUS, 0);
	poke(CONTROL, control | CTRL_START);
	for (i = 0; i < 400; i++) {
		if (peek(STATUS) & STATUS_DONE)
			break;
		usleep(50);
	}
	if (i == 400) {
		fprintf(stderr, "mdread: transfer timed out (control 0x%08x)\n", control);
		return -1;
	}
	poke(STATUS, 0);
	poke(CONTROL, control);
	return 0;
}

static int read_c22(int addr, int regnum, uint32_t *out)
{
	uint32_t control = CTRL_C22 | (OP_C22_READ << CTRL_OP_SHIFT) |
			   ((uint32_t)addr << CTRL_PHY_SHIFT) |
			   ((uint32_t)regnum & CTRL_REG_MASK);

	if (transfer(control) != 0)
		return -1;
	*out = peek(READ_DATA) & 0xffffu;
	return 0;
}

static int read_c45(int addr, int devad, int regnum, uint32_t *out)
{
	uint32_t control = CTRL_C45 | ((uint32_t)addr << CTRL_PHY_SHIFT) |
			   ((uint32_t)devad & CTRL_REG_MASK);

	poke(C45_REG, (uint32_t)regnum & 0xffffu);
	if (transfer(control | (OP_C45_ADDR << CTRL_OP_SHIFT)) != 0)
		return -1;
	if (transfer(control | (OP_C45_READ << CTRL_OP_SHIFT)) != 0)
		return -1;
	*out = peek(READ_DATA) & 0xffffu;
	return 0;
}

static unsigned long num(const char *s)
{
	return strtoul(s, NULL, 0);
}

int main(int argc, char **argv)
{
	unsigned long base = DEFAULT_BASE;
	const char *env = getenv("BASE");
	int i, mode_c45, addr, devad, reg, count = 1, fd;
	char *end;

	if (argc < 4) {
		fprintf(stderr,
			"usage: mdread c22 <addr> <reg> [count]\n"
			"       mdread c45 <addr> <devad> <reg> [count]\n");
		return 2;
	}
	mode_c45 = strcmp(argv[1], "c45") == 0;
	if (!mode_c45 && strcmp(argv[1], "c22") != 0) {
		fprintf(stderr, "mdread: %s is neither c22 nor c45\n", argv[1]);
		return 2;
	}
	addr = (int)num(argv[2]);
	if (mode_c45) {
		devad = (int)num(argv[3]);
		reg = argc > 4 ? (int)num(argv[4]) : 0;
		if (argc > 5)
			count = (int)num(argv[5]);
	} else {
		devad = 0;
		reg = (int)num(argv[3]);
		if (argc > 4)
			count = (int)num(argv[4]);
	}
	if (addr < 0 || addr > 0x1f || reg < 0 || reg > 0xffff || count < 1) {
		fprintf(stderr, "mdread: argument out of range\n");
		return 2;
	}
	if (!mode_c45 && reg + count - 1 > 0x1f) {
		fprintf(stderr, "mdread: c22 registers stop at 0x1f\n");
		return 2;
	}
	if (env && *env) {
		base = strtoul(env, &end, 0);
		if (end == env) {
			fprintf(stderr, "mdread: BASE=%s is not an address\n", env);
			return 2;
		}
	}

	fd = open("/dev/mem", O_RDWR | O_SYNC);
	if (fd < 0) {
		fprintf(stderr, "mdread: open /dev/mem: %s\n", strerror(errno));
		return 1;
	}
	regs = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd,
		    (off_t)(base & ~0xffful));
	if (regs == MAP_FAILED) {
		fprintf(stderr, "mdread: mmap 0x%lx: %s\n", base, strerror(errno));
		close(fd);
		return 1;
	}
	regs = (volatile uint32_t *)((char *)regs + (base & 0xffful));

	for (i = 0; i < count; i++) {
		uint32_t v;
		int r;

		if (mode_c45)
			r = read_c45(addr, devad, reg + i, &v);
		else
			r = read_c22(addr, reg + i, &v);
		if (r) {
			munmap((void *)(regs - (base & 0xfff) / 4), 4096);
			close(fd);
			return 1;
		}
		if (mode_c45)
			printf("%d.%04x = %04x\n", devad, reg + i, v);
		else
			printf("c22.%02x = %04x\n", reg + i, v);
	}
	close(fd);
	return 0;
}
