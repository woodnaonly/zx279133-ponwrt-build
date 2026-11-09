/* SPDX-License-Identifier: GPL-2.0-only */
/* Generic-PHY refcount and failure-injection harness, not hardware acceptance. */
typedef unsigned int u32;
typedef _Bool bool;
typedef __SIZE_TYPE__ size_t;
/* Freestanding Windows builds have no CRT; structure assignment uses these. */
void *memset(void *destination, int value, size_t count) {
    volatile unsigned char *out = destination;
    for (size_t i = 0; i < count; ++i) out[i] = (unsigned char)value;
    return destination;
}
void *memcpy(void *destination, const void *source, size_t count) {
    unsigned char *out = destination;
    const unsigned char *in = source;
    for (size_t i = 0; i < count; ++i) out[i] = in[i];
    return destination;
}
#define true 1
#define false 0
#define NULL ((void *)0)
#define EIO 5
#define ENODEV 19
#define ENOTSUPP 524
#define SPEED_1000 1000
#define SPEED_2500 2500
#define PHY_MODE_ETHERNET 1
#define PHY_INTERFACE_MODE_SGMII 2
#define PHY_INTERFACE_MODE_2500BASEX 3
#define ZX279133_XMAC_SPEED_1G 3
#define ZX279133_XMAC_SPEED_2P5G 6
#define ZX279133_SWITCH_OUTERPHY_SERDES_MODE_1G 2
#define ZX279133_SWITCH_OUTERPHY_SERDES_MODE_2G5 0x16
#define MDIO_MMD_VEND2 0x1f
#define MII_BMCR 0
#define IS_ERR_OR_NULL(x) (!(x))
#define CHECK(x) do { if (!(x)) return __LINE__; } while (0)
typedef int phy_interface_t;
struct mutex { int unused; };
struct device { int unused; };
struct bus { struct device *parent; };
struct mdio_device { struct bus *bus; };
struct phy;
struct phy_ops { int (*power_on)(struct phy *); int (*power_off)(struct phy *); };
struct phy {
    int power_count, pm_count;
    void *pwr;
    struct mutex mutex;
    struct device dev;
    const struct phy_ops *ops;
};
struct zx279133_switch {
    struct phy *serdes;
    struct mdio_device *xpcs_mdiodev;
    struct device *dev;
    void *service;
    struct mutex lane_lock;
    int lane_work, lane_class;
    bool serdes_powered, usxgmii, datapath_ready, xpcs_runtime_held, datapath_held;
};
static int on_calls, off_calls, mode_calls, live, fail_on, fail_off, fail_mode;
static int fail_2500_on, fail_2500_mode, fail_2500_pcs, fail_pcs, fail_bypass, fail_words;
static int selected_mode, xmac_speed, min_power, min_pm;
static void mutex_lock(struct mutex *p) { (void)p; }
static void mutex_unlock(struct mutex *p) { (void)p; }
static void dev_info(struct device *d, const char *format, ...) { (void)d; (void)format; }
#define dev_warn dev_info
#define dev_err dev_info
static int regulator_enable(void *p) { (void)p; return 0; }
static void regulator_disable(void *p) { (void)p; }
static int phy_pm_runtime_get_sync(struct phy *p) { ++p->pm_count; return 0; }
static void phy_pm_runtime_put(struct phy *p) {
    --p->pm_count;
    if (p->pm_count < min_pm) min_pm = p->pm_count;
}
#define phy_pm_runtime_put_sync phy_pm_runtime_put
static int provider_on(struct phy *p) {
    (void)p; ++on_calls;
    if (fail_on || (fail_2500_on && selected_mode == PHY_INTERFACE_MODE_2500BASEX)) return -EIO;
    live = 1; return 0;
}
static int provider_off(struct phy *p) {
    (void)p; ++off_calls;
    if (fail_off) return -EIO;
    live = 0; return 0;
}
static const struct phy_ops provider_ops = { provider_on, provider_off };

/* PHY_CORE_FUNCTIONS */

static int phy_set_mode_ext(struct phy *p, int mode, int submode) {
    (void)p; (void)mode; ++mode_calls;
    if (fail_mode || (fail_2500_mode && submode == PHY_INTERFACE_MODE_2500BASEX)) return -EIO;
    selected_mode = submode; return 0;
}
static int zx279133_switch_xpcs_2500basex(struct zx279133_switch *p) {
    (void)p; return (fail_pcs || fail_2500_pcs) ? -EIO : 0;
}
static int zx279133_switch_xpcs_sgmii(struct zx279133_switch *p) { (void)p; return fail_pcs ? -EIO : 0; }
static int zx279133_switch_xpcs_bypass(struct zx279133_switch *p, bool enable) {
    (void)p; (void)enable; return fail_bypass ? -EIO : 0;
}
static void zx279133_switch_xmac_set_speed(struct zx279133_switch *p, u32 speed) { (void)p; xmac_speed = (int)speed; }
static int zx279133_switch_xpcs_read(struct zx279133_switch *p, int mmd, int reg) {
    (void)p; (void)mmd; (void)reg; return 0x1140;
}
static int zx279133_switch_xpcs_program_lane(struct zx279133_switch *p) { (void)p; return fail_words ? -EIO : 0; }
static void cancel_delayed_work_sync(int *p) { (void)p; }
static void zx279133_lan_service_set_dsa_active(void *p, bool v) { (void)p; (void)v; }
#define zx279133_lan_service_datapath_set_ready zx279133_lan_service_set_dsa_active
static bool zx279133_lan_service_control_begin(void *p) { (void)p; return true; }
static void zx279133_switch_xmac_restore(struct zx279133_switch *p) { (void)p; }
#define zx279133_switch_xpcs_restore zx279133_switch_xmac_restore
static void zx279133_lan_service_control_end(void *p) { (void)p; }
#define zx279133_lan_service_datapath_put zx279133_lan_service_control_end
static void pm_runtime_put_autosuspend(struct device *p) { (void)p; }
static void mdio_device_put(struct mdio_device *p) { (void)p; }

/* DRIVER_FUNCTIONS */

static struct phy phy_instance;
static struct device device_instance;
static struct bus bus_instance = { &device_instance };
static struct mdio_device pcs_instance = { &bus_instance };
static struct zx279133_switch sw;
static void reset_state(void) {
    phy_instance = (struct phy){ .ops = &provider_ops };
    sw = (struct zx279133_switch){ .serdes = &phy_instance,
        .xpcs_mdiodev = &pcs_instance, .dev = &device_instance };
    on_calls = off_calls = mode_calls = live = fail_on = fail_off = fail_mode = 0;
    fail_2500_on = fail_2500_mode = fail_2500_pcs = fail_pcs = fail_bypass = fail_words = 0;
    selected_mode = xmac_speed = min_power = min_pm = 0;
}
static int apply(int speed) {
    int ret = zx279133_switch_outerphy_apply_speed(&sw, speed);
    if (phy_instance.power_count < min_power) min_power = phy_instance.power_count;
    return ret;
}
static int healthy(void) {
    return phy_instance.power_count == 1 && phy_instance.pm_count == 1 &&
        sw.serdes_powered && live && min_power == 0 && min_pm == 0;
}
static int released(void) {
    return phy_instance.power_count == 0 && phy_instance.pm_count == 0 &&
        !sw.serdes_powered && !live && min_power == 0 && min_pm == 0;
}
int main(void) {
    /* First power-on must invoke the provider, without any preceding off. */
    reset_state();
    CHECK(apply(SPEED_1000) == 0);
    CHECK(on_calls == 1 && off_calls == 0 && healthy());
    CHECK(xmac_speed == ZX279133_XMAC_SPEED_1G);
    /* Repeated 1G/2.5G changes keep exactly one owned power/PM reference. */
    for (int i = 0; i < 100; ++i) {
        CHECK(apply((i & 1) ? SPEED_1000 : SPEED_2500) == 0);
        CHECK(healthy());
    }
    CHECK(on_calls == 101 && off_calls == 100);
    zx279133_switch_release(&sw);
    CHECK(released() && off_calls == 101);
    zx279133_switch_release(&sw);
    CHECK(released() && off_calls == 101);
    /* A failed off retains ownership and must not attempt the new mode. */
    reset_state(); CHECK(apply(SPEED_1000) == 0);
    fail_off = 1;
    CHECK(apply(SPEED_1000) == -EIO && healthy() && mode_calls == 1);
    fail_off = 0; zx279133_switch_release(&sw); CHECK(released());
    /* A failed mode set, first or during reconfiguration, owns no reference. */
    reset_state(); fail_mode = 1;
    CHECK(apply(SPEED_1000) == -EIO && released() && on_calls == 0 && off_calls == 0);
    zx279133_switch_release(&sw); CHECK(released() && off_calls == 0);
    reset_state(); CHECK(apply(SPEED_1000) == 0); fail_mode = 1;
    CHECK(apply(SPEED_1000) == -EIO && released() && off_calls == 1);
    zx279133_switch_release(&sw); CHECK(released() && off_calls == 1);
    /* A provider-on failure is balanced by the PHY core, not by another off. */
    reset_state(); fail_on = 1;
    CHECK(apply(SPEED_1000) == -EIO && released() && off_calls == 0);
    fail_on = 0; CHECK(apply(SPEED_1000) == 0 && healthy());
    /* PCS/bypass/final-word failures still own a reference for cleanup. */
    for (int i = 0; i < 3; ++i) {
        reset_state(); fail_pcs = i == 0; fail_bypass = i == 1; fail_words = i == 2;
        CHECK(apply(SPEED_1000) == -EIO && healthy());
        zx279133_switch_release(&sw); CHECK(released() && off_calls == 1);
    }
    /* Fallback at each stage must not steal a nonexistent reference. */
    for (int i = 0; i < 3; ++i) {
        reset_state(); fail_2500_mode = i == 0; fail_2500_on = i == 1; fail_2500_pcs = i == 2;
        CHECK(apply(SPEED_2500) == 0 && healthy());
        CHECK(selected_mode == PHY_INTERFACE_MODE_SGMII && xmac_speed == ZX279133_XMAC_SPEED_1G);
        CHECK(off_calls == (i == 2 ? 1 : 0));
        zx279133_switch_release(&sw); CHECK(released());
    }
    reset_state(); sw.serdes = NULL;
    CHECK(apply(SPEED_1000) == -ENODEV && on_calls == 0 && off_calls == 0);
    reset_state(); sw.xpcs_mdiodev = NULL;
    CHECK(apply(SPEED_1000) == -ENODEV && healthy());
    zx279133_switch_release(&sw); CHECK(released());
    return 0;
}
