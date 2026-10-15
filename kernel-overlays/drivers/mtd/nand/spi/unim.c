// SPDX-License-Identifier: GPL-2.0
/*
 * Unim (UM19A1H series) SPI NAND, as fitted to the Skyworth SK-D840N.
 *
 * No mainline spi-nand table has manufacturer 0xb0, so without this file the chip is
 * rejected at probe and the whole MTD subsystem never appears:
 *
 *   spi-nand spi0.0: unknown raw ID b024b024b0
 *   spi-nand spi0.0: probe with driver spi-nand failed with error -95
 *
 * while the vendor 4.19 BSP on the same silicon says
 *
 *   spi-nand spi1.0: ID b024b024, Unim SPI NAND was found.
 *   spi-nand spi1.0: 256 MiB, block size: 128 KiB, page size: 2048, OOB size: 128
 *
 * and its U-Boot agrees ("READ ID data: b0-24-b0"), naming the part UM19A1HISW.
 * Geometry and the on-die ECC claim come from those two readings; the OOB and status
 * layouts are the standard 2 Gbit / 2 KiB+128 / 8-bit-per-512 one shared with W25N02KV.
 * What proves the declaration is not just plausible is a read, not a mount: mainline's
 * mtdblock hash of the jffs2 the 4.19 wrote has to equal the 4.19's own hash of it.
 */

#include <linux/device.h>
#include <linux/kernel.h>
#include <linux/mtd/spinand.h>

#define SPINAND_MFR_UNIM		0xb0

static SPINAND_OP_VARIANTS(read_cache_variants,
		SPINAND_PAGE_READ_FROM_CACHE_1S_4S_4S_OP(0, 2, NULL, 0, 0),
		SPINAND_PAGE_READ_FROM_CACHE_1S_1S_4S_OP(0, 1, NULL, 0, 0),
		SPINAND_PAGE_READ_FROM_CACHE_1S_1S_2S_OP(0, 1, NULL, 0, 0),
		SPINAND_PAGE_READ_FROM_CACHE_FAST_1S_1S_1S_OP(0, 1, NULL, 0, 0),
		SPINAND_PAGE_READ_FROM_CACHE_1S_1S_1S_OP(0, 1, NULL, 0, 0));

static SPINAND_OP_VARIANTS(write_cache_variants,
		SPINAND_PROG_LOAD_1S_1S_4S_OP(true, 0, NULL, 0),
		SPINAND_PROG_LOAD_1S_1S_1S_OP(true, 0, NULL, 0));

static SPINAND_OP_VARIANTS(update_cache_variants,
		SPINAND_PROG_LOAD_1S_1S_4S_OP(false, 0, NULL, 0),
		SPINAND_PROG_LOAD_1S_1S_1S_OP(false, 0, NULL, 0));

static int um19a1h_ooblayout_ecc(struct mtd_info *mtd, int section,
				 struct mtd_oob_region *region)
{
	if (section > 3)
		return -ERANGE;

	region->offset = 64 + (16 * section);
	region->length = 13;

	return 0;
}

static int um19a1h_ooblayout_free(struct mtd_info *mtd, int section,
				  struct mtd_oob_region *region)
{
	if (section > 3)
		return -ERANGE;

	region->offset = (16 * section) + 2;
	region->length = 14;

	return 0;
}

static const struct mtd_ooblayout_ops um19a1h_ooblayout = {
	.ecc = um19a1h_ooblayout_ecc,
	.free = um19a1h_ooblayout_free,
};

static int um19a1h_ecc_get_status(struct spinand_device *spinand, u8 status)
{
	struct nand_device *nand = spinand_to_nand(spinand);

	switch (status & STATUS_ECC_MASK) {
	case STATUS_ECC_NO_BITFLIPS:
		return 0;

	case STATUS_ECC_UNCOR_ERROR:
		return -EBADMSG;

	case STATUS_ECC_HAS_BITFLIPS:
		/* The chip corrects on its own and only reports "some flips", so the
		 * best available answer is the declared strength; reporting less would
		 * make the UBI/jffs2 layer believe there is margin it cannot see.
		 */
		return nanddev_get_ecc_conf(nand)->strength;

	default:
		break;
	}

	return -EINVAL;
}

static const struct spinand_info unim_spinand_table[] = {
	/* 2 Gbit: 2048 blocks x 64 pages x (2048 + 128) B */
	SPINAND_INFO("UM19A1HISW",
		     SPINAND_ID(SPINAND_READID_METHOD_OPCODE_DUMMY, 0x24),
		     NAND_MEMORG(1, 2048, 128, 64, 2048, 40, 1, 1, 1),
		     NAND_ECCREQ(8, 512),
		     SPINAND_INFO_OP_VARIANTS(&read_cache_variants,
					      &write_cache_variants,
					      &update_cache_variants),
		     0,
		     SPINAND_ECCINFO(&um19a1h_ooblayout,
				     um19a1h_ecc_get_status)),
};

static const struct spinand_manufacturer_ops unim_spinand_manuf_ops = {
};

const struct spinand_manufacturer unim_spinand_manufacturer = {
	.id = SPINAND_MFR_UNIM,
	.name = "Unim",
	.chips = unim_spinand_table,
	.nchips = ARRAY_SIZE(unim_spinand_table),
	.ops = &unim_spinand_manuf_ops,
};
