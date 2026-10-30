# The live experiment runbook

Every command in this document is one line typed into the board's console, and
every one of them can be undone or left alone: the board is on the trial
kernel, in RAM, and the power is cut every ninety seconds or so, so nothing
here survives a power cycle anyway.

## The channel

The board's only network path in from the PC runs through the 9132, and the
9132 is what is being fixed, so SSH is not an option on a trial kernel that
cannot forward a frame.  Everything is therefore typed into the console and
read back out of the serial log.

```
staging/ctl/sercmd.txt      the file the relay reads and types
staging/ctl/serial.log      everything the board says
staging/ctl/dbg.sh <file>   type a list of commands, show the answer
staging/ctl/survey.sh       the whole register survey in one go
staging/ctl/accept5.sh      the G2 acceptance
```

`dbg.sh` joins the lines of its file with `; ` and types them as one line,
so the whole list is one round trip - the board does not live long enough
for one round trip per command.

## The two rules the console imposes

**Numbers with a prefix.**  The console parses a number with
`kstrtou32(tok, 0)`, and base 0 means the prefix decides: `0x140000` is
hexadecimal, `140000` is decimal, `010` is octal.  Every register offset
has to carry the `0x` prefix, and so does every ERAM table id.

**The table id is not the table number.**  `eg 18 128 2` reads entry 128 of
table **0x12**, because 18 is decimal.  The switch protocol table is 0x18,
so it is `eg 0x18 128 2`.  The console's own help text gets this wrong
("eg 18 128 2" is printed as the example), which is how the mistake gets
made.

## The registers that matter

| what | offset | what it tells you |
|---|---|---|
| XMAC0 TX_CTRL | `0x140000` | bit 0 is the enable; bits 29..31 are the speed |
| XMAC0 RX_CTRL | `0x140010` | bit 0 is the enable; the rest is the vendor's value |
| XMAC0 FRAME_CFG | `0x140020` | |
| XMAC0 MODE_CFG | `0x140280` | |
| XMAC0 DUPLEX | `0x140500` | bit 24 is half duplex |
| XMAC0 MISC_CFG | `0x143400` | bit 9 is the IPC bit |
| XMAC0 TX frames | `0x142070` | **WITHDRAWN - this window is not the data path's counter (docs/92 §96.33.9)** |
| XMAC0 TX good | `0x1420b0` | |
| NPPT XMAC link | `0x0084` | |
| NPPT XPCS0 status | `0x0090` | bits 11..13 are the PCS link |
| NPPT XMAC error | `0x00fc` | |
| IDM configuration | `0x280000` | bit 24 and bit 13 differ from the vendor |
| IDM RX queue counters | `0x2800c4`..`0x280104` | one word per hardware queue |
| IDM BP ring | `0x28010c`, `0x280110` | the vendor's encoding differs from the driver's |
| SOPC ready array | `0x34294` + 4*i | the "ready" bit of SerDes output port i |
| SOPC send array | `0x342b0` + 4*i | the send enable of the same |

The ERAM tables the driver lays out are printed by `cat
/sys/kernel/debug/zx279133-9132`: the base block and the width of every
entry.  The two that matter here are

* table **0x1f** - the flood group table - base block 19975, 32 bits per
  entry, so `es 0x1f <group id> 32 <port id>` writes one group's
  destination port;
* table **0x7d** - the MF extension table - base block 24737, 128 bits per
  entry, one entry per SPA memory id.  Word 0 is the port attribute word,
  word 1 is SPA register 18 and word 2 is SPA register 17.

## The experiments

### E1 - did the XMAC come up?

Read it:

```
echo "reg 0x140000" > /sys/kernel/debug/zx279133-9132
echo "reg 0x140010" > /sys/kernel/debug/zx279133-9132
echo "reg 0x140020" > /sys/kernel/debug/zx279133-9132
echo "reg 0x140280" > /sys/kernel/debug/zx279133-9132
echo "reg 0x143400" > /sys/kernel/debug/zx279133-9132
```

The values the vendor's own `xamc_init_conf_by_speed()` writes are TX_CTRL
`0x00010000` with the speed bits, RX_CTRL `0x3e8000c6`, FRAME_CFG
`0x80000000`, MODE_CFG 2, and MISC_CFG with bit 9 set; the enable is bit 0
of TX_CTRL and of RX_CTRL.  If the words read back as anything else, the
program did not happen or something overwrote it.

### E2 - did the frame get out of the MAC?  (the old counter is void)

`reg 0x142070` was believed to be the counter of frames the XMAC put on the
wire: if it stayed at zero while the netdev's own tx counter moved, the frame
would never have reached the MAC.

**That criterion is void** (docs/92 §96.33.9).  The window at `0x142000` is
not the data path's counter - a run of full-length frames through the 2.5G
jack moved it by an order of magnitude less than the frames that went by,
and another window of the same block moved by more than lan5's own total.
It measures something else, and what it measures does not track the lane.

Two readings do work, and neither of them lives in the switch:

* **`/sys/class/net/<port>/statistics/tx_packets`** - the DSA port's own
  transmit counter.  Six echo requests to the host behind the 2.5G jack move
  lan5's counter by exactly six, while the trial image's own veth
  (`labhost0`) does not move at all.  That pair is the proof, and the second
  half of the pair is what keeps the fixture from passing for the lane.
* **`bridge fdb show br br-lan`** - prints the DEVICE name for every entry,
  where `brctl showmacs` prints a bare number.  Read that number wrong and
  the whole topology comes out wrong (docs/92 §96.33.3).

The DSA per-port RX counters cannot be used either: the frames the driver
collects are counted on `lan-cpu0`, so the user ports' RX counters stay at
zero (docs/92 §96.32.5).

### E3 - did the frame get into the IDM?

`reg 0x2800c4`..`reg 0x280104` are the per-queue receive counters.  They
are the only evidence of a frame arriving that the chip will ever give.

### E4 - the flood groups (the receive path)

The four UNI jacks' flood groups hold the WAN port in the vendor's
configuration, so a unicast frame addressed to the board is dropped at the
9132 before the CPU sees it.  Put the CPU port into them:

```
echo "es 0x1f 0x40 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x80 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0xc0 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x100 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x440 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x480 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x4c0 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x500 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x840 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x880 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x8c0 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x900 32 5" > /sys/kernel/debug/zx279133-9132
```

The three indexes of a port p are `p<<6`, `(p+0x10)<<6` and `(p+0x20)<<6`
- the unknown-DA, broadcast and multicast groups - and the value is the
destination port id itself, in bits 0..5, not a bitmap.  Port 5 is the CPU
port.  The vendor's `group_id_table_port_id_set()` masks the port id to six
bits and sets bit 7 only for an SSID port, and its
`se_eram_table_set(0x1f, group_id, 1, &port_id)` addresses the entry at
`bit_len * index`, which is exactly what the driver's helper does.

The group ids also have to be in the table the switch engine reads: ERAM
0x7d's word 1 (SPA register 18) and word 2 (SPA register 17).  Read the
entry first, then write the whole block back with `ew`:

```
echo "er 24738 1" > /sys/kernel/debug/zx279133-9132
echo "ew 24738 <word0> <word1> <word2> <word3>" > /sys/kernel/debug/zx279133-9132
```

with word 1 and word 2 replaced by the vendor's own flood words of that
port: PPU 1 `0x00400840` / `0x04400410`, PPU 2 `0x00800880` /
`0x04800410`, PPU 3 `0x00c008c0` / `0x04c00410`, PPU 4 `0x01000900` /
`0x05000410`.

### E5 - the IDM's shared words

The driver writes `0x1000` to IDM+0x10c and IDM+0x110 where the vendor
writes `0x00203c00` and `0x00003c00`; the driver sets bit 13 of IDM+0x000
where the vendor sets bit 24 and clears bit 13.  Each is one write:

```
echo "reg 0x280000" > /sys/kernel/debug/zx279133-9132
echo "regw 0x28010c 0x00203c00" > /sys/kernel/debug/zx279133-9132
echo "regw 0x280110 0x00003c00" > /sys/kernel/debug/zx279133-9132
```

### E6 - the vendor's XMAC values, put back by hand

```
echo "regw 0x140000 0x38000000" > /sys/kernel/debug/zx279133-9132
echo "regw 0x140010 0x3e8000c7" > /sys/kernel/debug/zx279133-9132
echo "regw 0x140020 0x80000000" > /sys/kernel/debug/zx279133-9132
echo "regw 0x140280 0x00000002" > /sys/kernel/debug/zx279133-9132
```

TX_CTRL `0x38000000` is `0x00010000` with the 1G speed code 3 in bits
29..31 and the enable in bit 0; RX_CTRL is the vendor's `0x3e8000c6` with
the enable in bit 0.  The speed code is 3 for 1G, which is what this lane
runs at.

## What each answer means

| observation | what it says |
|---|---|
| XMAC0 TX frames moves, lan-cpu0 rx does not | the frame left the SoC and the 9132 did not give it back - the 9132's own forwarding |
| XMAC0 TX frames does not move, the netdev's tx does | the frame never reached the MAC - the IDM or the XMAC's programming |
| lan-cpu0 rx moves, the PC sees nothing | the frame arrived and the reply did not go out - the reverse direction |
| the IDM queue counters move, lan-cpu0 rx does not | the frame is in the IDM and nobody polled that queue |

## Accepting the lablan setup (2026-10-29)

The trial image carries a script, `S28lablan`, that makes the ONT behind one
jack reachable from every other jack.  The 9132 does not carry a frame from
one jack to another - not even one whose destination MAC is known
(docs/92 §96.37) - so the board has to do the forwarding itself, and that
takes three things: an address in the ONT's subnet on the bridge, a route
for the ONT out of its own jack, and, for every client, a route and a pinned
MAC out of the client's own jack.

To accept it:

1. plug the PC into the 2.5G jack (the panel's LAN1) and the ONT into
   LAN4;
2. put the image on the board and let it boot (TFTP `run249.itb` and
   `bootm 0x89000000`, or whatever the latest run is called);
3. on the PC: remove every static address, set the adapter to DHCP and
   wait for a lease.  The lease has to be an address in 192.168.1.0/24 -
   that is the range the image's dnsmasq hands out first, and a client in
   another subnet cannot reach the ONT at all;
4. ping 192.168.1.1.  Five answers are a pass.  The first answers can take
   a few seconds: the client asks for the ONT at once, and the board cannot
   hand the ONT's reply back until it has pinned the client's MAC, which it
   does every five seconds.

`staging/ctl/accept-lablan.ps1` does steps 3 and 4 and prints PASS or FAIL.

What to look at when it fails:

| observation | what it says |
|---|---|
| the PC has no 192.168.1.x lease | dnsmasq is not serving the ONT's range, or the ONT's jack is not in the bridge |
| the PC's neighbour for 192.168.1.1 is not the board's MAC | the board's proxy ARP is not answering - the route for the ONT out of its own jack is missing |
| the PC's neighbour is the board's MAC but nothing answers | the client's own pin is missing - the board has not worked out which jack the client is in |
| the board logs "the ONT has no MAC yet" | the ONT's MAC could not be learned through the bridge |

**The three traps that this setup has (measured on 2026-10-29 22:02-22:09,
docs/92 §96.39).** All three make the acceptance fail while the board's own
log says everything is fine, so they are worth knowing before the first
attempt rather than after it:

1. **A second dnsmasq cannot have the DHCP socket.** The image's dnsmasq is
   run by procd (`S19dnsmasq`), and S26nat has already started one. A second
   one dies with `failed to bind DHCP server socket: Address in use`, and the
   check that is supposed to notice (`pidof dnsmasq`) sees the *old* process,
   which is still alive. The range has to be written through UCI and applied
   with `/etc/init.d/dnsmasq restart`, so that there is one server and one
   configuration.
2. **The DHCP section in this image needs three things** and misses any one of
   them silently: `interface` is a *network* name (`lan`), not a device name
   (`br-lan`); `force 1` is needed, because the ONT answers DHCP on a jack of
   the same bridge and the script would otherwise refuse to serve the range;
   and the range is computed from `network.lan`'s address, so that address has
   to be in the ONT's subnet - which is where the board's own address belongs
   anyway.
3. **`/etc/labnowan`'s "off" has to be the first line** (or the first line
   that is not a comment). Otherwise S26nat reads the comment, the upstream
   watcher runs anyway, and it takes the jack with the ONT behind it for the
   WAN - the kernel log then says "the test PC is in lan4", and lan4 is the
   ONT's jack.

**The judgement is not the script's own message.** `dnsmasq serves ...` is
written by the script whether or not it is true. The two things that are true
are: the generated configuration carries the range
(`grep dhcp-range /var/etc/dnsmasq.conf.*`), and the lease file is not empty
(`/tmp/dnsmasq.leases`).

**TFTP and the acceptance state are mutually exclusive.** The board fetches
the image from the PC at 192.168.9.25, and the acceptance wants the PC on
DHCP with no static address at all. So: give the PC 192.168.9.25, boot the
board, and only then start the acceptance. A `tftpboot` that stops halfway
leaves the loader saying `Wrong Image Format for bootm command`; the log
then has no `Bytes transferred = ...` line at all, and that is the sign that
the server was unreachable.

Two things must NOT be done: the traffic towards the ONT must not be
masqueraded (the reply then reaches the client's card with a checksum the
client's stack rejects - docs/92 §96.38), and no chip register needs to be
touched (the untag SVID and the LAN TX output port are back at their
defaults, and the traffic still goes through).

## Testing a script change without waiting for CI (2026-10-29 23:15 - 23:42)

A CI run takes 50-80 minutes and the smart plug cuts the power every 10-30
minutes, so "push, wait, boot, hope" does not fit in a window.  The script
can be changed and tested on the board inside one window, using an older
image as the carrier:

```
# on the PC: the scripts live in staging/trial, and the TFTP server
# already serves that directory - a second server on another port does
# not collide with it
python -m http.server 8000 --directory C:\UGit\openwrt\SK-D840N\staging\trial

# on the board (after the kernel is up and the PC has 192.168.9.25 again):
wget -O /tmp/lablan8.sh http://192.168.9.25:8000/lablan8.sh
chmod 755 /tmp/lablan8.sh
setsid /tmp/lablan8.sh >/dev/kmsg 2>&1 &
```

The inner script is the part between `cat > /tmp/lablan.sh <<'LABEOF'` and
`LABEOF` in `trial-files/etc/rc.d/S28lablan`; `staging/ctl/extract-lablan.py`
pulls it out of the file, so the thing on the board is the thing in the
repository.

Two things to do on the board first: the older image's own `S28lablan` has
to be stopped (`ps w | grep lablan` gives the pid), or two scripts fight
over the same dnsmasq and the same bridge; and the masquerade the old
image's `S26nat` puts on the ONT's jack has to go (`nft delete table ip
nat`), because a masqueraded reply does not survive the client's stack.

**Seven traps, all of them "the log says it works and it does not"**
(docs/92 §96.40):

1. `network reload` rebuilds the bridge asynchronously.  The address put on
   the bridge before the rebuild is on a bridge that is about to be thrown
   away, and `proxy_arp` - which belongs to the device - goes with it.
   Everything that has to survive the reload is put back afterwards, and
   every round of five seconds looks at it again.
2. The kernel numbers the bridge's ports again on every rebuild, and the
   old numbering is still in the log.  The mapping is read out of the log,
   and the *last* line for a port number is the current one.
3. The image runs dnsmasq under a ujail wrapper, and the wrapper's own
   line carries the name too - so a count of "dnsmasq" processes is one
   too high.
4. This image's `ip` is busybox's.  `ip neigh show` prints
   `192.168.1.141 dev lan5 lladdr 84:47:09:5b:bd:2c PERMANENT`, so the
   MAC is behind `lladdr` and the device behind `dev`.  Reading the third
   field as the MAC takes the device name instead, and reading the second
   field as the device takes the word `dev`.
5. The question "who is 192.168.1.1" is an ARP request, and it leaves
   through whichever device the route to 192.168.1.1 says.  The frames
   the DSA layer sends out of a user port are tagged and the ONT does not
   answer a tagged ARP, so the route has to be pointed at the *bridge* for
   the length of the question - and put back on the ONT's own jack
   afterwards.  Watch out for another route to the same subnet: the
   image's own network service takes the ONT's jack for the WAN when the
   upstream search runs, and the lease it takes puts
   `192.168.1.0/24 dev lan4` in the table.
6. A neighbour entry that is left anywhere for the ONT's address is used
   as it is - the kernel does not ask again, so the question is never
   asked.  The entry is deleted on every device that has one, before
   every attempt.
7. A shell function that reads `$1` and is called in a pipeline reads
   nothing at all.  The helpers that pick the MAC and the device out of a
   line of `ip neigh show` read the standard input.

**A failure has to leave a picture.** "the ONT has no MAC yet" is the
script's own message, and it was wrong: the neighbour table had the
entry, REACHABLE, with the MAC in it.  So the script writes what it saw
when it gives up - the route, the bridge's ports and the neighbour table
- into the kernel log, and the two lines that contradict each other are
the ones to read:

```
trial: lablan: learn: the table is 192.168.1.1 dev br-lan lladdr 0c:f0:7b:39:d9:91 REACHABLE
trial: lablan: the ONT has no MAC yet; it will be learned
```

**What "it works" looks like in the log** (measured on 2026-10-29 23:41):

```
trial: lablan: the ONT is 192.168.1.1 (0c:f0:7b:39:d9:91) on lan4
trial: lablan: dnsmasq serves 192.168.1.101-192.168.1.200
trial: lablan: dnsmasq servers: 1
```

and the judgement on the PC: `staging/ctl/accept-lablan.ps1` prints
`PASS: 5 个回答`, with the lease in 192.168.1.0/24 and the neighbour for
192.168.1.1 being the board's own MAC.

**The second dnsmasq, and why "it started" is the wrong judgement.**
`/etc/init.d/dnsmasq restart` does not kill the bench's own dnsmasq: that one
was started outside procd, and the init script's stop only kills the
instance procd knows about.  The DHCP socket therefore stays with the bench
one, the image's dnsmasq dies with `failed to bind DHCP server socket:
Address in use`, and the clients get an address from the *wrong* range.

Measured on 2026-10-30 00:05 on image #269, with no manual intervention on
the board at all:

```
dhcp-range=set:lan,192.168.1.101,192.168.1.200,255.255.255.0,12h   <- the generated config is right
 2379 nobody    1340 S    dnsmasq -C /tmp/dnsmasq-trial.conf         <- but this is the one that is alive
```

The client then took **192.168.9.179** (the bench dnsmasq's range) and the
acceptance failed, while the board's log said "dnsmasq serves
192.168.1.101-192.168.1.200".  After `kill 2379` and one more
`/etc/init.d/dnsmasq restart`, the image's own dnsmasq was alive and the
same board passed: `192.168.1.141/24`, five answers.

So the trap is not "two dnsmasq fight over the socket" but "**whoever gets
the socket serves, and it is not necessarily the one whose configuration is
right**".  The judgement is therefore not "dnsmasq is running" - `pidof
dnsmasq` sees the one that is alive, which is the one that won - but
"**the one that is alive has the right range**", which means looking at the
process's own command line and at the range in the generated configuration.

**The acceptance, with nothing done by hand on the board** (measured on
2026-10-30 00:28-00:36 on image #270):

1. give the PC 192.168.9.25 (the boot needs it);
2. boot the board: `tftpboot 0x89000000 run270.itb` - the log must show
   `Bytes transferred = 12175420`, the length of the local file, and no
   `Wrong Image Format`;
3. wait 70-90 seconds and **do not touch the board** - the image's own
   `S28lablan` does the whole thing;
4. on the PC: `pwsh -NoProfile -File staging/ctl/accept-lablan.ps1 -IfIndex 19`.

The pass looks like this - DHCP gives the client an address in the ONT's
subnet, and the client can reach the ONT:

```
== 拿到租约 192.168.1.141/24 ==
== ping 192.168.1.1 ==
LinkLayerAddress : D0-45-8D-84-09-00
PASS: 5 个回答
```

and on the board:

```
trial: lablan: the ONT is 192.168.1.1 (0c:f0:7b:39:d9:91) on lan4
trial: lablan: dnsmasq serves 192.168.1.101-192.168.1.200
192.168.1.141 dev lan5 lladdr 84:47:09:5b:bd:2c PERMANENT
192.168.1.1 dev lan4 lladdr 0c:f0:7b:39:d9:91 PERMANENT
192.168.1.1 dev lan4 scope link
192.168.1.141 dev lan5 scope link
```

**The dnsmasq that is alive has to be the one whose configuration is
right.**  On image #269 this acceptance failed with the client taking
`192.168.9.179` - the bench's own dnsmasq was holding the DHCP socket, the
image's dnsmasq had died on `failed to bind DHCP server socket: Address in
use`, and the generated configuration (with the right range) belonged to
the one that was dead.  So after the acceptance, check
`ps w | grep dnsmasq`: there must be a `/usr/sbin/dnsmasq -C
/var/etc/dnsmasq.conf.*`, and there must **not** be a
`dnsmasq -C /tmp/dnsmasq-trial.conf`.

### 完全自动验收（第二轮：镜像 #276，2026-10-30 01:34–01:38）

#276 是**第一个把七个坑全部装进去**的镜像（#270 只有第一个）。这一轮的验收
**和 #270 走的是同一条路、同一个脚本**，只是镜像里的 S28lablan 是完整加固版。

```powershell
# PC 侧：先给有线口加回 TFTP 地址
New-NetIPAddress -InterfaceIndex 19 -IPAddress 192.168.9.25 -PrefixLength 24
```
（**WLAN 绝不动** —— 整个实验只用 ifIndex 19 这根有线。）

板上（U-Boot 提示符，**一条一条发，间隔 ≥4 s**）：
```
setenv ipaddr 192.168.9.1; setenv serverip 192.168.9.25
tftpboot 0x89000000 run276.itb     # 必须看到 Bytes transferred = 12176764
bootm 0x89000000
```
然后**等 70–90 秒**，让镜像自带的 S28lablan 自己跑完。**此后不要再碰板子**，
只剩下这四条**只读**的取证命令：
```
dmesg | grep lablan | tail -12
ps w | grep dnsmasq
grep -h dhcp-range /var/etc/dnsmasq.conf.*
ip neigh show
```

板上该看到的东西（**四项都要对上**，这是 §96.41 定下来的判据）：
- `the ONT is 192.168.1.1 (0c:f0:7b:39:d9:91) on lan4`
- `dnsmasq serves 192.168.1.101-192.168.1.200` **且** `dnsmasq servers: 1`
- `ps w | grep dnsmasq` 里活着的只有 `/usr/sbin/dnsmasq -C /var/etc/dnsmasq.conf.*`，
  **不能**有 `dnsmasq-trial.conf`
- `dhcp-range=set:lan,192.168.1.101,192.168.1.200,255.255.255.0,12h`
- 邻居表里客户端钉在 lan5（`PERMANENT`）、光猫钉在 lan4（`PERMANENT`）

PC 侧验收：
```powershell
pwsh -NoProfile -File staging/ctl/accept-lablan.ps1 -IfIndex 19
```
期望：`== 拿到租约 192.168.1.141/24 ==` 然后 **`PASS: 5 个回答`**，
邻居的 `LinkLayerAddress` 是板子 MAC（`D0-45-8D-84-09-00`）。

**#276 的实测结果（2026-10-30 01:34–01:38，四分钟）**：上面四项全对，
`PASS: 5 个回答`。**板上零手工干预。**

**镜像与校验**：release 直链
`https://github.com/woodnaonly/zx279133-ponwrt-build/releases/download/skd840n_initramfs_276_40907144565/sk-d840n-initramfs.itb`，
同 tag 的 `sha256sums` 给出
`4fbc3a54f567e9247cdf7283d4f2b7de7af14f9aa2e1e1de5a2844114c759c02`，
与本地算的一致；长度 **12176764** 字节，和 U-Boot 的
`Bytes transferred = 12176764` 一致。**长度对不上就是文件没下全，别往下走。**

**窗口纪律**：插座每 10–30 分钟断一次电，断电后板上一切运行时配置归零、回到
U-Boot。**一轮实验（引导 + 等 S28lablan + 验收）必须挤在同一个窗口里。**
#276 这一轮从引导到验收结束只用了 **四分钟**，所以窗口是够的 —— 但**下载和校验
要在窗口之外做完**。

### 回到 U-Boot 的两条路（2026-10-30 08:46 实测）

**relay 的复位对这块板子无效。** `tools/serrelay.ps1` 那套 DTR/RTS 抖动
（`hold_ms` 取 900 与 3500 各试一次）板子**毫无反应**，`/proc/uptime` 停在
`889.85` 不归零 ⇒ 这块板子的 RESET 不接在那两根线上。**别再在复位上花时间。**

只有两条路能回 U-Boot 提示符：

1. **插座断电**（板上一切运行时配置归零）—— owner 那边每 10–30 分钟一次。
2. **`reboot -f`**（在**flash 里那个厂商的 Oct 23 内核**上有效）：
   ```
   reboot -f
   ```
   内核打印 `[  960.920298] reboot: Restarting system`，随后 U-Boot 的横幅在串口上
   出来（`U-Boot 2021.01-svn308776`、`Hit any key to stop autoboot: 1 0`），
   **提示符是 `=>`**。从 `reboot -f` 到看到横幅约 **17 秒**。

   ⚠️ **适用范围**：docs/07:14880 记的是另一种情况 —— 在 flash 里那个 **mainline**
   上，`reboot` 与 `echo b > /proc/sysrq-trigger` 都**弹不回** U-Boot，只会让板子
   停住。**同一个动词在两个内核上结果不同**，别照搬。

拿到 `=>` 之后照常 TFTP 引导：

```
setenv ipaddr 192.168.9.1
setenv serverip 192.168.9.25
tftpboot 0x89000000 run276.itb     # 必须看到 Bytes transferred = 12176764
bootm 0x89000000
```

### 量「LAN1（2.5G）这条线有没有在跑帧」：三窗口（2026-10-30 09:00 实测）

**先看这个坑**：这个驱动**没有实现 per-port 的 rx 统计**。板上
`cat /proc/net/dev` 里 **lan1…lan5 的 rx 全是 0 字节 0 包**（只有 tx 在涨），
`/sys/class/net/lan5/statistics/rx_packets` 在三个窗口里也都是 **50 不动**。
⇒ **网卡层量不到这条线的收方向**；能用的是**芯片自己的 XMAC0 统计块**
（nppt + `0x142400` 起）。§94 那条「lane 的 frames 涨、dropped 不涨」（N5）
在网卡层是量不了的，别再拿它当判据。

脚本：`staging/ctl/lanerate-lablan.sh`（`tools/lanerate.sh` 的 lablan 版：
量 lan5 / br-lan，目标 `192.168.1.1`，**不往板子上写地址**）。
在 git-bash 里跑：

```bash
cd /c/UGit/openwrt/SK-D840N
sh staging/ctl/lanerate-lablan.sh
```

它做三段同长的窗口：**pre（静默）→ sil（静默 30 s）→ bur（PC 发 25 帧）**，
每段把 XMAC0 的统计块、XMAC1 的统计块、SOPC 那几块全读一遍，外加 lan5 与
br-lan 的网卡计数。**判据是老规矩：突发窗的增量必须明显大于静默窗的增量**
（静默窗量的是板子自己的背景流量）。

**期望值（2026-10-30 09:00，#276，PC 从 192.168.1.141 ping 光猫 192.168.1.1）**：

| 字 | 静默窗 | 突发窗 |
| --- | --- | --- |
| XMAC0 帧计数 `nppt+0x142400` | +36 | **+88** |
| XMAC0 字节计数 `nppt+0x142420` | +3109 | **+7145** |
| `br-lan` rx_packets | +94 | +122 |
| PC 侧 `ping 192.168.1.1` | — | **24/25 有回答** |

配套的板上只读取证（各发一条，间隔 ≥4 s）：

```
brctl showmacs br-lan     # 客户端 84:47:09:5b:bd:2c 必须落在 port 5 = lan5
ip neigh show              # 192.168.1.141 dev lan5 ... PERMANENT
cat /sys/class/net/lan5/speed /sys/class/net/lan5/operstate
```

远端 PHY 的寄存器用 `m45` 读（trial 镜像里这个动词可用；**flash 里的 Oct 23
内核没有**，会报「the running image does not have that verb yet」）：

```
m45 5 0x1e 0x758d   # 链路字，通流基准 0x801e
m45 5 0x1e 0x7589   # AN 状态，通流基准 0x035a
m45 5 0x1e 0x7580   # 模式字：0x9402 = 1000BASE-X；0x9416 = 2.5G 档
m45 5 0x07 0x01     # AN 状态：0x00e9 / 0x00ad / 0x002d 都会动，不作判据
m45 5 0x1f 0xa438   # DSP 补丁窗：0x0038 = SoC 侧在动这颗 PHY
```

**`7.0x01` 那个字不作判据**（§94 的结论）：能区分的只有一个东西 ——
**LAN1 上有对端，而且帧在动**。判据就用上面那张三窗口表。

### 没有对端（光猫没插）的时候，怎么测 LAN1 这条线（2026-10-30 10:39 实测）

光猫不在的时候，**板上唯一的对端就是板子自己**（`192.168.1.100`）—— 这是一次
干净的隔离测量：DHCP、ping、大流量三个层次都能测。

```powershell
# 1) 引导：PC 侧给有线口加回 TFTP 地址（板子此刻在 U-Boot 提示符）
New-NetIPAddress -InterfaceIndex 19 -IPAddress 192.168.9.25 -PrefixLength 24
# 板上：setenv ipaddr 192.168.9.1 / setenv serverip 192.168.9.25
#       tftpboot 0x89000000 run276.itb  →  Bytes transferred = 12176764
#       bootm 0x89000000
# 等 70–90 秒，让 S28lablan 自己跑完（它会学不到光猫，这是预期的）

# 2) 拿租约：**release + renew 两步**，只 renew 不保险
ipconfig /release 19
ipconfig /renew 19
Get-NetIPAddress -InterfaceIndex 19 -AddressFamily IPv4
# 期望：192.168.1.141/24（Dhcp）

# 3) 与板子对话（光猫不在，所以目标是板子自己）
ping -n 25 192.168.1.100
```

**光猫不在时，板上该看到的东西**（这些是**正常**的，不是故障）：
- `the ONT has no MAC yet; it will be learned`
- `learn: the route to 192.168.1.1 is ... 192.168.1.1 dev lan4 scope link linkdown`
- `dnsmasq serves 192.168.1.101-192.168.1.200`、`dnsmasq servers: 1`
- 桥口映射照旧：`bridge port 5 is lan5`

**⚠️ 两条要注意的**：
- `learn_ont` **只在开机时做一次**（它在那个 5 秒自愈循环之外）。所以**开机时没有
  光猫 ⇒ 永远学不到它的 MAC**；光猫后来插回去，也要**再引导一次板子**。
- PC 侧**只 `ipconfig /renew` 可能停在 APIPA**（看起来像板子不给租约）。用
  **release + renew** 两步。

**三窗口（目标换成板子自己）**：

```bash
cd /c/UGit/openwrt/SK-D840N
SKD_TARGET=192.168.1.100 SKD_BIND=192.168.1.141 sh staging/ctl/lanerate-lablan.sh
```

2026-10-30 10:52 的实测：ping **25/25**；XMAC0 帧计数 `nppt+0x142400` 静默窗
**+51**、突发窗 **+127**；字节计数 `nppt+0x142420` 静默 **+4318**、突发 **+11837**。
（`lan5` 的 `rx_packets` 仍然不动 —— 那个驱动没有 per-port 的 rx 统计。）

**大流量（这条线能不能扛住）**：

```bash
# PC 侧准备一个 500 MiB 的文件，HTTP 服务已经在跑（staging/trial 就是它的根）
fsutil file createnew C:\UGit\openwrt\SK-D840N\staging\trial\bulk500.bin 524288000

# 板上：板子当客户端，从 PC 拉
time wget -q -O /dev/null http://192.168.1.141:8000/bulk500.bin
# 板子当服务端，PC 往里灌：
#   PC: pwsh -File staging/ctl/recvbulk.ps1 -Port 9000 -Seconds 12
#   板: time nc 192.168.1.141 9000 < /dev/zero
```

2026-10-30 10:49 的实测：板子 ← PC **500 MiB / 10.32 s = 50.8 MB/s（406 Mbit/s）**，
`time` 的 `user 1.69s / sys 7.80s`；板子 → PC **695 MB / 12.00 s = 55.2 MB/s
（442 Mbit/s）**，`user 0.80s / sys 10.72s`。**两个方向的 CPU 都占掉 92–96% 的
墙钟 ⇒ 瓶颈是板子的 CPU，不是线。**

### 刷机：把 CI 的 flash release 写进板子的 flash（2026-10-30 11:09 实测）

**通道**：U-Boot 侧（`tools/ubflash.sh`）—— 本项目唯一验证过的刷机通道。
**闸门**：payload 先 padding + crc32 → 书面授权（`staging/ctl/flash.permit`）→ 才允许擦。

```bash
# 1) 取 flash release（tag 里带 run 号）；下面是 run 280
base=https://github.com/woodnaonly/zx279133-ponwrt-build/releases/download/skd840n_flash_280_40984125609
for f in flash-uImage flash-dtb.bin flash-rootfs.jffs2 sha256sums-release sha256sums; do
  curl -sfL -o staging/trial/$f $base/$f
done
# 2) 校验（sha256sums-release 是这份 flash 产物的清单）
sha256sum -c staging/trial/sha256sums-release   # 三份必须一字不差
# 3) 造 .runN 副本（授权闸门要拿它证明「授权的字节就是这次拉的字节」）
cp staging/trial/flash-uImage    staging/trial/flash-uImage.run280
cp staging/trial/flash-dtb.bin   staging/trial/flash-dtb.bin.run280
# 4) 先跑一次刷机器：它只做 padding，然后停在「没有授权」那里，不碰板子
sh tools/ubflash.sh 280
# 5) 写书面授权（第三段是 owner 的决定，原文写进去）
sh tools/permit-waiver.sh 280 "dtb kernel root" "<owner 决定>"
# 6) 真刷
STAGES="dtb kernel root" sh tools/ubflash.sh 280
```

**PC 侧要先给有线口 TFTP 地址**（板子 U-Boot 的 serverip 是 192.168.9.25）：
```powershell
New-NetIPAddress -InterfaceIndex 19 -IPAddress 192.168.9.25 -PrefixLength 24
```

**刷完从 flash 起来**（U-Boot **没有 `boot` 命令**，它的 bootcmd 是三条，拆开发）：
```
mtd read kernel 0x88000000
mtd read dtb 0x88800000
bootm 0x88000000 - 0x88800000
```

**三段各自验的内容**（`tools/ubflash.sh` 自己做的）：擦后读分区头/尾各 4 KiB 必须是 `0xFF`
（crc `f154670a`），写后整段回读的 crc32 必须等于 padded 文件的 crc32。
**`boot` 与 `parameter tags`（GPON 身份）全程不碰。**

**2026-10-30 的实测**：dtb crc `cfda7eac`、kernel crc `de8eefbd`、root crc `84b9bb1b`，
全部通过；起来后 `lan5` 在 DSA 树里（flash 里原来那个 10-23 内核没有）、`br-lan = 192.168.9.1/24`、
`lan5` speed 2500 / up，PC 侧 `ping 192.168.9.1` 3/3 <1 ms。

**⚠️ 两条**：
- **插座断电节奏 vs 擦写时间**。智能插座每 10–30 分钟断一次，断在擦写中间就是一个
  写坏的分区（可恢复：三段都有回读校验，断了重跑那一段就行；kernel/dtb 的回滚件是
  `staging/kernel_stock.bin` + `staging/dtb_stock.bin`，root 的回滚件是 `staging/rootfs.jffs2`）。
- **flash 版的 rootfs 不带 lablan 脚本**（CI 里 `trial-files/` 只进 initramfs 镜像）。
  「PC 通过板子访问 192.168.1.1 的光猫」这件事，flash 版做不到 —— 那是 RAM 版 trial 镜像的能力。

### 让板子自己从 flash 起来：relay 的 `ser-nostop` 开关（2026-10-30 12:33 起）

**症状**：智能插座每 10–30 分钟断一次电；每次回来板子都停在 U-Boot 的 `=>`，
不自己引导。**原因是我们自己的串口 relay** —— `tools/serrelay.ps1` 在 autoboot
倒计时窗口里发空格（空格就是按键），倒计时被打断，bootcmd 就没跑。

板子 flash 里的 `bootcmd` **本来就是对的**（2026-10-30 11:38 实测）：

```
mtd read kernel 0x88000000; mtd read dtb 0x88800000; bootm 0x88000000 - 0x88800000
```

**修法**（零代码；relay 每轮都 `Test-Path`，建了立刻生效）：

```powershell
New-Item -ItemType File -Path 'C:\UGit\openwrt\SK-D840N\staging\ctl\ser-nostop' -Force
```

**回滚 / 怎么再拿到 `=>` 提示符**：把这个文件**改名让位**（本机策略拦 `Remove-Item`，
改名更稳）—— 例如 `Rename-Item ser-nostop ser-nostop.disabled`；
relay 下一轮（≈1 s）就不再发空格，再复位板子就有了。
**刷机需要提示符 ⇒ 刷机前先改名让位（`ser-nostop.disabled`），刷完再改回 `ser-nostop`。**

```powershell
Rename-Item 'C:\UGit\openwrt\SK-D840N\staging\ctl\ser-nostop' 'ser-nostop.disabled'
Rename-Item 'C:\UGit\openwrt\SK-D840N\staging\ctl\ser-nostop.disabled' 'ser-nostop'
```

**为什么删/改名是有效的**：`tools/serrelay.ps1` 第 145 行每一轮都 `Test-Path` 那个文件，
文件在位 ⇒ **一个空格都不发**（连换行也不发）；文件不在位 ⇒ 发空格，
空格就是按键，把 autoboot 倒计时打断，板子就停在 `=>`。
第 134–139 行还有一层：loader 一旦回了 `=>`，phase 变成 `prompt`，此后连空格都不发了。

### relay 掉线怎么发现、怎么拉起来（2026-10-30 14:38 实测）

**症状**：MCP 的 `board_state` 答 `unknown`，`bench_status` 的 markers 里出现
`=== relay down 14:38:37 ===`，`relay.log_age_s` 不再增长。

**为什么要紧**：relay 死了 ⇒ **没有人往串口发空格** ⇒ 下一次插座断电时，
板子会自己走完 autoboot 倒计时并**从 flash 引导**（不是停在 `=>`）。
刷机需要 `=>` 提示符，所以 relay 掉线期间断电，板子就不在刷机位上。

**拉起来**（`Start-Process` 让它脱离本轮命令，否则命令一结束它也跟着死）：

```powershell
$p = Start-Process -FilePath 'C:\Program Files\PowerShell\7\pwsh.exe' `
  -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',
                'C:\UGit\openwrt\SK-D840N\tools\serrelay.ps1','-Seconds','21600' `
  -WorkingDirectory 'C:\UGit\openwrt\SK-D840N' -WindowStyle Hidden -PassThru
```

`-WorkingDirectory` 必须是工作区根目录：`serrelay.ps1` 的相对路径（`staging/ctl/...`）
是按 `Get-Location` 算的。拉起来之后 markers 里应当出现
`=== relay up COM3 @115200 8N1 <时间> ===`。

**顺手把待发命令清掉**：relay 死之前 `staging/ctl/sercmd.txt` 里可能还压着一条命令，
relay 一活就会把它发出去（刷机时这会插进 U-Boot 的命令行里）。
`Rename-Item sercmd.txt sercmd.txt.stale` 把它挪开即可（本机策略拦 `Remove-Item`）。

**插座断电的节奏**（2026-10-30 实测）：每 **20 分 30 秒**一次，断电 **15 秒**。
实测时刻：10:36:06 / 10:56:37 / 11:17:07 / 11:37:38 / 11:58:08 / 12:18:39 /
12:39:09 / 12:59:40 / 13:20:11 / 13:40:41 / 14:01:11 / 14:21:41 / 14:42:11。
⇒ **刷机要挑一个断电之后的窗口**：断电后 15 秒起、到下一次断电前 9 分钟止，
刷三段大约要 3–5 分钟。

### 2.5G 口的链路查法：远端 PHY 的 SerDes 档位（2026-10-30 12:39 定案）

**症状**：面板 LAN1（2.5G 丝印口）在刷机版板子上永远不通 —— PC 的 ping 全丢、
ARP 是 `Incomplete`；面板 LAN2/3/4 正常。

**根因**：这条 lane 的远端是外挂的 RTL8226（MDIO controller #1 的地址 5）。
它的 SerDes 档位寄存器**只有厂商 U-Boot 的 net bring-up 会写**，而 net bring-up
只在 loader 被给了网络时才跑 —— 从 flash 自己起来的板子没有这一步，RTL8226
就停在出厂的 2.5G 档，而 SoC 侧是 1G 档（`Uni SerDes 1G profile written: mode 0x101`）
⇒ 两端对不上，base-X 永不锁定。

**三个要看的寄存器**（devad `0x1e`）：

| 寄存器 | 出厂默认 | U-Boot 的 1G 档 | 备注 |
| --- | --- | --- | --- |
| `0x75f3` | `0x030d` | `0x0002` | |
| `0x697a` | `0x0003` | `0x0000` | 低 6 位 = SerDes 档 |
| `0x6a04` | `0x0503` | `0x0403` | |

`0x6f10` / `0x6f11`（出厂 `0x084a` / `0x0021`）**loader 没动，不要碰**；
`0x7588` / `0x7589` / `0x7587` 也不要碰。

**两个判读位**：

- `0x758d` 的 **bit 2** = lane 在不在送码：`0x801e` 通、`0x8018` 不通、冷启动 `0x0010`。
- `0x7580` 的**低 6 位** = SerDes 档：`0x02` = 1000BASE-X（本 lane 要的），
  `0x12` / `0x16` = 2.5G 档。

**读**（板上的 debugfs 控制台，`zx279133-np.c` 的动词，走真 Clause-45，
不经过 phylib 的 C22 路径 —— 那条路对这颗芯片的 devad `0x1e` 只回 `0xdead`）：

```
echo 'm45 5 0x1e 0x758d' > /sys/kernel/debug/zx279133-9132
```

**写**（手工验证用；出厂值随时可写回去）：

```
echo 'm45w 5 0x1e 0x75f3 0x0002' > /sys/kernel/debug/zx279133-9132
echo 'm45w 5 0x1e 0x697a 0x0000' > /sys/kernel/debug/zx279133-9132
echo 'm45w 5 0x1e 0x6a04 0x0403' > /sys/kernel/debug/zx279133-9132
```

写完在 PC 上 `ping 192.168.9.1`：通了就说明这条 lane 只差这三个寄存器
（2026-10-30 12:29–12:32 实测：写出厂值 → 0/10；写 1G 档 → 15/15）。

**U-Boot 侧的对照**（loader 的 net init 一跑就把 RTL8226 配好了，这也是为什么
经 TFTP 引导的 RAM 版镜像一直是通的）：

```
setenv ipaddr 192.168.9.1
ping 192.168.9.25        # 这一步会触发 net init
```

日志里应当出现 `mode_eth_1gbase_x_cfg` → `uni serdes init succeed` →
`xmac 0 speed set 2 in sgmii(xmac speed 3)` → `mac 4 phy status changed: 1000M
full-duplex` → `host 192.168.9.25 is alive`。

### 刷机之后的全自动验收清单（2026-10-30 定版）

**第 0 步 · 前提**：`staging/ctl/ser-nostop` **在位**（relay 一个空格都不发），
插座断电后板子才自己走完 autoboot 倒计时并从 flash 引导。
刷机需要 `=>` 提示符 ⇒ **刷机前先把它改名让位**（`ser-nostop.disabled`），**刷完改回来**。

1. **板子自己从 flash 起来**：插座断电 15 s → 板子自动走完倒计时 → 串口出现
   `Linux version 6.18.38+`，**且没有停在 `=>`**。
2. **LAN1 冷启动自通**（同一块板、同一根网线、同一个 PC 口）：
   - 板上 dmesg 出现 `the 2.5G jack's PHY: the SerDes option is set to the 1G class`
     —— 这一行就是「出厂档被改回 1G 档」；
   - 板上 `echo 'm45 5 0x1e 0x758d' > /sys/kernel/debug/zx279133-9132` = **`0x801e`**（bit 2 = 1，lane 在送码）；
   - PC 侧 `ping 192.168.9.1` **25/25**。
3. **自愈**（验的是驱动自己会修，不是人会修）：
   板上把三个寄存器写回出厂档 ——
   `m45w 5 0x1e 0x75f3 0x030d`、`m45w 5 0x1e 0x697a 0x0003`、`m45w 5 0x1e 0x6a04 0x0503`；
   链路随即断（`0x758d` 的 bit 2 变 0、ping 全丢）；
   **≤ 2 s 内** dmesg 要再出现一次 `the SerDes option is set to the 1G class`，
   `0x758d` 回到 `0x801e`，ping 恢复。
4. **拔电再上电 ⇒ 板子自己起来**（同第 1 条），并且第 2 条自动成立。
5. **LuCI 与「进系统」**：`http://192.168.9.1/` = **200**；
   `/cgi-bin/luci` = **403 + `x-luci-login-required: yes` + 完整登录页**
   （**这是 LuCI 未登录时的正常应答，不是坏了**）；
   `tcp/22` 有握手（证明跑的是 Linux，不是停在 U-Boot —— 那个 U-Boot 也会回答 ping）。
6. **10 分钟稳定性**：连跑 10 分钟，lane 不掉档（覆盖 11:25 那次「128 s 后自己掉档」的事件）。
7. **面板灯**：四个 LAN 灯走 **SoC 的 LED 引擎**，mainline **没有**那个引擎的驱动 ⇒
   **即使端口上了链路，这四个灯也必然是灭的**（dts 里已经写明这一点，它是 mainline 支持的缺口，
   不是刷机失败）。面板上**电源灯（gpio-10）与 PON 灯（gpio-63）是 GPIO 驱动的**，
   可以当「板子确实起来了」的证据。**LAN1 灯的亮灭要 owner 当场看面板记**。

**量具**：`tools/acceptlan1.ps1`（PC 侧：ping / tcp22 / http / ARP）＋
板上 debugfs 控制台的 `m45` / `m45w`。
PC 侧只用以太网适配器 ifIndex 19（192.168.9.25/24 静态），**WLAN 不动**。

> ⚠️ **判 ping 的时候要用对 API**（2026-10-30 15:07 实测踩到）。
>
> Windows PowerShell 5.1 的 `Test-Connection` 返回 `StatusCode`（int，0 是好）
> 和 `ResponseTime`；**PowerShell 7 返回的是 `Status`（字符串 `"Success"`）和
> `Latency`**。只认一种的脚本会把**通的链路读成死的** ——
> 15:07 那一次就是这样：板子在 LAN1 上答 TCP、答 HTTP，`arp` 也是
> `Reachable`，而脚本报 `ping: 0/25 replies - THE LINK DID NOT ANSWER`。
> 同一时刻 `ping.exe -n 10 192.168.9.1` 是 10/10、1–2 ms。
>
> ⇒ **拿 `ping.exe` 复核一次再下结论**；脚本里两种字段都认。

> ⚠️ **刷机时 `tftpboot` 会触发 loader 的 net init**（2026-10-30 15:03 实测）。
>
> `tools/ubflash.sh` 用 `tftpboot` 取镜像，**这一步会让厂商 U-Boot 跑它的
> net bring-up**，于是 **RTL8226 被 loader 配成了 1G 档**（`0x697a=0x0000`、
> `0x7580=0x9402`、`0x758d=0x801e`，`nppt 84=0x00000001`）。
> 刷完立刻 `run bootcmd` 引导，看到的链路是通的 —— **但那不是驱动的功劳**，
> 是 loader 的。⇒ **要验驱动自己修，必须做一次没有人碰网络的冷启动**
> （`ser-nostop` 在位 + 插座断电），那一次 loader 不会跑 net init。
> 判据是 dmesg 里出现 `the 2.5G jack's PHY: the SerDes option is set to the 1G class`。

