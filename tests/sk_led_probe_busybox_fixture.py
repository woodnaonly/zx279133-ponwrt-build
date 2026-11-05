#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Explicit SSH-only BusyBox smoke test of the real helper with fake hardware.

Every device/configuration path is replaced inside a private /tmp fixture.
There are no real debugfs requests, GPIO operations or persistent writes.
The fixture is retained for inspection until the router's next reboot.
"""
import argparse
import importlib.util
from pathlib import Path
import re
import shlex
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('sk_led_probe', ROOT / 'tools/diagnose-sk-leds.py')
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)

MDIO = r'''#!/bin/sh
set -eu
read -r kind phy devad reg value
[ "$phy" = 5 ] || exit 91
case "$devad/$reg" in
    0x1f/0xd032|0x1f/0xd040|0x1f/0xd044|0x1f/0xa434|0x1e/0x697a|0x1e/0x6a04|0x1e/0x6f10|0x1e/0x6f11|0x1e/0x7580) ;;
    *) exit 92 ;;
esac
root=$SK_LED_FIXTURE_ROOT
file=$root/registers/$devad-$reg
current=$(cat "$file")
case "$kind" in
    m45) ;;
    m45w)
        case "$devad/$reg" in 0x1f/0xd032|0x1f/0xd040|0x1f/0xd044) ;; *) exit 93 ;; esac
        printf 'WRITE %s %s %s %s\n' "$devad" "$reg" "$current" "$value" >> "$root/history"
        if [ -s "$root/fail_once" ] && [ "$(cat "$root/fail_once")" = "$reg" ]; then
            : > "$root/fail_once"
            exit 94
        fi
        if [ -s "$root/fail_restore" ] && [ "$(cat "$root/fail_restore")" = "$reg" ] &&
           [ "$value" = "$(cat "$root/originals/$devad-$reg")" ]; then exit 95; fi
        if [ -s "$root/mismatch_once" ] && [ "$(cat "$root/mismatch_once")" = "$reg" ]; then
            : > "$root/mismatch_once"
            value=$(printf '0x%04x' "$((value ^ 1))")
        fi
        printf '%s\n' "$value" > "$file"
        ;;
    *) exit 96 ;;
esac
[ ! -s "$root/drop_response" ] || exit 0
counter=$(cat "$root/counter")
counter=$((counter + 1))
printf '%s\n' "$counter" > "$root/counter"
printf '[%s] outer phy 5 devad %s reg %s = %s\n' "$counter" "$devad" "$reg" "$(cat "$file")" >> "$root/kernel.log"
'''

SETUP = r'''
mkdir -p "$root/commands" "$root/registers" "$root/originals" "$root/leds" "$root/usb" "$root/pinctrl"
printf 'skyworth,sk-d840n\n' > "$root/board"
printf '1\n' > "$root/carrier"
printf '1000\n' > "$root/speed"
printf "config led 'lan1'\n option trigger 'default-on'\n" > "$root/system"
printf 'fixture GPIO, no hardware\n' > "$root/gpio"
: > "$root/diag"
: > "$root/history"
: > "$root/kernel.log"
printf '0\n' > "$root/counter"
for pair in '0x1f-0xd032 0x00a7' '0x1f-0xd040 0x0001' '0x1f-0xd044 0x00a7' '0x1f-0xa434 0x002c' '0x1e-0x697a 0x0000' '0x1e-0x6a04 0x0503' '0x1e-0x6f10 0x086a' '0x1e-0x6f11 0x0021' '0x1e-0x7580 0x9416'; do
    set -- $pair
    printf '%s\n' "$2" > "$root/registers/$1"
    printf '%s\n' "$2" > "$root/originals/$1"
done
for name in lan1 lan2 lan3 lan4 usb1 phone1 power wan pon los; do
    color=green
    if [ "$name" = los ]; then color=red; fi
    led=$root/leds/skyworth:$color:$name
    mkdir -p "$led"
    printf '1\n' > "$led/brightness"
    printf 'none [default-on]\n' > "$led/trigger"
done
mkdir -p "$root/leds/skyworth:green:usb1/ports"
printf '1\n' > "$root/leds/skyworth:green:usb1/ports/usb1-port1"
printf '1\n' > "$root/leds/skyworth:green:usb1/ports/usb2-port1"
'''

CASES = r'''
export SK_LED_FIXTURE_ROOT=$root
PATH=$root/commands:$PATH
export PATH
session=0
new_session() {
    session=$((session + 1))
    state=$(printf '%s/sk-led-diag.%06d' "$root" "$session")
    mkdir "$state"
    sh "$root/probe.sh" "$state" snapshot > "$state/test.log" 2>&1
    sh "$root/probe.sh" "$state" arm-level-0 >> "$state/test.log" 2>&1
}
run() { sh "$root/probe.sh" "$state" "$1" >> "$state/test.log" 2>&1; }
assert_originals() (
    # Keep this loop's register out of the caller's injection case labels.
    for reg in 0xd032 0xd040 0xd044; do
        cmp "$root/registers/0x1f-$reg" "$root/originals/0x1f-$reg"
    done
)
restored() {
    run restore || { cat "$state/test.log"; exit 1; }
    assert_originals
    grep -q '^RESTORED_FIELDS .* verified=1$' "$state/test.log"
    # Allow this guard to observe cancellation and release its global flock.
    sleep 1
    sleep 1
}
for level in off on; do
    new_session
    run level-$level || { cat "$state/test.log"; exit 1; }
    [ "$(cat "$root/registers/0x1f-0xd032")" = 0x0080 ]
    [ "$(cat "$root/registers/0x1f-0xd040")" = 0x0000 ]
    [ ! -f "$state/restored" ]
    restored
    printf 'PASS BusyBox held %s and three-word restoration\n' "$level"
done
for injection in fail_once mismatch_once; do
    for reg in 0xd032 0xd040 0xd044; do
        new_session
        printf '%s\n' "$reg" > "$root/$injection"
        if run level-off; then printf 'ERROR injection did not fail\n'; exit 1; fi
        [ ! -f "$state/cancelled" ]
        restored
        printf 'PASS BusyBox %s %s partial-write recovery\n' "$injection" "$reg"
    done
done
for reg in 0xd032 0xd040 0xd044; do
    new_session
    run level-off
    printf '%s\n' "$reg" > "$root/fail_restore"
    if run restore; then printf 'ERROR incomplete recovery accepted\n'; exit 1; fi
    [ ! -f "$state/cancelled" ] && [ ! -f "$state/restored" ]
    for other in 0xd032 0xd040 0xd044; do
        [ "$other" = "$reg" ] || cmp "$root/registers/0x1f-$other" "$root/originals/0x1f-$other"
    done
    : > "$root/fail_restore"
    restored
    printf 'PASS BusyBox failed restore %s still recovers other fields and retries\n' "$reg"
done
new_session
run level-off
printf '0\n' > "$root/carrier"
tries=0
while [ ! -f "$state/guard.done" ]; do
    sleep 1
    tries=$((tries + 1))
    [ "$tries" -lt 5 ] || { cat "$state/guard.log"; exit 1; }
done
grep -q '^GUARD_RESTORED verified=1$' "$state/guard.done"
assert_originals
printf 'PASS BusyBox independent guard early link-change recovery\n'
printf 'FIXTURE_COMPLETE no real hardware accessed; retained %s\n' "$root"
'''


def payload(helper, root):
    if not re.fullmatch(r'/tmp/sk-led-mock\.[A-Za-z0-9]{6}', root):
        raise ValueError('Unsafe mock fixture directory')
    source = helper.read_text(encoding='utf-8')
    if source.count('> "$diag"') != 2:
        raise ValueError('Hardware substitutions must match exactly two writes')
    source = source.replace('> "$diag"', '| sk-led-fixture-io')
    replacements = {
        '/tmp/sk-led-diag.': root + '/sk-led-diag.',
        '/tmp/sk-led-phy-probe-lock': root + '/global-lock',
        '/tmp/sysinfo/board_name': root + '/board',
        '/sys/class/net/lan1/carrier': root + '/carrier',
        '/sys/class/net/lan1/speed': root + '/speed',
        '/etc/config/system': root + '/system',
        '/sys/class/leds': root + '/leds',
        '/sys/bus/usb/devices': root + '/usb',
        '/sys/kernel/debug/gpio': root + '/gpio',
        '/sys/kernel/debug/pinctrl': root + '/pinctrl',
        '/sys/kernel/debug/zx279133-9132': root + '/diag',
    }
    for original, replacement in replacements.items():
        if original not in source:
            raise ValueError('Missing fixture substitution: ' + original)
        source = source.replace(original, replacement)
    if '/sys/' in source or '/etc/' in source or '> "$diag"' in source:
        raise ValueError('Mock helper still contains a production hardware/configuration path')
    files = {
        'probe.sh': source,
        'commands/sk-led-fixture-io': MDIO,
        'commands/dmesg': '#!/bin/sh\ncat "$SK_LED_FIXTURE_ROOT/kernel.log"\n',
    }
    script = 'set -eu\numask 077\nroot=' + shlex.quote(root) + '\n' + SETUP
    for name, content in files.items():
        script += 'printf %s ' + shlex.quote(content) + ' > ' + shlex.quote(root + '/' + name) + '\n'
    script += 'chmod 700 "$root/commands/sk-led-fixture-io" "$root/commands/dmesg"\n'
    return script + CASES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-on-router', action='store_true', required=True)
    parser.add_argument('--host', default='192.168.9.1')
    parser.add_argument('--bind', default='192.168.9.25')
    parser.add_argument('--identity', type=Path, default=Path.home() / '.ssh/id_onu')
    parser.add_argument('--ssh', default='ssh')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9.:-]+', args.host) or args.host.startswith('-'):
        parser.error('Invalid host')
    transport = PROBE.SSHTransport(args, lambda message: print(message, flush=True))
    root = transport.call('umask 077; mktemp -d /tmp/sk-led-mock.XXXXXX').strip()
    data = payload(PROBE.HELPER, root).encode('utf-8')
    print('ISOLATED_BUSYBOX_FIXTURE ' + root + ' (not a physical LED test)', flush=True)
    # Output streams continuously; the calling terminal can yield between cases.
    result = subprocess.run(transport.ssh + ['sh -s'], input=data, timeout=90)
    return result.returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except PROBE.ProbeError as error:
        print('FIXTURE NOT RUN/COMPLETE: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
