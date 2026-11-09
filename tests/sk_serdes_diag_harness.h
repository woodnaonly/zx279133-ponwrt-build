/* SPDX-License-Identifier: GPL-2.0-only */
/* Host lifecycle model only. No real MMIO or PLL hardware acceptance. */
typedef unsigned int u32;
typedef _Bool bool;
typedef __SIZE_TYPE__ size_t;
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
#define __iomem
#define BIT(x) (1U << (x))
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define CHECK(x) do { if (!(x)) return __LINE__; } while (0)
#define PHY_INTERFACE_MODE_SGMII 2
#define PHY_INTERFACE_MODE_2500BASEX 3
struct mutex { int held; };
struct device { int unused; };
struct clk { unsigned long rate; };
struct reset_control { int status; };
struct clk_bulk_data { const char *id; struct clk *clk; };
struct reset_control_bulk_data { const char *id; struct reset_control *rstc; };
/* PROVIDER_DEFINITIONS */
struct phy { struct zx279133_uni_serdes *priv; };
static struct device device_instance;
static struct clk clock_instances[2] = { {125000000}, {50000000} };
static struct reset_control reset_instances[2];
static u32 registers[256];
static struct zx279133_uni_serdes priv_instance;
static struct phy phy_instance = { &priv_instance };
static int invariant_error, clock_refs, enable_failure, program_result, program_calls;
static int writes, reads, reset_reads, reset_pulses, matches, logs;
static unsigned int last_diff1g, last_diff2g5, last_attempt;
static int last_reset_rx, last_reset_tx, last_result;
static void mutex_lock(struct mutex *p) { if (p->held) invariant_error++; p->held++; }
static void mutex_unlock(struct mutex *p) { if (!p->held) invariant_error++; p->held--; }
#define lockdep_assert_held(p) do { if (!(p)->held) invariant_error++; } while (0)
static void guarded(void) {
    if (!clock_refs || !priv_instance.lock.held) invariant_error++;
}
static u32 readl(const void *address) {
    guarded(); reads++;
    if ((const u32 *)address < registers || (const u32 *)address >= registers + ARRAY_SIZE(registers)) {
        invariant_error++; return 0;
    }
    return *(const u32 *)address;
}
static void writel(u32 value, void *address) { guarded(); writes++; *(u32 *)address = value; }
static unsigned long clk_get_rate(struct clk *c) { guarded(); return c->rate; }
static int reset_control_status(struct reset_control *r) { guarded(); reset_reads++; return r->status; }
static int clk_bulk_prepare_enable(size_t count, struct clk_bulk_data *clocks) {
    (void)count; (void)clocks;
    if (!priv_instance.lock.held) invariant_error++;
    if (enable_failure) return -5;
    clock_refs++; return 0;
}
static void clk_bulk_disable_unprepare(size_t count, struct clk_bulk_data *clocks) {
    (void)count; (void)clocks; guarded(); clock_refs--;
}
static void dev_info(struct device *dev, const char *format, ...) __attribute__((format(printf, 2, 3)));
static void dev_info(struct device *dev, const char *format, ...) {
    (void)dev;
    if (format[0] != 'L' || format[1] != 'A' || format[2] != 'N') return;
    __builtin_va_list args;
    __builtin_va_start(args, format);
    last_attempt = __builtin_va_arg(args, unsigned int);
    const char *stage = __builtin_va_arg(args, const char *);
    last_result = __builtin_va_arg(args, int);
    (void)__builtin_va_arg(args, int); /* submode */
    (void)__builtin_va_arg(args, int); /* powered */
    (void)__builtin_va_arg(args, int); /* pending */
    if (__builtin_va_arg(args, unsigned long) != 125000000UL) invariant_error++;
    if (__builtin_va_arg(args, unsigned long) != 50000000UL) invariant_error++;
    last_reset_rx = __builtin_va_arg(args, int);
    last_reset_tx = __builtin_va_arg(args, int);
    (void)__builtin_va_arg(args, u32); /* mode */
    (void)__builtin_va_arg(args, u32); /* word0 */
    (void)__builtin_va_arg(args, u32); /* word50 */
    last_diff1g = __builtin_va_arg(args, unsigned int);
    last_diff2g5 = __builtin_va_arg(args, unsigned int);
    __builtin_va_end(args);
    if ((logs % 2 == 0 && stage[0] != 'b') || (logs % 2 == 1 && stage[0] != 'a')) invariant_error++;
    logs++;
}
#define dev_err dev_info
static struct zx279133_uni_serdes *phy_get_drvdata(struct phy *p) { return p->priv; }
static bool zx279133_uni_serdes_rate_matches(struct zx279133_uni_serdes *p) { (void)p; guarded(); return matches; }
static int zx279133_uni_serdes_program_rate(struct zx279133_uni_serdes *p) {
    (void)p; guarded(); program_calls++; return program_result;
}
static int zx279133_uni_serdes_reset_pulse(struct zx279133_uni_serdes *p) {
    (void)p; guarded(); reset_pulses++; return 0;
}
static void zx279133_uni_serdes_write_profile(struct zx279133_uni_serdes *p) { (void)p; guarded(); writes++; }
#define readl_poll_timeout(address, value, condition, interval, timeout) \
    ((void)(interval), (void)(timeout), (value) = readl(address), (condition) ? 0 : -110)
/* PROVIDER_FUNCTIONS */
static void reset_state(void) {
    priv_instance = (struct zx279133_uni_serdes){ .dev = &device_instance, .serdes = registers,
        .clocks = { { "pclk", &clock_instances[0] }, { "ref50m", &clock_instances[1] } },
        .resets = { { "rx", &reset_instances[0] }, { "tx", &reset_instances[1] } },
        .submode = PHY_INTERFACE_MODE_SGMII };
    for (size_t i = 0; i < ARRAY_SIZE(registers); ++i) registers[i] = 0;
    reset_instances[0].status = 1; reset_instances[1].status = -524;
    invariant_error = clock_refs = enable_failure = program_result = program_calls = 0;
    writes = reads = reset_reads = reset_pulses = matches = logs = 0;
    last_diff1g = last_diff2g5 = last_attempt = 0;
    last_reset_rx = last_reset_tx = last_result = 0;
}
int main(void) {
    /* Clock-enable failure: no diagnostic MMIO/status reads or attempts. */
    reset_state(); enable_failure = 1;
    CHECK(zx279133_uni_serdes_power_on(&phy_instance) == -5);
    CHECK(!reads && !reset_reads && !logs && !clock_refs && !priv_instance.rate_diag_count);
    CHECK(!invariant_error && !priv_instance.lock.held);
    /* First/cold failures stay unpowered and release exactly their clock ref. */
    for (int mode = PHY_INTERFACE_MODE_SGMII; mode <= PHY_INTERFACE_MODE_2500BASEX; mode++) {
        reset_state(); priv_instance.submode = mode; program_result = -110;
        for (int attempt = 1; attempt <= 30; attempt++) {
            CHECK(zx279133_uni_serdes_power_on(&phy_instance) == -110);
            CHECK(!clock_refs && !priv_instance.powered && !invariant_error && !priv_instance.lock.held);
            CHECK(logs == (attempt < 3 ? attempt : 3) * 2);
        }
        CHECK(program_calls == 30 && reset_reads == 12 && last_attempt == 3);
        CHECK(last_reset_rx == 1 && last_reset_tx == -524 && last_result == -110);
        CHECK(!writes && !reset_pulses);
    }
    /* Powered/rate-change failure: keep clocks and mark a pending retry. */
    reset_state(); CHECK(zx279133_uni_serdes_power_on(&phy_instance) == 0);
    CHECK(clock_refs == 1 && priv_instance.powered && logs == 2);
    matches = 1; CHECK(zx279133_uni_serdes_power_on(&phy_instance) == 0);
    CHECK(logs == 2 && program_calls == 1 && clock_refs == 1);
    priv_instance.rate_pending = true; program_result = -110;
    CHECK(zx279133_uni_serdes_power_on(&phy_instance) == -110);
    CHECK(priv_instance.powered && priv_instance.rate_pending && clock_refs == 1 && logs == 4);
    program_result = 0; CHECK(zx279133_uni_serdes_power_on(&phy_instance) == 0);
    CHECK(!priv_instance.rate_pending && clock_refs == 1 && logs == 6);
    CHECK(!writes && !reset_pulses && !invariant_error && !priv_instance.lock.held);
    /* Profile comparison ignores only enable, not unrelated mode/rate bits. */
    for (int mode = 0; mode < 2; mode++) {
        reset_state();
        const u32 *profile = mode ? zx279133_uni_1gbase_x_profile : zx279133_uni_1g_profile;
        for (size_t i = 0; i < ARRAY_SIZE(zx279133_uni_1g_profile); ++i) registers[i] = profile[i];
        registers[ZX279133_UNI_SERDES_MODE_CTRL / 4] ^= ZX279133_UNI_SERDES_ENABLE;
        CHECK(zx279133_uni_serdes_power_on(&phy_instance) == 0);
        CHECK((mode ? last_diff2g5 : last_diff1g) == 0 && !invariant_error);
    }
    return 0;
}
