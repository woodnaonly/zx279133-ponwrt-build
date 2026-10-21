# 33 — the M1 acceptance, end to end (2026-10-21)

Everything below is a script that already exists.  This is the order they
run in, what each gate means, and the two things that are easy to get wrong.

**Where the scripts live.**  `tools/...` in this document means the bench's
own `tools/` directory at the top of the workspace - the scripts that drive
the board from the PC.  The repository's `tools/` is the other one: the
release and image tooling that CI uses.  The bench scripts stay outside the
repository on purpose (docs/22, "仓库外（本机工具，不进镜像）"): they are
about *this* board on *this* bench, and they must never end up inside a
released image.  `staging/ctl/...` is the same thing: the scripts that talk
to the board over the serial console.

The physical setup is one cable: the PC is in the board's **UNI2** jack
(`lan2` in DSA numbering, ifindex 5).  That single cable is the LAN path the
PC's frames take into the 9132, and in the one-cable mode it is also the
board's WAN.

## G2 — the 9132 forwards

**Gate:** the PC's ping to the board's address comes back, and the counters
say where the frame went.

```
sh staging/ctl/go116.sh <run id>     # wait for the kit, fetch it, boot it
sh staging/ctl/accept5.sh            # the measurement
```

`accept5.sh` types four short commands into the board's console (the trial
kernel has no network path in, so the console is the only way to ask it
anything) and reads the answers out of the serial log:

| what it asks | what it means |
|---|---|
| `lan-cpu0` rx | frames the 9132 handed to the CPU.  Zero means the chip did not trap them. |
| `lan2` rx | frames the 9132 took off the PC's cable.  Zero means the PC's frames never reached the switch. |
| `lane tx frames` | frames the SoC put on the wire towards the 9132's CPU port.  This is the SoC's own MAC. |
| `eram block ...` | what the driver actually left in the ERAM, so that "the tables are wrong" and "the tables are right and the frames are lost anyway" can be told apart. |

The three outcomes that matter:

* `lan2` rx = 0 - the PC's frames never arrive.  The PHY, the port enable or
  the DSA mapping, not the tables.
* `lan2` rx > 0 and `lan-cpu0` rx = 0 - the frames arrive and are not
  trapped.  The protocol action table (0x18) or the CPU path tables
  (0x19, 0x22, 0x27).
* `lan-cpu0` rx > 0 and the ping still fails - the frames reach the CPU and
  the reply does not come back.  The flood group, the MAC table, or the
  board's own routing.

**Everything in G2 was checked against the vendor's own bytes first** - see
docs/32 - so the tables are not the suspect any more.

## G3 — the board is a router on one cable

**Gate:** DHCP + DNS + NAT + a client behind the router reaching the
internet, all on the one cable.

```
pwsh -File tools/labnat-upstream.ps1
```

**Disarm the canary first.**  The trial image reboots itself 300 s after
userspace, so that "no answer" can be read as "it booted, this port is not
it".  The NAT acceptance needs longer than that, and the RAM image's `/etc`
is wiped by the reboot anyway - so the canary has to be taken out of the
way, and the image says how: touch `/tmp/trial-noreboot` and the canary
subshell exits instead of rebooting.  Over the serial console, in the same
window where the G2 measurement is taken:

```
touch /tmp/trial-noreboot
```

(touch it again, or remove it, when the round is over and the canary should
be back.)

**Use `pwsh` 7, not `powershell` (Windows PowerShell 5.1).**  The scripts
carry the adapter's Chinese name (以太网) and 5.1 reads a file without a BOM
as the system code page, which does not parse.  This was measured, not
guessed: `powershell -File tools/labupstream.ps1` fails with "Missing ')' in
function parameter list".

What the script does, and each step's gate:

1. **ICS on** - `tools/labupstream.ps1 -Mode on` makes the PC the upstream:
   the Wi-Fi is the public side, the wired adapter the private one, which
   gives it 192.168.137.1 and a DHCP server.  The bench's own 192.168.9.25 is
   kept as a second address on that adapter, so the board stays reachable
   while its WAN port is the port the PC is in.
   *Measured on this PC: the round trip works - on gives the wired adapter
   192.168.137.1/24 and takes 192.168.9.25 away; off gives 192.168.9.25 and
   192.168.1.25 back, with the default route through the board restored.*
2. **the board's WAN** - `/etc/labwan` is set to `pc`, which S26nat reads:
   the port the PC is in leaves the bridge and becomes the WAN.
3. **the lease** - the board is found on the shared subnet **from this
   side** - from the neighbour cache, or by probing 192.168.137.2..40 - not
   by asking the board, because the board's LAN address stops answering the
   moment its WAN port is the port the PC is in.
4. **the evidence** - the client's lease behind the router, the conntrack
   entry, the `nft` masquerade counters, the WAN interface's byte counter,
   and `ip_forward`.
5. **ICS off** - this PC is put back the way it was.

The client behind the router is the board's own: S27labclient makes a veth
pair and a network namespace, and the namespace is the client.  Its traffic
to the PC goes out through the board's own NAT and then through the PC's
ICS NAT - and, on the way out, **across the 9132**, which is why G3 cannot
be done before G2.

## A known risk in G3, found while reading the vendor's flood setup

The one-cable mode needs the board to **send** a broadcast: its DHCP
discover, from the CPU port out towards the jack the PC is in.  That is the
egress direction, and the protocol action table (0x18) is about the ingress
direction - it is what traps a frame *to* the CPU - so 0x18 does not help
here.

The driver does write the vendor's per-port flood words: for every PPU it
writes SPA indirect registers 17 and 18 - the broadcast flood word and the
unknown-unicast / multicast flood word - with the values the vendor's own
system has.  Decoded, the broadcast destinations are:

| PPU | port | broadcast goes to | unknown multicast goes to |
|-----|------|-------------------|---------------------------|
| 1 | UNI1 | 4, 10, 22, 26 | 6, 11, 22 |
| 2 | UNI2 | 4, 10, 23, 26 | 7, 11, 23 |
| 3 | UNI3 | 4, 10, 22, 23, 26 | 6, 7, 11, 22, 23 |
| 4 | UNI4 | 4, 10, 24, 26 | 8, 11, 24 |
| 5 | the CPU port (XMAC0) | 4, 10, 22, 24, 26 | 6, 8, 11, 22, 24 |

which is the vendor's own configuration, copied verbatim.  Note that the
CPU port's broadcast set does not contain every UNI port's own bit, so
whether the board's DHCP discover reaches the jack the PC is in depends on
which logical port UNI2 is - a question the board, not the vendor's code,
has to answer.

What the driver does **not** write is the flood *group*: the vendor's
`sw_init_flood_ctrl()` (switch.ko 0x2780) also builds a destination port
list and turns the flood on and off per port -

```
    multicast flood:  dest port list = { the WAN port } + { every port 0..7
                       that is not the WAN port },  flood enabled on every
                       one of those, disabled on the WAN port
    unknown-DA flood: the same shape
    broadcast flood:  the same shape
```

- and the per-port group ids behind that list are values the vendor only
ever sets at run time, so they are not knowable offline.

What the 9132 does after a reset with nobody writing the flood group is not
known from the vendor's code.  So:

* if the board's DHCP discover reaches the PC, the question is settled and
  nothing has to be written;
* if it does not, **this is the first thing to look at**, and the change is
  bounded: the four vendor API calls above, in the vendor's own order, and
  the per-port group ids (readable off a vendor board, not offline).

The G2 gate does not depend on it: the PC's frames reach the CPU by being
trapped (0x18), and the board's reply is a unicast to a MAC the 9132 has
learned.

## G4 — the flash

**Gate:** two cold boots, `uname -r` = 6.18.38, the pinned cmdline, the
mainline `/proc/mtd`, `verify_router.sh` 5/5, and a jffs2 with no dirent
damage.  And the way back must still work.

```
sh tools/gettrial.sh flash             # the three images and the manifest
sh tools/flashpush.sh --write          # from the RAM environment
# two power cycles, then:
sh tools/postflash.sh
```

**The flash has to be written from the RAM environment.**  The script refuses
to run against a jffs2 `/` - erasing the flash under a mounted jffs2 oopses
the kernel.  On a trial kernel the root is an initramfs and `/tmp` is RAM,
so the images are copied there and written from there.

The write order is root, dtb, kernel: until the kernel is replaced the box
still boots what it boots now, so an early mistake is recoverable.  The
rollback is `restore_set/` (the factory's five pieces) plus
`backups/root-20261020-2045.bin.gz`, and the U-Boot/tftp serial chain is
already working.

## The order, in one line

```
G2 (go116.sh + accept5.sh)  ->  G3 (labnat-upstream.ps1)  ->  G4 (gettrial.sh flash + flashpush.sh --write + postflash.sh)
```

and the first thing to check when one of them fails is which of the three
gates it failed at, because each one has its own counters and its own
evidence.
