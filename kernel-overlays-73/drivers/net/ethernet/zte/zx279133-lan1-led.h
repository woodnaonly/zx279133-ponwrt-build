/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef ZX279133_LAN1_LED_H
#define ZX279133_LAN1_LED_H

#include <linux/errno.h>
#include <linux/types.h>

/* Verified on this SK-D840N: PHY 5, MMD 0x1f, channel zero only. */
struct zx_lan1_led_io {
	void *context;
	int (*read)(void *context, u16 reg);
	int (*write)(void *context, u16 reg, u16 value);
};

struct zx_lan1_led_fields {
	struct zx_lan1_led_io io;
	u16 saved[3];
	bool owned;
	bool faulted;
	int restore_error;
};

static const u16 zx_lan1_led_regs[] = { 0xd032, 0xd040, 0xd044 };
static const u16 zx_lan1_led_masks[] = { 0x0027, 0x0001, 0x0001 };

static inline int zx_lan1_led_read(struct zx_lan1_led_fields *led, int index)
{
	int value = led->io.read(led->io.context, zx_lan1_led_regs[index]);

	if (value < 0)
		return value;
	if (value > 0xffff || value == 0xffff || value == 0xdead)
		return -EIO;
	return value;
}

static inline int zx_lan1_led_modify(struct zx_lan1_led_fields *led,
				    int index, u16 bits)
{
	u16 wanted;
	int before, actual, ret;

	before = zx_lan1_led_read(led, index);
	if (before < 0)
		return before;
	wanted = (before & ~zx_lan1_led_masks[index]) |
		 (bits & zx_lan1_led_masks[index]);
	if (wanted != before) {
		ret = led->io.write(led->io.context, zx_lan1_led_regs[index], wanted);
		if (ret < 0)
			return ret;
	}
	actual = zx_lan1_led_read(led, index);
	return actual < 0 ? actual : (actual == wanted ? 0 : -EIO);
}

/* Attempt every field even after one fails; never overwrite unrelated bits. */
static inline int zx_lan1_led_restore(struct zx_lan1_led_fields *led)
{
	int index, ret, actual, error = 0;

	if (!led->owned)
		return 0;
	for (index = 2; index >= 0; index--) {
		ret = zx_lan1_led_modify(led, index, led->saved[index]);
		if (ret < 0) {
			if (!error)
				error = ret;
			continue;
		}
		actual = zx_lan1_led_read(led, index);
		if (actual < 0 || actual != led->saved[index]) {
			if (!error)
				error = actual < 0 ? actual : -EIO;
		}
	}
	led->restore_error = error;
	if (!error)
		led->owned = false;
	return error;
}

static inline int zx_lan1_led_set(struct zx_lan1_led_fields *led, unsigned int on)
{
	int index, ret;

	if (on > 1)
		return -EINVAL;
	if (!led->owned || led->faulted)
		return -ENODEV;
	/* Disable only channel zero's link/activity events, then set its level. */
	for (index = 0; index < 3; index++) {
		ret = zx_lan1_led_modify(led, index, index == 2 ? on : 0);
		if (ret < 0) {
			led->faulted = true;
			zx_lan1_led_restore(led);
			return ret;
		}
	}
	return 0;
}

static inline int zx_lan1_led_claim(struct zx_lan1_led_fields *led)
{
	int index, value;

	if (led->owned || led->faulted || !led->io.read || !led->io.write)
		return -EINVAL;
	/* Finish all baseline reads before the first field can change. */
	for (index = 0; index < 3; index++) {
		value = zx_lan1_led_read(led, index);
		if (value < 0)
			return value;
		led->saved[index] = value;
	}
	led->owned = true;
	return zx_lan1_led_set(led, 0);
}

#endif
