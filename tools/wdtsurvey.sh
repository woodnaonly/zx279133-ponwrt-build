#!/bin/sh
# Read-only survey of the four zx_wdt register blocks + the CRM reset-mask register.
# Each block is sampled twice ~4 s apart so a decrementing/incrementing counter field
# can be told apart from a static control field. devmem2 on this unit prints the value
# as 64 bits, so only the low 32 hex digits are meaningful.
D=${1:-/tmp/wdt}
mkdir -p $D
for b in 14f09000 14f0a000 14f0b000 14f0c000; do
	for off in 000 004 008 00c 010 014 018 01c 020 024 028 02c 030 034 038 03c 040 044 048 04c 050 054 058 05c; do
		v=$(/sbin/devmem2 0x$b$off w 2>/dev/null | tail -1 | sed 's/.*: 0x//' | cut -c9-16)
		echo "$b +$off = $v"
	done
done
echo "-- pass 2 after 4 s"
sleep 4
for b in 14f09000 14f0a000 14f0b000 14f0c000; do
	for off in 000 004 008 00c 010 014 018 01c 020 024 028 02c 030 034 038 03c 040 044 048 04c 050 054 058 05c; do
		v=$(/sbin/devmem2 0x$b$off w 2>/dev/null | tail -1 | sed 's/.*: 0x//' | cut -c9-16)
		echo "$b +$off = $v"
	done
done
echo "-- crm around 0x10e10208"
for off in 200 204 208 20c 210; do
	echo "10e10$off = $(/sbin/devmem2 0x10e10$off w 2>/dev/null | tail -1 | sed 's/.*: 0x//' | cut -c9-16)"
done
echo "-- proc state"
cat /proc/watchdog/ctrl
