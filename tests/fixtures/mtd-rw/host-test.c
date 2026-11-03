/* SPDX-License-Identifier: GPL-2.0-only
 * Host-only model: no Linux MTD driver, device node, or flash writer exists.
 * The test runner places the pinned upstream file plus our patch next to it.
 */
#include "test-kernel.h"

static struct mtd_info devices[65];
static bool present[65];
static unsigned references, lookup_calls;

static struct mtd_info *get_mtd_device(void *unused, unsigned index)
{
    (void)unused;
    lookup_calls++;
    if (index >= 65 || !present[index])
        return (struct mtd_info *)(intptr_t)-ENODEV;
    references++;
    return &devices[index];
}

static void put_mtd_device(struct mtd_info *device)
{
    (void)device;
    references--;
}

static void printk(const char *format, ...)
{
    (void)format;
}

#include "mtd-rw.c"

#define CHECK(condition) do { if (!(condition)) { \
    fprintf(stderr, "failed at line %d: %s\n", __LINE__, #condition); return 1; } } while (0)

static void reset_model(void)
{
    memset(devices, 0, sizeof(devices));
    memset(present, 0, sizeof(present));
    references = lookup_calls = 0;
    unlocked = mtd_last = 0;
    i_want_a_brick = false;
}

int main(void)
{
    unsigned i;
    reset_model();
    present[1] = true;
    CHECK(mtd_rw_init() == -EINVAL);
    CHECK(lookup_calls == 0 && references == 0 && devices[1].flags == 0);

    /* The real board has no mtd0; boot/tags are RO, the rest stay writable. */
    reset_model();
    for (i = 1; i <= 6; i++) {
        present[i] = true;
        devices[i].flags = i == 1 || i == 4 ? 0 : MTD_WRITEABLE;
    }
    i_want_a_brick = true;
    CHECK(mtd_rw_init() == 0);
    CHECK(unlocked == ((1ULL << 1) | (1ULL << 4)) && references == 0);
    for (i = 1; i <= 6; i++)
        CHECK(devices[i].flags & MTD_WRITEABLE);
    mtd_rw_exit();
    CHECK(references == 0);
    for (i = 1; i <= 6; i++)
        CHECK(devices[i].flags == (i == 1 || i == 4 ? 0 : MTD_WRITEABLE));

    /* Interior holes and indices above 31 must not alias low bitmap bits. */
    reset_model();
    present[0] = present[1] = present[40] = present[63] = true;
    devices[1].flags = MTD_WRITEABLE;
    i_want_a_brick = true;
    CHECK(mtd_rw_init() == 0);
    CHECK(unlocked == ((1ULL << 0) | (1ULL << 40) | (1ULL << 63)));
    CHECK(devices[40].flags & MTD_WRITEABLE);
    CHECK(devices[63].flags & MTD_WRITEABLE);
    mtd_rw_exit();
    CHECK(devices[0].flags == 0 && devices[40].flags == 0 && devices[63].flags == 0);
    CHECK(devices[1].flags == MTD_WRITEABLE && references == 0);

    reset_model();
    i_want_a_brick = true;
    CHECK(mtd_rw_init() == -ENODEV && references == 0);

    reset_model();
    present[64] = true;
    i_want_a_brick = true;
    CHECK(mtd_rw_init() == -ENODEV);
    CHECK(lookup_calls == 64 && devices[64].flags == 0 && references == 0);
    puts("PASS: opt-in, missing mtd0, sparse aliases, 64-bit restore and bounded scan");
    return 0;
}
