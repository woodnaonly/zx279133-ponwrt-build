#!/bin/sh
# Ask the vendor PON stack what it thinks the fibre state is, read-only.
#
# Every command below starts with 'p', i.e. print: zte_xgpon's /proc/gpon_sdk/debug
# write handler calls a show function whose output goes to the kernel log, and changes
# nothing. Do NOT add commands starting with r or c here - those delete live T-CONT and
# GEM-port entries - and do not use the multicast nodes under /proc/pon/debug, which
# dump thousands of lines and evict the boot trace from the small 4.19 log buffer.
#
# dmesg is read before and after each command so the answer is attributed to the command
# that produced it, and so a node that blocks cannot swallow the earlier results.
#
#	sh tools/ponprobe.sh | tee staging/ponprobe.log
n=$(dmesg | wc -l)
for c in pstate pploam pomci pstaticfec ppower pmic pirq ptimer pgemport ptcont; do
	printf '### %s\n' "$c"
	echo "$c" > /proc/gpon_sdk/debug 2>&1
	m=$(dmesg | wc -l)
	if [ "$m" -gt "$n" ]; then
		dmesg | tail -n $((m - n))
		n=$m
	else
		printf '(no new kernel log lines)\n'
	fi
done

# An unknown token is not an error: the handler runs off the end of its command table
# and prints the whole table. This is the authoritative vocabulary, from the driver.
printf '### help (via an unmatched token)\n'
echo zzz_not_a_command > /proc/gpon_sdk/debug 2>&1
m=$(dmesg | wc -l)
dmesg | tail -n $((m - n))
n=$m

printf '### state of the interfaces and devices\n'
for d in /sys/class/net/*; do
	printf '%s operstate=%s carrier=%s\n' "${d#/sys/class/net/}" \
		"$(cat $d/operstate 2>/dev/null)" "$(cat $d/carrier 2>/dev/null)"
done
grep -E 'gpon|optical|switch_dev' /proc/devices
ls -l /dev | grep -E 'gpon|optical|switch' || echo '(no PON /dev nodes)'
printf '### threads\n'
ps | grep -E 'gpondrv|pon' | grep -v grep
