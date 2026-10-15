#!/bin/sh
# Read-only survey of the PON side of the running system. Nothing here writes to
# flash or changes configuration; it only lists what the vendor stack consists of.
K=/lib/modules/4.19.136+
echo "=== 1. modules present ($(ls $K | wc -l) files) ==="
ls -l $K | awk '{printf "%9s %s\n", $5, $9}' | sort -k2 | head -60

echo
echo "=== 2. modinfo of the PON-ish ones ==="
for m in np_133 gpondrv_xg zte_xgpon zx_ponreg_133 wan_bridge ffe switch netdriver GN28L95 rlt8226b; do
	[ -f "$K/$m.ko" ] || continue
	echo "-- $m.ko ($(wc -c <"$K/$m.ko") bytes)"
	grep -a -E '^(description|author|license|depends|vermagic)=' "$K/$m.ko" 2>/dev/null | head -8
done

echo
echo "=== 3. loaded now (name size used-by) ==="
lsmod | head -40

echo
echo "=== 4. char/misc devices the stack creates ==="
grep -iE 'pon|gpon|epon|xg|omci|mpcp|ethdrv|ffe|switch|pce|dsl|mec' /proc/devices || echo "(none matched)"
ls -l /dev | grep -iE 'pon|gpon|epon|xg|omci|mpcp|ethdrv|ffe|switch|mec|slc|voip' || echo "(no such /dev nodes)"

echo
echo "=== 5. device tree nodes for the PON hardware ==="
cd /sys/firmware/devicetree/base 2>/dev/null || exit 0
for n in $(find . -maxdepth 3 -type d -name '*pon*' -o -maxdepth 3 -type d -name '*serdes*' -o -maxdepth 3 -type d -name '*xdrail*' 2>/dev/null | head -20); do
	echo "-- $n"
	for p in "$n"/*; do
		[ -f "$p" ] || continue
		v=$(hexdump -v -e '4/1 "%02x" " "' "$p" 2>/dev/null | head -c 90)
		echo "     ${p##*/} = $v"
	done
done

echo
echo "=== 6. how it is started ==="
ls /etc/init.d | grep -iE 'pon|gpon|epon|omci|wan|net|dcm|voip|switch' | tr '\n' ' '
echo
ls /etc/rc.d | grep -iE 'S[0-9].*(pon|wan|net|switch|dcm)' | tr '\n' ' '
echo
cat /etc/hotplug.d/net/* 2>/dev/null | grep -iE 'pon|ethdriver|ioctl' | head -6
echo "-- processes:"
ps w 2>/dev/null | grep -iE 'pon|omci|mpcp|dcm|switch|eth' | grep -v grep | head -12

echo
echo "=== 7. OMCI / MIB userspace ==="
find /etc /usr /lib /bin /sbin /tmp -maxdepth 4 \( -iname '*omci*' -o -iname '*me_*.bin' -o -iname '*.mib*' -o -iname '*mpcp*' \) 2>/dev/null | head -20
echo "-- modules-load.d / autoload:"
cat /etc/modules-load.d/* 2>/dev/null | head; ls /etc/modules.d/ 2>/dev/null | tr '\n' ' '
