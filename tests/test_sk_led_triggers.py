#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile the actual LOS worker and PON carrier function with mock LED subscribers.

This tests our driver routing, not the kernel LED core or physical lamps. CI uses
the host C compiler; Windows can also use a standalone Zig compiler.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / 'kernel-overlays/drivers/net/ethernet/zte'


def function(source, signature):
    start = source.index(signature)
    end = source.index('\n}', start) + 2
    return source[start:end]


STUBS = r'''
#define LED_OFF 0
#define LED_FULL 255
#define ZXPON_O5 5
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - __builtin_offsetof(type, member)))
#define to_delayed_work(ptr) container_of(ptr, struct delayed_work, work)
#define msecs_to_jiffies(ms) (ms)
#define mutex_lock(lock) ((void)(lock))
#define mutex_unlock(lock) ((void)(lock))
#define CHECK(condition) do { if (!(condition)) return (__LINE__ % 250) + 1; } while (0)
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; };
struct gpio_desc { int value; };
struct led_trigger { int brightness; int *subscriber; };
struct zx279133_eth {
    struct delayed_work pon_led_work;
    struct gpio_desc *opsd_gpio;
    struct led_trigger *los_trigger;
};
struct net_device { int carrier; };
struct zxpon { int state; };
static struct zxpon zx_state;
static int zx_pon_lock, notes, light, scheduled, delay, cancelled, stopped, unregistered;
static struct net_device *zx_wan;
static struct led_trigger *zx_green;
static int gpiod_get_value_cansleep(struct gpio_desc *gpio) { return gpio->value; }
static void led_trigger_event(struct led_trigger *trigger, int brightness) {
    if (!trigger) return;
    trigger->brightness = brightness;
    if (trigger->subscriber) *trigger->subscriber = brightness;
}
static void led_trigger_unregister_simple(struct led_trigger *trigger) {
    (void)trigger; unregistered = cancelled;
}
static void zx279133_pon_note_rxsd(int present) { ++notes; light = present; }
static void schedule_delayed_work(struct delayed_work *work, int ms) {
    (void)work; ++scheduled; delay = ms;
}
static void cancel_delayed_work_sync(struct delayed_work *work) { (void)work; cancelled = 1; }
static void zx279133_pon_stop(void) { stopped = cancelled; }
static int zxpon_state(struct zxpon *state) { return state->state; }
static void netif_carrier_on(struct net_device *dev) { dev->carrier = 1; }
static void netif_carrier_off(struct net_device *dev) { dev->carrier = 0; }
static int netif_carrier_ok(struct net_device *dev) { return dev->carrier; }
'''

CASES = r'''
int main(void) {
    int los_lamp = 0, pon_lamp = 0, i, previous;
    struct gpio_desc sensor;
    struct led_trigger los, pon;
    struct zx279133_eth eth;
    struct net_device wan;
    los.brightness = 0; los.subscriber = &los_lamp;
    pon.brightness = 0; pon.subscriber = &pon_lamp;
    eth.opsd_gpio = &sensor; eth.los_trigger = &los;
    sensor.value = 0;
    zx279133_pon_led_work(&eth.pon_led_work.work);
    CHECK(los_lamp == LED_FULL && light == 0 && delay == 200 && notes == 1);
    sensor.value = 1;
    zx279133_pon_led_work(&eth.pon_led_work.work);
    CHECK(los_lamp == LED_OFF && light == 1);

    /* User switches to none/default=0, then the input changes repeatedly. */
    los.subscriber = 0; los_lamp = LED_OFF;
    for (i = 0; i < 200; ++i) {
        sensor.value = i & 1;
        zx279133_pon_led_work(&eth.pon_led_work.work);
        CHECK(los_lamp == LED_OFF && light == sensor.value);
    }
    /* A different trigger/permanent-on has the same exclusive ownership. */
    los_lamp = LED_FULL; sensor.value = 1;
    zx279133_pon_led_work(&eth.pon_led_work.work);
    CHECK(los_lamp == LED_FULL && light == 1);
    los.subscriber = &los_lamp;
    zx279133_pon_led_work(&eth.pon_led_work.work);
    CHECK(los_lamp == LED_OFF);
    sensor.value = 0;
    zx279133_pon_led_work(&eth.pon_led_work.work);
    CHECK(los_lamp == LED_FULL);
    previous = notes; sensor.value = -5;
    zx279133_pon_led_work(&eth.pon_led_work.work);
    CHECK(notes == previous && los_lamp == LED_FULL);
    eth.los_trigger = 0; sensor.value = 1;
    zx279133_pon_led_work(&eth.pon_led_work.work);
    CHECK(notes == previous + 1 && light == 1);
    previous = scheduled; eth.opsd_gpio = 0;
    zx279133_pon_led_work(&eth.pon_led_work.work);
    CHECK(scheduled == previous);

    zx_green = &pon; zx_wan = &wan; wan.carrier = 0;
    zx_state.state = ZXPON_O5; zxpon_apply_carrier();
    CHECK(wan.carrier == 1 && pon_lamp == LED_FULL);
    zx_state.state = 1; zxpon_apply_carrier();
    CHECK(wan.carrier == 0 && pon_lamp == LED_OFF);
    pon.subscriber = 0; pon_lamp = LED_OFF;
    for (i = 0; i < 200; ++i) {
        zx_state.state = i & 1 ? ZXPON_O5 : 1;
        zxpon_apply_carrier();
        CHECK(pon_lamp == LED_OFF && wan.carrier == (i & 1));
    }
    pon_lamp = LED_FULL; zx_state.state = 1; zxpon_apply_carrier();
    CHECK(pon_lamp == LED_FULL && wan.carrier == 0);
    pon.subscriber = &pon_lamp; zxpon_apply_carrier();
    CHECK(pon_lamp == LED_OFF);
    zx_green = 0; zx_state.state = ZXPON_O5; zxpon_apply_carrier();
    CHECK(wan.carrier == 1);
    zx279133_pon_led_cancel(&eth);
    CHECK(cancelled && unregistered);
    cancelled = 0; zx279133_pon_cleanup(&eth);
    CHECK(cancelled && stopped);
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc', default=shutil.which('cc') or shutil.which('gcc') or shutil.which('clang') or shutil.which('zig'))
    args = parser.parse_args()
    if not args.cc:
        parser.error('C compiler required; pass --cc (CI must not silently skip this test)')
    main_c = (DRIVER / 'zx279133-main.c').read_text(encoding='utf-8')
    pon_c = (DRIVER / 'zx279133-pon.c').read_text(encoding='utf-8')
    for source in (main_c, pon_c):
        assert 'led_set_brightness(' not in source, 'driver bypasses LED trigger ownership'
        assert 'devm_led_get(' not in source, 'driver still directly owns a lamp'
    assert 'led_trigger_register_simple("zx279133-los"' in main_c
    assert 'led_trigger_register_simple("zx279133-pon"' in pon_c
    assert 'devm_add_action_or_reset(dev, zx279133_pon_cleanup, eth)' in main_c
    assert pon_c.index('unregister_netdevice_notifier(&zxpon_net_nb)') < pon_c.index('led_trigger_unregister_simple(zx_green)')
    source = STUBS + '\n'.join([
        function(main_c, 'static void zx279133_pon_led_work('),
        function(main_c, 'static void zx279133_pon_led_cancel('),
        function(main_c, 'static void zx279133_pon_cleanup('),
        function(pon_c, 'static void zxpon_apply_carrier('),
    ]) + CASES
    with tempfile.TemporaryDirectory(prefix='sk-led-triggers-') as tmp:
        directory = Path(tmp)
        cfile, executable = directory / 'test.c', directory / 'test.exe'
        cfile.write_text(source, encoding='utf-8')
        command = [args.cc]
        if Path(args.cc).stem.lower() == 'zig':
            command.append('cc')
        command += ['-std=c11', '-O0', '-Wall', '-Wextra', '-Werror', str(cfile), '-o', str(executable)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, f'C assertion failed (line modulo 250 + 1): {result.returncode}\n{source}'
    print('PASS: actual driver functions: optical/O5 state, 400 manual-override updates, restore-auto, missing sensor/trigger and cleanup order')


if __name__ == '__main__':
    main()
