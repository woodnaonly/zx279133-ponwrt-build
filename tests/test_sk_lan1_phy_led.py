#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile the actual PHY-field engine and actual LED lifecycle callbacks."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / 'kernel-overlays/drivers/net/ethernet/zte'

# These are Linux ABI values, not the host libc's errno implementation. On
# glibc, <errno.h> includes <linux/errno.h>; forwarding our shim back to libc
# recurses through that include and leaves EIO/EINVAL/etc. undefined (#338).
LINUX_ERRNO_STUB = '''#ifndef SK_LAN1_TEST_LINUX_ERRNO_H
#define SK_LAN1_TEST_LINUX_ERRNO_H
#define EIO 5
#define EEXIST 17
#define ENODEV 19
#define EINVAL 22
#define ETIMEDOUT 110
#endif
'''


def check_errno_fixture(compiler, root):
    """Reproduce glibc's errno include cycle, also on non-Linux hosts."""
    recursive = root / 'host-errno-cycle'
    (recursive / 'linux').mkdir(parents=True)
    (recursive / 'errno.h').write_text(
        '#ifndef SK_LAN1_TEST_HOST_ERRNO_H\n#define SK_LAN1_TEST_HOST_ERRNO_H\n'
        '#include <linux/errno.h>\n#endif\n', encoding='utf-8')
    (recursive / 'linux/errno.h').write_text(
        '#include <errno.h>\n#ifndef ETIMEDOUT\n#define ETIMEDOUT 110\n#endif\n',
        encoding='utf-8')
    source = recursive / 'check.c'
    source.write_text(
        '#include <linux/errno.h>\n' + ''.join(
            f'_Static_assert({name} == {value}, "{name} Linux ABI");\n'
            for name, value in (('EIO', 5), ('EEXIST', 17), ('ENODEV', 19),
                                ('EINVAL', 22), ('ETIMEDOUT', 110))),
        encoding='utf-8')
    flags = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-c',
             '-o', str(recursive / 'check.o')]
    broken = subprocess.run(compiler + flags + ['-I' + str(recursive), str(source)],
                            capture_output=True, text=True, timeout=90)
    assert broken.returncode != 0 and 'EIO' in broken.stderr, (
        'Regression fixture did not reproduce #338: ' + broken.stdout + broken.stderr)
    fixed = subprocess.run(compiler + flags + ['-I' + str(root),
                           '-I' + str(recursive), str(source)],
                           capture_output=True, text=True, timeout=90)
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    print('PASS Linux errno include-cycle regression: old shim rejected, self-contained shim accepted')


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2]


STUBS = r'''
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "zx279133-lan1-led.h"
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define to_delayed_work(p) container_of(p,struct delayed_work,work)
#define msecs_to_jiffies(x) (x)
#define ZX279133_SWITCH_OUTERPHY_ADDR 5
#define LED_REJECT_NAME_CONFLICT 0x1000000
enum led_brightness { LED_OFF=0, LED_ON=1 };
struct device { int unused; };
struct device_node { int unused; };
struct mii_bus { int unused; };
struct mutex { int unused; };
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; };
struct led_init_data { void *fwnode; };
struct led_classdev {
    const char *name;
    unsigned int max_brightness, flags;
    int (*brightness_set_blocking)(struct led_classdev *,enum led_brightness);
    const void *groups;
};
struct zx279133_switch;
struct zx279133_lan1_led {
    struct zx279133_switch *priv;
    struct device_node *node;
    struct led_classdev cdev;
    struct zx_lan1_led_fields fields;
    struct mutex lock;
    struct delayed_work work;
    unsigned int attempts;
    bool registered, stopping;
};
struct zx279133_switch {
    struct device *dev;
    struct mutex lane_lock;
    struct zx279133_lan1_led lan1_led;
};
static const void *zx279133_lan1_led_groups;
static struct mii_bus bus;
static u16 words[3], initial[3];
static int reads, writes, fail_read_n, fail_write_reg, corrupt_reg;
static int ready, schedules, delay, cancelled, unregistered, put, register_error;
static int notices, errors;
static struct zx279133_lan1_led *stop_at_bus_lookup;
static int index_of(u16 reg) {
    for (int i=0;i<3;i++) if (zx_lan1_led_regs[i]==reg) return i;
    assert(!"access outside the three authorized LED registers"); return -1;
}
static int io_read(void *context,u16 reg) {
    (void)context; int i=index_of(reg); reads++;
    if (reads==fail_read_n) return -ETIMEDOUT;
    return words[i];
}
static int io_write(void *context,u16 reg,u16 value) {
    (void)context; int i=index_of(reg); writes++;
    assert(!((words[i]^value)&(u16)~zx_lan1_led_masks[i]));
    if (fail_write_reg==(int)reg) { fail_write_reg=0; return -ETIMEDOUT; }
    words[i]=value;
    if (corrupt_reg==(int)reg) { corrupt_reg=0; words[i]^=1; }
    return 0;
}
static int mdiobus_c45_read(void *context,int phy,int mmd,u16 reg) {
    assert(phy==5 && mmd==0x1f); return io_read(context,reg);
}
static int mdiobus_c45_write(void *context,int phy,int mmd,u16 reg,u16 value) {
    assert(phy==5 && mmd==0x1f); return io_write(context,reg,value);
}
static void mutex_lock(struct mutex *m) { (void)m; }
static void mutex_unlock(struct mutex *m) { (void)m; }
static struct mii_bus *zx279133_switch_outer_mdio(struct zx279133_switch *p) {
    (void)p;
    if (stop_at_bus_lookup) stop_at_bus_lookup->stopping=true;
    return ready ? &bus : NULL;
}
static void *of_fwnode_handle(struct device_node *n) { return n; }
static void of_node_put(struct device_node *n) { assert(n); put++; }
static void schedule_delayed_work(struct delayed_work *w,int ms) {
    (void)w; schedules++; delay=ms;
}
static void cancel_delayed_work_sync(struct delayed_work *w) { (void)w; cancelled++; }
static int led_classdev_register_ext(struct device *d,struct led_classdev *c,struct led_init_data *i) {
    (void)d; assert(i->fwnode && c->max_brightness==1 && c->flags==LED_REJECT_NAME_CONFLICT);
    assert(!strcmp(c->name,"skyworth:green:lan1")); return register_error;
}
static void led_classdev_unregister(struct led_classdev *c) {
    int before=writes; assert(cancelled);
    assert(c->brightness_set_blocking(c,LED_OFF)==0);
    assert(writes==before); unregistered++;
}
static void dev_err(struct device *d,const char *fmt,...) { (void)d; (void)fmt; errors++; }
static void dev_err_ratelimited(struct device *d,const char *fmt,...) { (void)d; (void)fmt; errors++; }
static void dev_info(struct device *d,const char *fmt,...) { (void)d; (void)fmt; notices++; }
static void reset(void) {
    words[0]=initial[0]=0xa5a7; words[1]=initial[1]=0x391f; words[2]=initial[2]=0x40fb;
    reads=writes=fail_read_n=fail_write_reg=corrupt_reg=0;
    ready=schedules=delay=cancelled=unregistered=put=register_error=notices=errors=0;
    stop_at_bus_lookup=NULL;
}
static struct zx_lan1_led_fields fields(void) {
    struct zx_lan1_led_fields f={0}; f.io.read=io_read; f.io.write=io_write; return f;
}
'''

CASES = r'''
static void equal_originals(void) { assert(!memcmp(words,initial,sizeof(words))); }
int main(void) {
    struct zx_lan1_led_fields f;
    struct zx279133_switch p;
    struct device dev;
    struct device_node node;
    int before;
    reset(); f=fields(); assert(zx_lan1_led_claim(&f)==0);
    for (unsigned int n=0;n<200;n++) {
        assert(zx_lan1_led_set(&f,n&1)==0);
        for (int i=0;i<3;i++) assert((words[i]&~zx_lan1_led_masks[i])==(initial[i]&~zx_lan1_led_masks[i]));
        assert(!(words[0]&0x27) && !(words[1]&1) && (words[2]&1)==(n&1));
    }
    before=writes; assert(zx_lan1_led_set(&f,2)==-EINVAL && writes==before);
    assert(zx_lan1_led_restore(&f)==0 && !f.owned); equal_originals();
    puts("PASS PHY field masks, 200 toggles, invalid level and full restoration");
    for (int n=1;n<=3;n++) {
        reset(); f=fields(); fail_read_n=n;
        assert(zx_lan1_led_claim(&f)==-ETIMEDOUT && !f.owned && !writes); equal_originals();
    }
    for (int i=0;i<3;i++) {
        reset(); f=fields(); words[i]=0xffff;
        assert(zx_lan1_led_claim(&f)==-EIO && !f.owned && !writes);
        for (int mismatch=0;mismatch<2;mismatch++) {
            reset(); f=fields();
            if (mismatch) corrupt_reg=zx_lan1_led_regs[i]; else fail_write_reg=zx_lan1_led_regs[i];
            assert(zx_lan1_led_claim(&f)<0 && f.faulted && !f.owned); equal_originals();
            before=writes; assert(zx_lan1_led_set(&f,1)==-ENODEV && writes==before);
        }
    }
    puts("PASS baseline read timeouts, unreliable words, each field write failure/mismatch and no retry after fault");
    for (int i=0;i<3;i++) {
        reset(); f=fields(); assert(!zx_lan1_led_claim(&f));
        fail_write_reg=zx_lan1_led_regs[i];
        assert(zx_lan1_led_restore(&f)<0 && f.owned);
        for (int j=0;j<3;j++) if (i!=j) assert(words[j]==initial[j]);
    }
    reset(); f=fields(); assert(!zx_lan1_led_claim(&f)); words[0]^=0x100;
    assert(zx_lan1_led_restore(&f)<0 && f.owned && words[0]==(initial[0]^0x100));
    assert(words[1]==initial[1] && words[2]==initial[2]);
    puts("PASS partial restoration continues and external unrelated bits are preserved without false success");
    reset(); memset(&p,0,sizeof(p)); p.dev=&dev; p.lan1_led.priv=&p; p.lan1_led.node=&node;
    for (int n=0;n<30;n++) zx279133_lan1_led_work(&p.lan1_led.work.work);
    assert(p.lan1_led.attempts==30 && schedules==29 && delay==1000 && !writes && !p.lan1_led.registered);
    zx279133_lan1_led_cleanup(&p.lan1_led);
    assert(cancelled==1 && !unregistered && put==1);
    puts("PASS actual worker: at most 30 bus attempts, one-second deferral and pending-work cleanup");
    reset(); memset(&p,0,sizeof(p)); p.dev=&dev; p.lan1_led.priv=&p; p.lan1_led.node=&node;
    ready=1; stop_at_bus_lookup=&p.lan1_led;
    zx279133_lan1_led_work(&p.lan1_led.work.work);
    assert(!reads && !writes && !p.lan1_led.registered && !p.lan1_led.fields.owned);
    zx279133_lan1_led_cleanup(&p.lan1_led);
    assert(cancelled==1 && !unregistered && put==1); equal_originals();
    puts("PASS actual worker: stop arriving during bus lookup prevents PHY claim");
    for (int duplicate=0;duplicate<2;duplicate++) {
        reset(); memset(&p,0,sizeof(p)); p.dev=&dev; p.lan1_led.priv=&p; p.lan1_led.node=&node;
        ready=1; register_error=duplicate ? -EEXIST : 0;
        zx279133_lan1_led_work(&p.lan1_led.work.work);
        assert(p.lan1_led.registered==!duplicate);
        if (!duplicate) {
            assert(zx279133_lan1_led_brightness(&p.lan1_led.cdev,LED_ON)==0);
            assert(words[2]&1);
        } else equal_originals();
        zx279133_lan1_led_cleanup(&p.lan1_led);
        assert(cancelled && unregistered==!duplicate && put==1 && !p.lan1_led.node);
        equal_originals();
    }
    puts("PASS actual callbacks: unique registration, manual level, unregister-before-restore and registration-failure rollback");
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc', default=shutil.which('cc') or shutil.which('gcc') or shutil.which('clang') or shutil.which('zig'))
    args = parser.parse_args()
    if not args.cc:
        parser.error('C compiler required; pass --cc; CI must not skip this test')
    source = (DRIVER / 'zx279133-switch.c').read_text(encoding='utf-8')
    signatures = ('static int zx279133_lan1_led_read(', 'static int zx279133_lan1_led_write(',
                  'static int zx279133_lan1_led_brightness(', 'static void zx279133_lan1_led_work(',
                  'static void zx279133_lan1_led_cleanup(')
    compiled = STUBS + '\n'.join(function(source, s) for s in signatures) + CASES
    with tempfile.TemporaryDirectory(prefix='sk-lan1-phy-led-') as directory:
        root = Path(directory)
        (root / 'linux').mkdir()
        (root / 'linux/types.h').write_text('#include <stdint.h>\n#include <stdbool.h>\ntypedef uint16_t u16;\n', encoding='utf-8')
        (root / 'linux/errno.h').write_text(LINUX_ERRNO_STUB, encoding='utf-8')
        cfile, executable = root / 'test.c', root / 'test.exe'
        cfile.write_text(compiled, encoding='utf-8')
        command = [args.cc] + (['cc'] if Path(args.cc).stem.lower() == 'zig' else [])
        check_errno_fixture(command, root)
        command += ['-std=c11', '-O0', '-Wall', '-Wextra', '-Werror', '-I' + str(root),
                    '-I' + str(DRIVER), str(cfile), '-o', str(executable)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout, end='')


if __name__ == '__main__':
    main()
