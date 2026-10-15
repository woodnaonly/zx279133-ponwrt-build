#!/bin/sh
# Stage and load the flasher payload: the vendor 4.19 kernel (proven to reach userspace by
# control round K4) + the live DTB + the RAM root whose /init programs the mainline triple.
#
# For a kernel+dtb-only write prefer tools/flash-direct.sh instead: it does the same two
# writes from the running system, which is observable over ssh and, above all, does not have
# to fit the 8.1 MB uImage inside an initrd - the largest initrd this kernel has been
# *proven* to load is 577 kB. This RAM-root path is what `root` needs, because root is the
# filesystem / is mounted from and erasing a mounted jffs2 oopses the kernel mid-write.
#
#	sh tools/flash-round.sh            # dry run: the payload surveys the flash and serves
#	                                   # it over http://192.168.9.1/, writing nothing
#	sh tools/flash-round.sh write      # program dtb + kernel only, leaving the installed jffs2 intact
#	sh tools/flash-round.sh write-root # also erase + program root (full mainline image)
#
# Why the vendor kernel carries the flasher instead of mainline: the payload must reach
# userspace reliably, and as of this round that is proven for the vendor kernel and not for
# mainline. The tools it needs (/sbin/mtd, devmem2) are in the initrd; the images are fetched
# back over the network by default - see STAGE below.
set -eu
cd "$(dirname "$0")/.."
DEV=${DEV:-root@192.168.9.1}
KEY=${KEY:-$HOME/.ssh/id_onu}
D=${D:-/tmp/ctl}
MODE=${1:-dry}
CTLTAG=${CTLTAG:-F0}
SSH="ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i $KEY -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no $DEV"

[ -s staging/ctl/rootfs/mtd ] || { echo "staging/ctl/rootfs/mtd missing - pull it: $SSH 'cat /sbin/mtd' > staging/ctl/rootfs/mtd (plus any libs ldd names)" >&2; exit 1; }
[ -s staging/ctl/gate-Image ] || { echo "gate-Image missing - unpack the new flash-uImage first" >&2; exit 1; }

# The flasher initrd embeds the triple, so a rebuild is what makes "what is in RAM" equal
# "what we reviewed" - and its md5 is printed so the device copy can be compared.
case "$MODE" in
	dry)   TAG=FD1; ARGS="" ;;
	write) TAG=FW1; ARGS="--write" ;;
	write-root) TAG=FW2; ARGS="--write --root" ;;
	*) echo "mode is dry | write | write-root" >&2; exit 2 ;;
esac

# The gate on the artifact, not on the dts we think we wrote: root must be at 0xf00000 (so
# that `mtd write <img> root` on the running 4.19 lands where the new kernel mounts) *and*
# be the sixth child (so that U-Boot's root=/dev/mtdblock5 names it). Both are checked by
# tools/dtbparts.py and either one failing means this image panics with no serial to read.
python tools/dtbparts.py staging/trial/flash-dtb.bin root=0xf00000,0xf000000 \
	|| { echo "refusing to build a flasher for this dtb - see TABLE WRONG above" >&2; exit 1; }

# Same proof as tools/flash-direct.sh: a `--write` initrd is a fire-ready irreversible command,
# so it should not even get built for bytes that have never booted on this board.
if [ "$MODE" != dry ]; then
	sh tools/gatecheck.sh staging/trial/flash-uImage staging/trial/flash-dtb.bin || exit 1
fi

# Freshness guard for `write-root`, and only for that mode.
#
# root is the one partition whose contents this project changes *after* the image is built:
# the 02_network in the built rootfs decides the only address ssh answers on. The staged
# flash-set was built 2026-10-12 20:52; the overlay's 02_network was fixed 2026-10-14, and
# the fix is NOT in the staged jffs2 (verified by extracting it: the staged copy is the
# pre-fix 1080 B `ucidef_set_interface_lan "eth0"`, which leaves the address to
# config_generate's 192.168.1.1 - the address no tool here waits for, and the one this
# machine's own router already answers on).
#
# Checked by mtime rather than by searching the image on purpose: jffs2 nodes are zlib
# compressed, so a grep that finds nothing is not evidence of anything. A freshness check
# needs no extractor, and it fails safe for the case that actually matters - source moved
# on, artifact did not.
#
# Kernel and dtb are exempt: those are the partitions flash-direct.sh writes, and they are
# covered by gatecheck instead.
if [ "$MODE" = write-root ]; then
	NET=ci-build-repo/overlays/target/linux/zte/zx279133/base-files/etc/board.d/02_network
	RS=staging/trial/flash-rootfs.jffs2
	if [ ! -f "$NET" ]; then
		echo "refusing write-root: cannot find $NET to compare against" >&2
		exit 1
	fi
	NET_T=$(ls -l --time-style=+%s "$NET" 2>/dev/null | tr -d ' ' | sed 's/.*\([0-9]\{10\}\)$/\1/')
	[ -n "$NET_T" ] || NET_T=$(stat -c %Y "$NET" 2>/dev/null || echo 0)
	RS_T=$(stat -c %Y "$RS" 2>/dev/null || stat -f %m "$RS" 2>/dev/null || echo 0)
	echo "=== staged rootfs vs the 02_network currently in the tree ==="
	echo "  02_network (source) mtime: $NET_T"
	echo "  flash-rootfs.jffs2  mtime: $RS_T"
	if [ "$RS_T" -lt "$NET_T" ]; then
		echo >&2
		echo "refusing write-root: the staged rootfs is OLDER than the board.d it contains." >&2
		echo "  It was built before the fix, so writing it would put a board on lan" >&2
		echo "  192.168.1.1 - unreachable by every tool here, and shadowed by this" >&2
		echo "  machine's own router. Rebuild the flash-set first (re-run CI), or" >&2
		echo "  run 'write' instead, which leaves the installed root untouched." >&2
		exit 1
	fi
	echo "  ok: the staged rootfs is not older than the board.d in the tree"
fi

# How the images reach the payload. `net` is the default because size is a real risk here:
# embedding the triple makes a 12 MB initrd, and the largest initrd this vendor kernel has
# been *proven* to load is 577 kB - a load failure would look exactly like a dead board. With
# net staging the initrd stays at 595 kB and the payload fetches each file from the PC and
# verifies its md5 before anything is erased (`embed` remains for when the PC cannot be
# reachable from the RAM root).
STAGE=${STAGE:-net}
# The jffs2 only goes into the RAM root when a mode actually erases root: it is 4 MiB that
# would otherwise sit unused and change the md5 of every round's payload.
ROOTARG=""
[ "$MODE" = write-root ] && ROOTARG="--payload p/flash-rootfs.jffs2=staging/trial/flash-rootfs.jffs2"
sha256sum staging/trial/flash-uImage staging/trial/flash-dtb.bin staging/trial/flash-rootfs.jffs2 \
	| awk '{print "triple: " substr($2,index($2,"flash")) " " substr($1,1,16)}'
python tools/mkctlram.py --mode flash --tag "$TAG" $ARGS --stage "$STAGE" \
	--extra sbin/mtd=staging/ctl/rootfs/mtd \
	--extra sbin/devmem2=staging/ctl/rootfs/devmem2 \
	--payload p/flash-uImage=staging/trial/flash-uImage \
	--payload p/flash-dtb.bin=staging/trial/flash-dtb.bin $ROOTARG
if [ "$STAGE" = net ]; then
	cat <<EOF

Net staging: the payload will connect back to this PC, so start the server BEFORE firing the
jump (ports follow the --payload order above, one file per port; keep it running until the
report says STAGED for each file):
  python tools/fileserve.py 5561 staging/trial/flash-uImage 5562 staging/trial/flash-dtb.bin \\
    $( [ "$MODE" = write-root ] && echo "5563 staging/trial/flash-rootfs.jffs2" )
EOF
fi

MD5=$(md5sum staging/ctl/ctl-flash.cpio.gz | awk '{print $1}')
echo "flasher initrd md5 $MD5"
$SSH "mkdir -p $D"
for f in staging/probe-Image:Image staging/ctl/fdt-live.bin:fdt staging/ctl/ctl-flash.cpio.gz:ctl-flash.cpio.gz tools/ctl-kexec.sh:ctl-kexec.sh tools/ctl-exec.sh:ctl-exec.sh; do
	L=${f%:*}; R=${f#*:}
	printf 'copying %s -> %s ... ' "$L" "$R"
	timeout 300 sh -c "$SSH \"cat > '$D/$R'\"" < "$L" || { echo failed; exit 1; }
	echo done
done
$SSH "md5sum $D/ctl-flash.cpio.gz; cat /sys/kernel/kexec_loaded"
echo "expect locally: $MD5"

echo "=== load (vendor 4.19 + live dtb + flasher initrd) ==="
$SSH "cd $D && CTLTAG=$CTLTAG INITRD=ctl-flash.cpio.gz sh ctl-kexec.sh"
cat <<EOF

Loaded. To fire it and read the survey:
  sh tools/gatewatch.sh 300 &
  $SSH 'DELAY=45 MASK=0 CTLTAG=$CTLTAG setsid sh $D/ctl-exec.sh >/dev/null 2>&1 &'
  curl -s -m 5 http://192.168.9.1/        # CTLFLASH line: geometry, sizes, /dev/mtd nodes
Dry run exits by itself after ~150 s (reboot -f, then SysRq-b). Mode was: $MODE
EOF
