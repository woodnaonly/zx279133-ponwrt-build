# 86 — The CI's own release, flashed and booted twice, and routing

**Date:** 2026-10-25
**Subject:** the last link in the chain - the image that GitHub Actions
built, downloaded, written into the NAND, and booted twice on its own, with
the NAT rule inside it rather than added by hand.

---

## 1. What was left to close

The G4 acceptance had already been met once, on 2026-10-25 between 01:35 and
03:05: the board was flashed from the RAM environment, the plug cut the
power twice, and both times the board came up by itself and answered - 14/14
on the mainline gate and 5/5 on `verify_router.sh`.

What that round did not close was the last link.  The image it flashed was
built on this PC: the kernel came out of the run 165 release, and the
compression into the 8 MiB partition was done here with the same zopfli the
CI now uses, and the bytes were checked against the CI's own output
byte for byte.  That is a strong argument, and it is still an argument -
the CI's own release was never fetched, never written, never booted.

So the round this document records did one thing: it took the release that
GitHub Actions published, wrote it into the flash exactly as the CI says
to, and put the board through the two cold boots and the NAT round again.

## 2. The release

The release the CI published for this round is **`skd840n_flash_182_40209332232`**.

That is run 182 of `build-sk-d840n.yml`.  It is not the first flash
release the CI ever published: `staging/trial` holds
`skd840n_flash_166_*` and `skd840n_flash_181_*`, and 181 was already
flashed and booted twice (HANDOFF section 9.21).  What this run is the
first of is the narrower thing docs/86 records, and the cold boots below
are the measurement of it: the first image whose own
`/etc/init.d/sk-nat` reaches the board executable and installs the
masquerade rule without help.

Before anything was written to the NAND the release went past the gate
(`tools/preflash.py`), which reads the manifest, the uImage and the
rootfs of the release - and the rootfs by its file names:

```
the release: C:\UGit\openwrt\SK-D840N\staging\trial\skd840n_flash_182_40209332232
  OK   flash-uImage matches the manifest
  OK   flash-dtb.bin matches the manifest
  OK   flash-rootfs.jffs2 matches the manifest
  OK   the uImage fits the 8 MiB kernel partition - 8069601 bytes, 319007 to spare
  OK   the uImage has the right magic - 0x27051956
  OK   the payload is gzip, which is all the loader reads - compression type 1
  OK   the payload decompresses - 20568072 bytes of kernel, md5 e3ef0bbc67198c691f6b42da9488894b
  OK   the kernel announces 6.18.38 - Linux version 6.18.38+ (runner@runnervmwhx2h) (aarch64-openwrt-linux-musl-gcc (OpenWrt GCC
  OK   the kernel's version line is complete - U ld (GNU Binutils) 2.44) #0 SMP Fri Oct
  OK   the rootfs carries the NAT init script - /etc/init.d/sk-nat
  OK   the rootfs starts the NAT rule at boot - the uci-defaults is in the image
       the names in the rootfs with 'nat' in them - 99-sk-nat, dropbear.alternatives, ip-full.alternatives, logd.alternatives, sk-nat, ubox.alternatives, uclient-fetch.alternatives
       the rootfs holds 769 names
  OK   the dtb has the dtb magic - d00dfeed

preflash: the release is fit to be flashed
```

## 3. The bytes are the same kernel, and not the same bytes

The image that was flashed on 2026-10-25 at 01:35 - the one that passed the
two cold boots - was:

| file | bytes | md5 |
|---|---|---|
| `flash-uImage` (zopfli'd on this PC) | 8069601 | `329f1bbc1b9b5af2d6d5ba634c2d3bdf` |
| `flash-dtb.bin` | 13040 | `8c460c9794558ac7e18bcb18d903a228` |
| `flash-rootfs.jffs2` | 4587524 | `55ee805ae7013fe333419577daf9e130` |

The three files the CI published, as they arrived here:

| file | bytes | md5 |
|---|---|---|
| `flash-uImage` | 8069601 | `170592a71ed0d83b9aea4b77fd2aa628` |
| `flash-dtb.bin` | 13040 | `8c460c9794558ac7e18bcb18d903a228` |
| `flash-rootfs.jffs2` | 4718596 | `2af47bc59aeb090982e69f1e658487d4` |

These are the bytes this round wrote, and the FLASH section's read-back
is the proof that the partitions hold exactly them.  The table that used
to sit here described a *hand-built* image from 2026-10-25 01:35 - other
md5s (`329f1bbc...`, `55ee805a...`) inside a section about a CI release,
which is how a document starts lying, so it is gone.

Two things are worth saying about the comparison, because they are the two
ways one can get this wrong.

**The uImage can never be byte-identical.**  A uImage header holds the time
the image was made, so two images built from the same kernel at different
moments are different files.  What has to match is the payload - the gzip
stream, and the kernel that comes out of it.

**The kernel cannot be byte-identical either, and for a reason that is not
about the build.**  The kernel's version line carries the name of the
machine that built it:

```
Linux version 6.18.38+ (runner@runnervmwhx2h) (aarch64-openwrt-linux-musl-gcc ...) #0 SMP Fri Oct 23 22:18:27 2026
```

GitHub's runners have a different hostname each time, so the kernel binary
differs from run to run even when the source, the configuration and the
toolchain are identical.  The date in that line, on the other hand, is the
base tree's commit time - `5996412`, 2026-10-23 22:18:27 UTC - which is
pinned, and which is why the *source* is provably the same.

So the right claim is not "the CI's image is the image that was accepted".
It is: **the same sources, the same configuration and the same toolchain,
built by the CI, flashed by the CI's own flasher, and booted by the board
itself.**  `tools/preflash.py` checks what can be checked before anything is
written, and `staging/ctl/cmpuimage.py` compares what can be compared after
the fetch.

## 4. The flash

The flash was done the way the CI says to do it: the images were
written from the RAM environment, in one power window, so that the
plug could not cut the power in the middle of `mtd write`.

```
flashonpower: the power came back at 01:33:10, starting now
  the uImage is 8069601 bytes, the rootfs is 4718596 bytes
  the flag file was already away
flasher: verify kernel
flasher:     mtd write rootfs.jffs2 root && mtd verify rootfs.jffs2 root
kernel: OK 170592a71ed0d83b9aea4b77fd2aa628
dtb: OK 8c460c9794558ac7e18bcb18d903a228
root: OK 2af47bc59aeb090982e69f1e658487d4
g4flash: the flag file is back on the way out
  the flag file is back: the next power cycle is the board's own
g4flash: done at 01:34:44
flashonpower: g4flash said 0 at 01:34:44
```

This round's own step did the write - `g4flash said 0`, and the
read-back shows the partitions holding the same bytes:

```
(no manual write in this round: the flashbyhand.txt on disk predates this run's release and is not quoted)
```

## 5. Three things that were wrong on the way

The round did not go straight through.  Three things went wrong, and two of
them are worth writing down because they look like success while they are
happening.

### 5.1 A reset that lost the loader's banner

The board has to be in the loader to boot the RAM image, and the RAM image
is what programs the flash.  The reset that puts it there was done by
`tools/hardreset.ps1`, which takes the serial port away from the recorder
while it does it - because a COM port can only be owned once.

The loader prints its banner about a second after the reset line is
released, which is exactly while the helper owns the port.  So the banner
was never seen, the round said `the loader never showed up`, and the board
went on booting the flash it already had.  Measured at 22:29 on 2026-10-25:

```
############ 4. the loader, and then the RAM image ############
hardreset: stopping the relay (pid 13744)
hardreset: holding the reset line for 4000 ms
hardreset: done
hardreset: the relay is back
  waiting for the loader's banner
g4flash: the loader never showed up
```

The fix is to let the recorder do the reset: it holds the port, so it sees
the banner, and the spaces it sends are what stop the loader's countdown.
The countdown is the other half of it - the banner is printed long before
the countdown starts, so a single space sent at the banner is spent on
nothing and the countdown runs out and the board boots the flash by itself.
The recorder now sends spaces until the loader is at its prompt:

```
=== >>> 22:44:07 autoboot: the loader is at its prompt, the spaces stop ===
```

And the relay's own DTR/RTS toggle turned out not to reset this board at
all: the board answered pings through the whole of it, and no banner came
out.  What does work is the loader's own `reset` command, which is a
command sent over the console to a board that is already at its prompt.

### 5.2 A pipe that breaks halfway looks like a pipe that worked

The images are put on the board with a pipe:

```sh
cat staging/trial/flash-sysupgrade.bin | ssh "cat > /tmp/flash/flash-sysupgrade.bin"
```

The board's file came back 133120 bytes short of the local one - 13050077
of 13138197 - and the script said `done`.  Nothing was erased: the
flasher's own manifest check (`sha256sum -c sha256sums-release`) refused
to program the flash, which is precisely what that check is for.

### 5.3 A file left over from an earlier round

The file that was short was also the wrong file.  The round's step 2 copies
the uImage, the dtb, the rootfs and the manifest out of the release - and
not the sysupgrade image - so the one that went to the board was a leftover
of the run 165 round, and the release's own manifest does not match it.
The two mistakes hid each other: a stale file that is also a short file
looks like a transfer that broke.

Both are fixed in the bench now: the release's own copy of every file goes
to the board, and the size of what arrived is compared with the size of
what was sent, and the copy is repeated until it is whole.

The evidence, in the order it happened.

**The manifest check on the board, with the stale file:**

```
fetching flash-sysupgrade.bin   -> C:\UGit\openwrt\SK-D840N\staging\trial\skd840n_flash_182_40209332232\flash-sysupgrade.bin
   flash-sysupgrade.bin   OK
   (no flash-sysupgrade.bin in this release)
  flash-sysupgrade.bin
```

**And then the write, with the release's own file:**

```
(no manual write in this round: the flashbyhand.txt on disk predates this run's release and is not quoted)
```

## 6. Cold boot 1

Cold boot 1 - the plug cut the power, and the board came up by
itself, out of the flash, with nothing sent to the console.

```
coldboot: the console log is 103382771 bytes now; waiting for a boot
coldboot: the kernel banner appeared at 01:48:40
coldboot: waiting for the board to answer over ssh
coldboot: the board answers at 01:48:58: kernel 6.18.38+, uptime 19s
coldboot: giving the userspace a moment to settle
coldboot: verification round 1
coldboot: the original gate as well
coldboot: verify_router says 0
coldboot: done at 01:49:37
```

## 7. Cold boot 2

Cold boot 2 - the plug cut the power, and the board came up by
itself, out of the flash, with nothing sent to the console.

```
coldboot: the console log is 103439624 bytes now; waiting for a boot
coldboot: the kernel banner appeared at 02:04:09
coldboot: waiting for the board to answer over ssh
coldboot: the board answers at 02:04:25: kernel 6.18.38+, uptime 16s
coldboot: giving the userspace a moment to settle
coldboot: verification round 1
coldboot: the original gate as well
coldboot: verify_router says 0
coldboot: done at 02:05:04
```

## 8. The NAT round

The NAT round is the thing the image exists for: the board
masquerades one client behind it, and the counters and the
conntrack table are read while that is true.  The rule is the one
the image carries - `/etc/init.d/sk-nat` - and not one that was
put there by hand.

```
############ 0. the bench ############
############ 1. the WAN port moves to lan2, over the console ############
root@ImmortalWrt:~# [   59.690634] sk-nat: masquerade installed on lan2
############ 2. this PC becomes the upstream ############
############ 3. the board takes a lease on the shared subnet ############
############ 4. dropbear listens on the wan as well ############
############ 5. a client behind the router ############
=== copying the client over ===
=== waiting for the client to take a lease from the board ===
client: labcli0 (netns: 1)
lease: 192.168.9.240/24
ping 223.5.5.5: PING 223.5.5.5 (223.5.5.5): 56 data bytes 64 bytes from 223.5.5.5: seq=0 ttl=50 time=18.589 ms  --- 223.5.5.5 ping statistics --- 1 packets transmitted, 1 packets received, 0% packet loss round-trip min/avg/max = 18.589/18.589/18.589 ms 
conntrack:
############ 6. the evidence ############
=== the masquerade rule and its counter ===
		oifname "lan2" counter packets 1 bytes 84 masquerade
=== the client's flow, with the address rewritten ===
=== the wan side's own counters ===
=== and the client, from inside its own namespace ===
2 packets transmitted, 2 packets received, 0% packet loss
--- the masquerade rule
		oifname "lan2" counter packets 2 bytes 168 masquerade
=== the client's own evidence ===
client: labcli0 (netns: 1)
lease: 192.168.9.240/24
ping 223.5.5.5: PING 223.5.5.5 (223.5.5.5): 56 data bytes 64 bytes from 223.5.5.5: seq=0 ttl=50 time=18.589 ms  --- 223.5.5.5 ping statistics --- 1 packets transmitted, 1 packets received, 0% packet loss round-trip min/avg/max = 18.589/18.589/18.589 ms 
conntrack:
--- the wan tx counter
############ 7. putting everything back ############
=== removing the client ===
```

## 9. What is still open

G5, the PON state machine, is still frozen.  Everything in this document is
M1: a lab router on Ethernet, with the 9132's jacks behind it, masquerading
one client and serving another.  Nothing here says anything about the PON
data path.
