#!/bin/sh
# One command from "the board just came up" to "the flash is written", because a window is
# minutes long and the order matters: wait, pre-flight, listen, arm, judge, then - only if a
# round booted these exact bytes - write.
#
#	sh tools/runround.sh                      # wait up to 30 min, gate round, stop and report
#	WAIT=120 FLASH=1 sh tools/runround.sh     # ... and flash when the gate permits it
#
# Nothing here can write flash without the permit, and the permit is only written by
# tools/gatewatch.sh scoring a live CTLALIVE report: this round's tag, a 6.x kver, root listed
# as mtd5 by the booting kernel itself, the head of root hashing the same bytes the vendor 4.19
# reads, and that kernel mounting the installed jffs2 read-only. FLASH defaults to 0 on
# purpose: an unexpected window - the board rebooting while someone is working at it - should
# not be enough to program an irreversible write. A permitted round parks instead of exiting:
# it waits for staging/ctl/GO, so a short window survives the round trip of asking, and the
# write still begins only because someone typed something.
#
# The board address is 192.168.9.1 for both identities that can answer there: the installed
# OpenWrt, and the announce payload, which gives every netdev it can find the test /24.
#
# 192.168.1.1 belongs in this list because it is what our own flashed root answers on, and
# missing it turns a working board into "never came back". The chain: immortalwrt's dropbear
# package ships `option Interface 'lan'` by default, and ci-build-repo's 02_network puts eth0
# alone in lan; config_generate then gives lan the stock default 192.168.1.1
# (base-files/files/bin/config_generate, "lan) ipad=${ipaddr:-192.168.1.1}"). Nothing in the
# build overrides it, so a freshly flashed board answers on .1.1 and not on .9.x.
#
# It is listed last and is NOT a trigger on its own - the probe below is an ssh that must
# return a uname line, and this PC sits on 192.168.1.0/24 twice (wired and WLAN) with the
# owner's own router answering 192.168.1.1. tools/backwatch.sh spells this out as well.
set -u
cd "$(dirname "$0")/.."
WAIT=${WAIT:-30}
FLASH=${FLASH:-0}
CTLTAG=${CTLTAG:-K5}
HOSTS=${HOSTS:-"192.168.9.1 192.168.9.2 192.168.1.1"}
DEV=${DEV:-}
KEY=${KEY:-$HOME/.ssh/id_onu}
PORT=${PORT:-5555}
RUN=${RUN:-38243906799}
LOGF=${LOGF:-staging/ctl/reports.log}
# 1 keeps the board from resetting itself, so only for a round whose silence must be
# distinguished from a hang - see tools/ctl-exec.sh for what the bit does.
MASK=${MASK:-0}
# How the round waits for a human word after a successful gate, and where that word goes.
GOFILE=${GOFILE:-staging/ctl/GO}
GO_WAIT=${GO_WAIT:-30}
probe() { timeout 8 ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i "$KEY" \
	-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o ConnectTimeout=3 "root@$1" \
	'uname -r; cat /proc/uptime | cut -d. -f1' 2>/dev/null | head -1; }
say() { echo "$(date '+%T') $*"; }

# Both addresses are probed, because two different systems can be what answers: the installed
# OpenWrt is reachable at 192.168.9.1 on its lan bridge, while the same box configured eth0
# statically at 192.168.9.2 - and after a flash only eth0's jack can reply at all. Waiting on
# one of them would read a live board as a missed window.
if [ -z "$DEV" ]; then
	say "waiting up to ${WAIT} min for the board on $HOSTS"
	end=$(( $(date +%s) + WAIT * 60 ))
	while [ "$(date +%s)" -lt "$end" ]; do
		for h in $HOSTS; do
			if [ -n "$(probe "$h")" ]; then DEV="root@$h"; export DEV; say "the board answered on $h: $(probe "$h")"; break; fi
		done
		[ -n "$DEV" ] && break
		sleep 15
	done
fi
[ -n "$DEV" ] || { say "no window within ${WAIT} min - nothing was staged, armed or written"; exit 1; }
SSH="ssh -q -n -o BatchMode=yes -o IdentitiesOnly=yes -i $KEY -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o ConnectTimeout=4 $DEV"

say "pre-flight (read-only; stops at the first problem)"
RUN="$RUN" sh tools/preflight.sh || { say "pre-flight failed - not arming anything"; exit 1; }

# The sink has to be listening before the payload boots, or the round is scored as silence.
# Asked whether the port answers rather than parsing netstat, whose column order differs here.
if python -c "import socket,sys
try:
    socket.create_connection(('127.0.0.1', $PORT), timeout=2).close()
except OSError:
    sys.exit(1)"; then
	say "a sink is already answering on $PORT"
else
	say "starting the report sink on $PORT"
	python tools/ncsrv.py "$PORT" "$LOGF" >>staging/ctl/ncsrv.log 2>&1 &
	sleep 2
fi

say "staging the gate payload and loading it"
CTLTAG="$CTLTAG" IMAGE_LOCAL=staging/ctl/gate-Image REMOTE_IMAGE=gate-Image \
	DTB_LOCAL=staging/ctl/gate-dtb REMOTE_DTB=gate-dtb INITRD=ctl-initramfs.cpio.gz \
	sh tools/pushctl.sh || { say "push/load failed - nothing was jumped to"; exit 1; }

say "watching for the report as tag=$CTLTAG, then firing the jump"
CTLTAG="$CTLTAG" LOGF="$LOGF" sh tools/gatewatch.sh 600 >staging/ctl/gatewatch.out 2>&1 &
GW=$!
# A second channel, and the only one that does not care which jack the cable is in: the
# payload's /init prints its report to /dev/kmsg before it touches the network, the board dts
# keeps a 2 MiB no-map console-ramoops window, and the warm watchdog reset leaves that text in
# DRAM until 4.19's allocator recycles it - within about a minute. So this starts before the
# jump and grabs the region the moment the installed kernel answers.
CTLTAG="$CTLTAG" LOGF="$LOGF" HOSTS="$HOSTS" sh tools/pstorecatch.sh >staging/ctl/pstorecatch.out 2>&1 &
PC=$!
sleep 3
$SSH "DELAY=45 MASK=$MASK CTLTAG=$CTLTAG setsid sh /tmp/ctl/ctl-exec.sh >/tmp/ctl/arm.out 2>&1 &" ||
	say "could not start ctl-exec - reading the round from the panel"
wait $GW
kill $PC 2>/dev/null
say "pstore channel (tools/pstorecatch.sh):"
tail -14 staging/ctl/pstorecatch.out 2>/dev/null | sed 's/^/  | /'
say "gate round verdict (from tools/gatewatch.sh):"
tail -20 staging/ctl/gatewatch.out | sed 's/^/  | /'

say "does the flash permit these bytes?"
if sh tools/gatecheck.sh staging/trial/flash-uImage staging/trial/flash-dtb.bin; then
	# A successful gate does not flash by itself - that stays a human act. But the board window
	# is what is scarce, so instead of exiting and losing it, park here until someone creates
	# GO. Writing then needs two more things: ssh on eth0's address, and the *installed* kernel
	# answering again - the payload's own canary reboot brings it back within ~4 min, and a
	# write attempted inside the RAM payload would have neither mtd tools nor a rollback path.
	if [ "$FLASH" != 1 ]; then
		say "permitted. Nothing will be written until $GOFILE exists; it waits up to $GO_WAIT min."
		say "  to go ahead in this window:  echo go >$GOFILE"
		say "  or later, in a new window:   FLASH=1 sh tools/runround.sh"
		end=$(( $(date +%s) + GO_WAIT * 60 ))
		while [ ! -f "$GOFILE" ] && [ "$(date +%s)" -lt "$end" ]; do sleep 5; done
		[ -f "$GOFILE" ] || { say "no GO within $GO_WAIT min - nothing written, the permit stays valid"; exit 0; }
		say "GO seen at $(date '+%T') - waiting for the installed system to come back"
		rm -f "$GOFILE"
		n=0
		while [ $n -lt 60 ]; do
			n=$(( n + 1 ))
			K=$(probe "${DEV#root@}")
			case "$K" in
			4.19*) say "installed kernel answering: $K"; break;;
			*) [ $(( n % 6 )) = 0 ] && say "still $K - the payload has not yielded the board yet";;
			esac
			sleep 5
		done
		case "$(probe "${DEV#root@}")" in
		4.19*) ;;
		*) say "refusing to write: $DEV is not answering as the installed 4.19 system, so the mtd"
		   say "tools and the rollback path it needs are not there. Once it does (the payload's own"
		   say "canary reboot is ~4 min in), the write is one command:"
		   say "  sh tools/flash-direct.sh write";
		   exit 1;;
		esac
		FLASH=1
	fi
	# A flashed board whose dropbear answers on no address at all cannot be reached, and that
	# looks exactly like a failed boot. So a reachable shell is a precondition of the write, not
	# a nicety, and unbinding dropbear is a change to the installed config that stays the
	# operator's call.
	# reachcheck reports eth0_ssh=1 when *either* 192.168.9.1 (lan bridge) or 192.168.9.2 (eth0
	# directly) answers. The current overlay puts lan's address on the bridge with eth0 as its
	# only port, so demanding .9.2 specifically would refuse every write on a board that is up
	# and reachable - which is the exact opposite of what this guard is for.
	R=$(sh tools/reachcheck.sh 2>&1 | grep "^REACH" || echo "REACH eth0_ssh=?")
	say "reachability before writing: $R"
	case "$R" in
	*eth0_ssh=1*) ;;
	*) say "refusing to write: no ssh answer on 192.168.9.1 or 192.168.9.2, so a successful flash would"
	   say "read as a dead board. Apply the fix deliberately with: APPLY=1 sh tools/reachcheck.sh"
	   exit 1;;
	esac
	say "permitted and reachable, and FLASH=1 - dry run first"
	sh tools/flash-direct.sh dry || { say "dry run refused"; exit 1; }
	say "writing dtb then kernel"
	sh tools/flash-direct.sh write
	say "write attempt finished. Reboot deliberately, then audit with:"
	say "  ssh root@192.168.9.1 reboot   # only once READ_OK appeared twice"
	# No HOST= here on purpose: passing it pins the probe to one address, and after this
	# project's own image is installed that address is 192.168.9.1, not 192.168.9.2.
	say "  sh tools/postflash.sh"
else
	say "NOT permitted - no write. The gate round above says why."
	# A payload that never reached its /init leaves no other trace, and with MASK=0 the eject
	# is a warm watchdog reset, which keeps DRAM contents: the board's own no-map pstore window
	# then holds mainline's console log. Read it while the board is still warm, because the
	# vendor kernel's page allocator reuses that memory within seconds of coming up.
	if ! grep -q VERDICT staging/ctl/gatewatch.out 2>/dev/null; then
		say "no report came in - capturing the ramoops window (0x9c000000, 2 MiB)"
		RM=staging/ctl/ramoops-$(date +%H%M%S).bin
		ONU="${DEV#root@}" timeout 240 python tools/getramoops.py "$RM" 2>&1 | sed 's/^/  | /'
		say "raw dump kept in $RM"
	fi
	# With MASK=0 a round that reports nothing is ambiguous: the vendor zx_wdt resets the
	# board ~15-20 s after the jump whether the payload died or merely never started. A
	# MASK=1 round disambiguates it - the board cannot self-eject, so coming back without a
	# report proves the handoff failed - but a real hang then needs the power unplugged,
	# which is why it stays the operator's deliberate call.
	if [ -n "$(probe "${DEV#root@}")" ]; then
		say "$DEV answered again, so the board recovered itself and a second round is safe."
		say "To make its silence unambiguous, at the price of a power pull if it hangs:"
		say "  MASK=1 FLASH=$FLASH WAIT=$WAIT sh tools/runround.sh"
	fi
fi
