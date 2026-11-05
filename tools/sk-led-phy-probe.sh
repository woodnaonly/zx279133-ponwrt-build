#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Internal device-side helper for diagnose-sk-leds.py, not an installed LED API.
# Writes stay on PHY 5/MMD 1f. The legacy experiments only touch d044;
# explicitly armed channel-0 level tests additionally allow LED masks in
# d032 (0027) and d040 (0001), with all three original words restored.
set -eu
umask 077
state=${1:-}
action=${2:-snapshot}
# Ordinary experiments retain the 30-second deadline. Only an explicit held
# single-channel observation below can select the fixed 60-second deadline.
deadline=30
case "$state" in
    /tmp/sk-led-diag.[A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9]) ;;
    *) printf 'ERROR invalid private state directory\n' >&2; exit 1 ;;
esac
[ -d "$state" ] && [ ! -L "$state" ] || exit 1
diag=/sys/kernel/debug/zx279133-9132
global_dir=/tmp/sk-led-phy-probe-lock
global_lock=$global_dir/active.lock

fail() { printf 'ERROR %s\n' "$*" >&2; exit 1; }
ensure_global_lock() {
    # Shared /tmp names must not allow a symlink to turn a lock open into a
    # configuration-file truncation. Only trust our root-owned private directory.
    if [ ! -e "$global_dir" ] && [ ! -L "$global_dir" ]; then
        mkdir -m 700 "$global_dir" || fail 'cannot create private lock directory'
    fi
    [ -d "$global_dir" ] && [ ! -L "$global_dir" ] || fail 'unsafe global lock directory'
    # The target's minimal BusyBox has no stat applet; numeric ls is available.
    ls -ldn "$global_dir" | awk '$1 == "drwx------" && $3 == 0 {ok=1} END {exit !ok}' ||
        fail 'global lock directory is not root-private'
    [ ! -L "$global_lock" ] || fail 'refusing symlink global lock'
    if [ -e "$global_lock" ]; then
        [ -f "$global_lock" ] || fail 'global lock is not a regular file'
    fi
}
board() {
    [ "$(cat /tmp/sysinfo/board_name)" = 'skyworth,sk-d840n' ] || fail 'wrong board'
    [ -r "$diag" ] && [ -w "$diag" ] || fail 'existing Clause-45 diagnostic interface unavailable'
    command -v flock >/dev/null || fail 'flock required; no experiment without automatic lock release'
}

level_mode() {
    [ -f "$state/experiment" ] && [ "$(cat "$state/experiment")" = level-0 ]
}

level_mask() {
    case "$1" in
        0xd032) printf '0x0027\n' ;;
        0xd040|0xd044) printf '0x0001\n' ;;
        *) fail 'unknown channel-0 LED field' ;;
    esac
}

saved_field() {
    case "$1" in
        0xd032|0xd040) cat "$state/original.${1#0x}" ;;
        0xd044) cat "$state/original" ;;
        *) fail 'unknown saved LED field' ;;
    esac
}

# Call under fd 9's flock. Reject a previous kernel-log response even when its
# value happens to match. Never clear dmesg or interpret a write's exit status as
# the value read back. The driver's write handler propagates MDIO errors.
exchange() {
    kind=$1 devad=$2 reg=$3
    needle="outer phy 5 devad $devad reg $reg = "
    before=$(dmesg | grep -F "$needle" | tail -n 1) || before=''
    if [ "$kind" = read ]; then
        printf 'm45 5 %s %s\n' "$devad" "$reg" > "$diag" || fail 'Clause-45 read failed'
    elif [ "$kind" = write ]; then
        [ ! -r /sys/class/leds/skyworth:green:lan1/backend ] ||
            fail 'formal LAN1 LED backend owns the PHY fields; use standard LED controls'
        value=$4
        case "$value" in 0x[0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; *) fail 'invalid saved LED word' ;; esac
        case "$devad/$reg" in
            0x1f/0xd044) ;;
            0x1f/0xd032|0x1f/0xd040)
                level_mode || fail 'extra LED fields require explicit level-mode arming'
                ;;
            *) fail 'refusing non-LED register write' ;;
        esac
        if level_mode; then
            write_mask=$(level_mask "$reg")
            current_word=$(exchange read "$devad" "$reg") || return 1
            [ "$(((current_word ^ value) & (65535 ^ write_mask)))" = 0 ] ||
                fail 'refusing change outside the authorized LED field'
        fi
        printf 'm45w 5 %s %s %s\n' "$devad" "$reg" "$value" > "$diag" || fail 'LED register write/readback failed'
    else
        fail 'refusing non-LED register write'
    fi
    after=$(dmesg | grep -F "$needle" | tail -n 1) || after=''
    [ -n "$after" ] && [ "$after" != "$before" ] || fail 'missing fresh MDIO response'
    parsed=$(printf '%s\n' "$after" | sed -n 's/^.* = \(0x[0-9a-f][0-9a-f][0-9a-f][0-9a-f]\)$/\1/p')
    case "$parsed" in ''|0xffff|0xdead) fail 'unreliable MDIO response' ;; esac
    printf '%s\n' "$parsed"
}

network_ok() {
    [ "$(cat /sys/class/net/lan1/carrier)" = "$(cat "$state/carrier")" ] &&
    [ "$(cat /sys/class/net/lan1/speed)" = "$(cat "$state/speed")" ] || fail 'LAN1 carrier/speed changed'
}

configuration_ok() {
    current=$(sha256sum /etc/config/system | awk '{print $1}')
    [ "$current" = "$(cat "$state/config.sha256")" ] || fail 'persistent LED configuration changed'
}

stable_ok() {
    network_ok
    configuration_ok
    while read -r devad reg expected; do
        if level_mode; then
            case "$devad/$reg" in
                0x1f/0xd032|0x1f/0xd040) expected=$(cat "$state/last.${reg#0x}") ;;
            esac
        fi
        actual=$(exchange read "$devad" "$reg") || return 1
        [ "$actual" = "$expected" ] || fail "non-LED sentinel changed: $devad/$reg"
    done < "$state/sentinels"
}

led_snapshot() {
    for led in /sys/class/leds/*; do
        [ -d "$led" ] || continue
        printf 'LED %s brightness=%s trigger=%s\n' "${led##*/}" \
            "$(cat "$led/brightness")" \
            "$(sed -n 's/.*\[\([^]]*\)\].*/\1/p' "$led/trigger")"
    done
}

usb_snapshot() {
    usb_led=/sys/class/leds/skyworth:green:usb1
    printf 'USB_OBSERVATION software/topology only; insertion and physical lamp NOT VERIFIED\n'
    for port in "$usb_led"/ports/*; do
        [ -f "$port" ] || continue
        printf 'USB_MONITOR port=%s enabled=%s\n' "${port##*/}" "$(cat "$port")"
    done
    for hub in /sys/bus/usb/devices/usb[0-9]*; do
        [ -d "$hub" ] || continue
        printf 'USB_ROOT name=%s speed=%s product=%s path=%s\n' "${hub##*/}" \
            "$(cat "$hub/speed")" "$(cat "$hub/product")" "$(readlink -f "$hub")"
    done
    for device in /sys/bus/usb/devices/[0-9]*-[0-9]*; do
        # Interface/port nodes have no idVendor. No serial numbers are collected.
        [ -f "$device/idVendor" ] && [ -f "$device/idProduct" ] || continue
        printf 'USB_DEVICE name=%s speed=%s vendor=%s product=%s\n' "${device##*/}" \
            "$(cat "$device/speed")" "$(cat "$device/idVendor")" "$(cat "$device/idProduct")"
    done
}

# All three complete words must match before reporting restoration. A failure
# on one field must not prevent recovery of the remaining fields. Preserve any
# concurrent changes outside our masks, but then refuse a full-restore claim.
verify_level_fields() {
    for verify_reg in 0xd032 0xd040 0xd044; do
        verify_saved=$(saved_field "$verify_reg") || return 1
        verify_actual=$(exchange read 0x1f "$verify_reg") || return 1
        [ "$verify_actual" = "$verify_saved" ] || return 1
    done
    printf 'RESTORED_FIELDS d032=%s d040=%s d044=%s verified=1\n' \
        "$(saved_field 0xd032)" "$(saved_field 0xd040)" "$(saved_field 0xd044)"
}

restore_level_fields() {
    restore_failed=0
    for restore_reg in 0xd044 0xd040 0xd032; do
        restore_saved=$(saved_field "$restore_reg") || { restore_failed=1; continue; }
        restore_mask=$(level_mask "$restore_reg") || { restore_failed=1; continue; }
        restore_current=$(exchange read 0x1f "$restore_reg") || { restore_failed=1; continue; }
        restore_wanted=$(printf '0x%04x' "$(((restore_current & (65535 ^ restore_mask)) | (restore_saved & restore_mask)))")
        if [ -f "$state/dirty" ] && [ "$restore_current" != "$restore_wanted" ]; then
            restore_actual=$(exchange write 0x1f "$restore_reg" "$restore_wanted") || { restore_failed=1; continue; }
            [ "$restore_actual" = "$restore_wanted" ] || { restore_failed=1; continue; }
        fi
        restore_actual=$(exchange read 0x1f "$restore_reg") || { restore_failed=1; continue; }
        if [ "$restore_actual" != "$restore_saved" ]; then
            printf 'ERROR full original word not restored: %s\n' "$restore_reg" >&2
            restore_failed=1
            continue
        fi
        printf '%s\n' "$restore_actual" > "$state/last.${restore_reg#0x}"
    done
    [ "$restore_failed" = 0 ] || return 1
    verify_level_fields || return 1
}

# Called with the IO lock held, by the host or the independent guard. An error
# leaves cancellation absent so that the guard still has its recovery attempt.
restore_word() {
    saved=$(cat "$state/original") || return 1
    if level_mode; then
        restore_level_fields || return 1
        actual=$(exchange read 0x1f 0xd044) || return 1
    elif [ -f "$state/dirty" ]; then
        actual=$(exchange write 0x1f 0xd044 "$saved") || return 1
    else
        actual=$(exchange read 0x1f 0xd044) || return 1
    fi
    [ "$actual" = "$saved" ] || return 1
    printf '%s\n' "$actual" > "$state/restored"
    printf 'RESTORED value=%s verified=1\n' "$actual"
}

guard() {
    # The fork must reopen these descriptors: inherited open-file descriptions
    # would share a flock and would not exclude foreground SSH operations.
    exec 8>>"$global_lock"
    flock -n 8 || { printf 'another PHY experiment is active\n' > "$state/guard.failed"; exit 1; }
    exec 9>&-
    exec 9>"$state/io.lock"
    printf 'ready\n' > "$state/guard.ready"
    elapsed=0
    while [ "$elapsed" -lt "$deadline" ]; do
        [ ! -f "$state/cancelled" ] || { printf 'GUARD_CANCELLED\n'; exit 0; }
        sleep 1
        elapsed=$((elapsed + 1))
        # A held observation must recover even after its host exits. Run the
        # failure-exiting check in a subshell so it cannot kill this guard
        # before restoration. Ordinary 30s phases retain host link checks.
        if [ "$deadline" = 60 ] && ! (network_ok); then
            printf 'GUARD_LINK_CHANGED restoring early\n'
            break
        fi
    done
    # Set before waiting for the IO lock. Any later host phase is refused under
    # that same lock, so it cannot overwrite a completed deadline restoration.
    printf 'deadline\n' > "$state/expired"
    flock -x 9
    if [ ! -f "$state/cancelled" ]; then
        if restore_word; then
            printf 'GUARD_RESTORED verified=1\n' > "$state/guard.done"
        else
            printf 'GUARD_RESTORE_FAILED\n' > "$state/guard.done"
            exit 1
        fi
    fi
}

board
if [ "$action" != snapshot ] && [ -r /sys/class/leds/skyworth:green:lan1/backend ]; then
    fail 'formal LAN1 LED backend owns the PHY fields; use standard LED controls'
fi
exec 9>"$state/io.lock"
flock -x 9
case "$action" in
snapshot)
    [ ! -f "$state/guard.ready" ] || fail 'cannot replace an armed baseline'
    printf 'BOARD skyworth,sk-d840n kernel=%s uptime=%s\n' "$(uname -r)" "$(cat /proc/uptime)"
    sha256sum /etc/config/system | awk '{print $1}' > "$state/config.sha256"
    cat /sys/class/net/lan1/carrier > "$state/carrier"
    cat /sys/class/net/lan1/speed > "$state/speed"
    printf 'BASELINE carrier=%s speed=%s config_sha256=%s\n' \
        "$(cat "$state/carrier")" "$(cat "$state/speed")" "$(cat "$state/config.sha256")"
    led_snapshot > "$state/leds.before"
    cat "$state/leds.before"
    usb_snapshot
    cat /sys/kernel/debug/gpio
    for path in /sys/kernel/debug/pinctrl/*; do
        [ -r "$path/pinmux-pins" ] || continue
        grep -E '^pin (21|62|63|64|65) ' "$path/pinmux-pins" || true
    done
    printf 'USB_DECLARATION GPIO1_5 pin21/p1-6 expected_mux=3; actual mux UNVERIFIED\n'
    printf 'MUX_READBACK_UNAVAILABLE ownership is not a hardware mux read\n'
    : > "$state/sentinels"
    for pair in '0x1f 0xd032' '0x1f 0xd040' '0x1e 0x697a' '0x1e 0x6a04' '0x1e 0x6f10' '0x1e 0x6f11' '0x1e 0x7580'; do
        set -- $pair
        result=$(exchange read "$1" "$2") || exit 1
        printf '%s %s %s\n' "$1" "$2" "$result" >> "$state/sentinels"
        case "$1/$2" in
            0x1f/0xd032|0x1f/0xd040)
                printf '%s\n' "$result" > "$state/original.${2#0x}"
                printf '%s\n' "$result" > "$state/last.${2#0x}"
                ;;
        esac
        printf 'REGISTER devad=%s reg=%s value=%s\n' "$1" "$2" "$result"
    done
    result=$(exchange read 0x1f 0xa434) || exit 1
    printf 'PHYSR value=%s\n' "$result"
    result=$(exchange read 0x1f 0xd044) || exit 1
    printf '%s\n' "$result" > "$state/original"
    printf '%s\n' "$result" > "$state/last"
    printf '%s\n' "$result" > "$state/last.d044"
    printf 'ORIGINAL_LED_WORD %s\n' "$result"
    printf 'SNAPSHOT verified=1\n'
    ;;
arm|arm-hold-[012]|arm-level-0)
    [ -f "$state/original" ] && [ -s "$state/sentinels" ] || fail 'no verified baseline'
    [ ! -f "$state/guard.ready" ] || fail 'guard already armed'
    [ "$(cat "$state/carrier")" = 1 ] || fail 'LAN1 must already have carrier; no network reconfiguration permitted'
    ensure_global_lock
    stable_ok
    [ "$(exchange read 0x1f 0xd044)" = "$(cat "$state/original")" ] || fail 'LED word changed since baseline'
    if [ "$action" = arm-level-0 ]; then
        channel=0
        deadline=60
        [ -s "$state/original.d032" ] && [ -s "$state/original.d040" ] || fail 'missing LED event baseline'
        printf 'level-0\n' > "$state/experiment"
    elif [ "$action" != arm ]; then
        channel=${action#arm-hold-}
        deadline=60
        # Pin this longer observation to one candidate bit; reject factory
        # writes and other channels even if invoked directly on the helper.
        printf 'polarity-%s\n' "$channel" > "$state/experiment"
    fi
    printf '%s\n' "$deadline" > "$state/guard.deadline"
    # No PTY, no inherited SSH standard streams and HUP ignored. This process
    # continues to hold the global experiment lock if the client disappears.
    (trap '' HUP; guard) </dev/null > "$state/guard.log" 2>&1 &
    printf '%s\n' "$!" > "$state/guard.pid"
    flock -u 9
    tries=0
    while [ ! -f "$state/guard.ready" ]; do
        [ ! -f "$state/guard.failed" ] || fail 'another PHY experiment is active'
        tries=$((tries + 1))
        [ "$tries" -le 3 ] || fail 'guard startup not confirmed'
        sleep 1
    done
    if [ "$action" = arm ]; then
        printf 'ARMED deadline=30 verified=1\n'
    elif [ "$action" = arm-level-0 ]; then
        printf 'ARMED deadline=60 channel=0 fields=d032,d040,d044 verified=1\n'
    else
        printf 'ARMED deadline=60 channel=%s verified=1\n' "$channel"
    fi
    ;;
level-on|level-off)
    level_mode || fail 'channel-0 level test is not explicitly armed'
    [ -f "$state/guard.ready" ] && [ ! -f "$state/cancelled" ] && [ ! -f "$state/expired" ] || fail 'guard not armed or deadline passed'
    kill -0 "$(cat "$state/guard.pid")" || fail 'guard process is not alive'
    # A held session is restricted to one level, never a later surprise toggle.
    [ ! -f "$state/level.requested" ] || fail 'held level already applied'
    printf '%s\n' "$action" > "$state/level.requested"
    stable_ok
    for level_reg in 0xd032 0xd040 0xd044; do
        level_current=$(exchange read 0x1f "$level_reg") || exit 1
        [ "$level_current" = "$(cat "$state/last.${level_reg#0x}")" ] || fail 'unexpected external LED field change'
    done
    # Disable only this channel's known link/activity events before polarity.
    for level_reg in 0xd032 0xd040 0xd044; do
        network_ok
        [ ! -f "$state/expired" ] && [ ! -f "$state/cancelled" ] || fail 'guard deadline passed before field write'
        level_mask_value=$(level_mask "$level_reg")
        level_current=$(exchange read 0x1f "$level_reg") || exit 1
        [ "$level_current" = "$(cat "$state/last.${level_reg#0x}")" ] || fail 'LED field changed before write'
        level_bits=0
        if [ "$level_reg" = 0xd044 ] && [ "$action" = level-on ]; then level_bits=1; fi
        level_wanted=$(printf '0x%04x' "$(((level_current & (65535 ^ level_mask_value)) | level_bits))")
        printf 'dirty\n' > "$state/dirty"
        if [ "$level_wanted" != "$level_current" ]; then
            level_actual=$(exchange write 0x1f "$level_reg" "$level_wanted") || exit 1
            [ "$level_actual" = "$level_wanted" ] || fail 'LED field write readback mismatch'
        fi
        level_actual=$(exchange read 0x1f "$level_reg") || exit 1
        [ "$level_actual" = "$level_wanted" ] || fail 'independent LED field readback mismatch'
        printf '%s\n' "$level_actual" > "$state/last.${level_reg#0x}"
    done
    stable_ok
    [ ! -f "$state/expired" ] || fail 'guard deadline passed after field writes'
    phase=OFF
    if [ "$action" = level-on ]; then phase=ON; fi
    printf 'LEVEL_CANDIDATE channel=0 level=%s d032=%s d040=%s d044=%s verified=1\n' \
        "$phase" "$(cat "$state/last.d032")" "$(cat "$state/last.d040")" "$(cat "$state/last.d044")"
    ;;
off|on|polarity-[012]-baseline|polarity-[012]-inverted)
    [ -f "$state/guard.ready" ] && [ ! -f "$state/cancelled" ] && [ ! -f "$state/expired" ] || fail 'guard not armed or deadline passed'
    kill -0 "$(cat "$state/guard.pid")" || fail 'guard process is not alive'
    stable_ok
    [ "$(exchange read 0x1f 0xd044)" = "$(cat "$state/last")" ] || fail 'unexpected external LED register change'
    case "$action" in
        off) experiment=factory; wanted=0x0000; phase=OFF ;;
        on) experiment=factory; wanted=0x00ff; phase=ON ;;
        polarity-*)
            # Candidate per-channel polarity bits from upstream RTL8221B code,
            # NOT a confirmed force-brightness mode on the RTL8226B here. Keep
            # every other bit, including vendor enable bits [6:4], untouched.
            channel=${action#polarity-}; channel=${channel%%-*}
            experiment=polarity-$channel
            mask=$((1 << channel))
            saved=$(cat "$state/original")
            case "$action" in
                *-baseline) wanted=$saved; phase=BASELINE ;;
                *-inverted) wanted=$(printf '0x%04x' "$((saved ^ mask))"); phase=INVERTED ;;
            esac
            ;;
    esac
    if [ -f "$state/experiment" ]; then
        [ "$(cat "$state/experiment")" = "$experiment" ] || fail 'cannot mix experiment fields in one session'
    else
        printf '%s\n' "$experiment" > "$state/experiment"
    fi
    # The deadline can expire while the MDIO/sentinel reads are in progress.
    [ ! -f "$state/expired" ] && [ ! -f "$state/cancelled" ] || fail 'guard deadline passed before write'
    printf 'dirty\n' > "$state/dirty"
    actual=$(exchange write 0x1f 0xd044 "$wanted") || exit 1
    [ "$actual" = "$wanted" ] || fail 'LED write readback mismatch'
    printf '%s\n' "$actual" > "$state/last"
    flock -u 9
    count=0
    # The stock image's minimal BusyBox sleep accepts integer seconds only.
    # Check the link after each second while retaining the two-second hold.
    while [ "$count" -lt 2 ]; do
        sleep 1
        network_ok
        [ ! -f "$state/expired" ] || fail 'guard deadline passed'
        count=$((count + 1))
    done
    case "$experiment" in
        factory) printf '%s value=%s verified=1\n' "$phase" "$actual" ;;
        polarity-*)
            printf 'POLARITY channel=%s phase=%s mask=0x%04x value=%s verified=1\n' \
                "$channel" "$phase" "$mask" "$actual"
            ;;
    esac
    ;;
restore)
    if [ ! -f "$state/guard.ready" ]; then
        [ ! -f "$state/dirty" ] || fail 'dirty state without a guard; recovery requires inspection'
        printf 'NOT_ARMED verified=1\n'
        exit 0
    fi
    if [ -f "$state/expired" ] || [ -f "$state/cancelled" ] ||
       ! kill -0 "$(cat "$state/guard.pid")" 2>/dev/null; then
        # A late client must never overwrite a newer experiment. Release our
        # per-session lock before taking the global lock (the guard uses the
        # opposite nesting), and only read an already-restored historical word.
        flock -u 9
        ensure_global_lock
        exec 8>>"$global_lock"
        tries=0
        until flock -n 8; do
            tries=$((tries + 1))
            [ "$tries" -lt 3 ] || fail 'late recovery cannot own global lock; no write performed'
            sleep 1
        done
        flock -x 9
        if [ -f "$state/restored" ]; then
            saved=$(cat "$state/original")
            [ "$(cat "$state/restored")" = "$saved" ] || fail 'invalid restoration record'
            [ "$(exchange read 0x1f 0xd044)" = "$saved" ] || fail 'historical restoration no longer matches; refusing stale write'
            if level_mode; then
                verify_level_fields || fail 'historical LED fields no longer match; refusing stale write'
            fi
            printf 'RESTORED value=%s verified=1\n' "$saved"
            printf 'RESTORE_SOURCE historical record plus fresh read; no repeated write\n'
        else
            restore_word || fail 'exclusive recovery retry failed'
        fi
    else
        restore_word || fail 'restoration readback failed; guard remains armed'
    fi
    # Cancel only after the full saved register word is confirmed restored.
    printf 'restoration verified\n' > "$state/cancelled"
    stable_ok
    led_snapshot > "$state/leds.after"
    cmp "$state/leds.before" "$state/leds.after" || fail 'another LED software state changed'
    cat "$state/leds.after"
    printf 'CONFIG_AND_SENTINELS_UNCHANGED verified=1\n'
    ;;
*) fail 'unsupported action' ;;
esac
