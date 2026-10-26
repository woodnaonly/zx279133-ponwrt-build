# 90 — the CPU sent every LAN frame out one jack, and the fix is one field

Date: 2026-10-26. Kernel: mainline 6.18.38+ / ImmortalWrt, CI run **185**
(`skd840n_flash_185_40257525883`, `skd840n_initramfs_185_40257525883`).
Commits: `f5df6d8` (the driver), `b885533` (the two bench tools), `e466768` (`q.py`).

## 1. What the owner reported

After run 182 the board booted from flash and routed, but on the panel:

> 现在只有 lan2 口是对的，lan1 2.5g lan3 lan4 1g 都不能用，现在网线插在 lan2

with the symptom, when asked, as **「连不上，灯是亮的」** and **「是，只换板上口」** - the same
cable, the same PC port, only the board's jack changing. Three of four LAN jacks linked and
answered nothing.

That is a strange combination, and it is the whole story: a link LED on these jacks is the
9132's own PHY auto-negotiating after reset, which needs no driver at all. So the lamps said
"the PHY is up" while the datapath said otherwise.

## 2. Where it was, in the code

The IDM TX descriptor names an output port: byte `0x0a` holds the port in bits 0..5 and the
"output port valid" flag in bit 6 (`ZX279133_IDM_TX_CONTROL = 0x00400000`). The vendor's own
LAN path writes the *target jack's* chip port there, 1..4. We wrote a single scalar:

```c
	tx_port = ndev != hw_ndev ? eth->lan_tx_port : zx279133_tx_port;   /* netdev.c:367 */
	...
	metadata0 = ZX279133_IDM_TX_CONTROL | (tx_port & 0xff) << 16;      /* netdev.c:397 */
```

with `eth->lan_tx_port = ZX279133_LAN_TX_PORT` and `#define ZX279133_LAN_TX_PORT 2`
(`zx279133.h:36`, `zx279133-main.c:820`). So **every frame the CPU sent to a LAN jack was
forced out port 2**, valid flag asserted, whatever the bridge and DSA had decided.

The destination was not actually unknown: the DSA tagger puts it in the frame -
`VID = TRANSPORT_VID_BASE(55) + dp->index`, and the four user ports are DSA ports 4..7, so an
egress frame arrives on the conduit carrying VID 59..62 = PPU 1..4 = `lan1..lan4`.
`zx279133_lan_strip_transport_vlan()` decoded that VID (netdev.c:1799) to take the tag off for
the wire, and then discarded it.

So the fix was not to learn anything new. It was to stop throwing away what the code had
already read.

## 3. The measurement that pinned it, before changing any code

The driver already exports a live knob for this field: `echo "txp N" >
/sys/kernel/debug/zx279133-9132` (`zx279133-np.c:1538`). No rebuild, no flash write.
`tools/txpwalk.sh` sweeps it, with the console as the control channel and the PC as the sensor:

```
=== txpwalk 2026-10-26 09:56 uptime=409s ports: 1 2 3 4
carrier: lan1=0 lan2=0 lan3=0 lan4=1
txp=1  pc->board=0/4  board->pc: 4 transmitted, 0 received, 100% packet loss
txp=2  pc->board=0/4  board->pc: 4 transmitted, 0 received, 100% packet loss
txp=3  pc->board=0/4  board->pc: 4 transmitted, 0 received, 100% packet loss
txp=4  pc->board=4/4  board->pc: 4 transmitted, 4 received,   0% packet loss
```

Reproduced identically at 10:00 with all four jacks in `br-lan`. One value answers, exactly
one, and the one that answers is the jack the cable is in. That is positive *and* negative
control in the same table, on the image that was already flashed.

## 4. The fix

`zx279133-netdev.c`: the strip function reports the VID it stripped, the LAN xmit passes the
resulting chip port into the common xmit, and the common xmit prefers the per-frame value and
falls back to `eth->lan_tx_port` only when the frame carries no transport VID:

```c
	if (ndev == hw_ndev)
		tx_port = zx279133_tx_port;
	else
		tx_port = lan_out_port ? lan_out_port : eth->lan_tx_port;
```

`*out_port = vid - (ZX279133_LAN_TRANSPORT_VID_MIN - ZX279133_9132_UNI_PPU_FIRST)` = `vid - 58`,
range 1..4. Frames the tagger never tagged keep the old behaviour, so `txp` remains the escape
hatch it was. Nothing else moved: no descriptor format change, no tagger change, no DTS change.

Two stale comments went with it - `zx279133-np.c`'s `txp` help said "this driver writes 5
(xmac_0)" while the compiled default was 2, and the field comment in `zx279133.h` said the same
thing about a value that was never 5. `zx279133-idm.c:370`'s `port == ZX279133_LAN_TX_PORT` was
read and left alone: `zx279133_idm_rx_lan_vport()` already maps PPU 1..4 to 59..62, so the term
is redundant, and "redundant" is not the same as "wrong".

## 5. Acceptance, on flash

The RAM kit was booted first (`tftpboot 0x89000000 sk-d840n-initramfs.itb; bootm 0x89000000`,
zero flash writes; `Bytes transferred = 12099924` is what proves the tftp root held *this* run's
file - the previous one is 32 bytes smaller). It passed there, then `kernel`+`dtb`+`root` were
programmed from inside that RAM environment in the order root, dtb, kernel, each read back:

```
mtd2 kernel (8068834 B)  b7c16ab730e0303c0aa69f23e8e1e774   == the image
mtd3 dtb    (13040 B)    8c460c9794558ac7e18bcb18d903a228   == the image
mtd5 root   (4718596 B)  9ab070bfe165f7e840d04b6b0a5f22c1   == the image
```

Then, on the flashed system, `staging/ctl/jackok.sh` - which prints the carrier map, the PC's
ping, the board's own ping and *the number of times anyone wrote the debugfs knob this boot*:

| jack | carrier | pc-&gt;board | board-&gt;pc | `txp` writes |
|---|---|---|---|---|
| LAN2 | `lan2=1` | 4/4 | 4/4, 0% loss | **0** |
| LAN3 | `lan3=1` | 4/4 | 4/4, 0% loss | **0** |
| LAN4 | `lan4=1` | 4/4 | 4/4, 0% loss | **0** |

`txp-writes=0` is the column that makes the other three mean something: the descriptor still
held the compiled default 2, and LAN4 - which used to need `txp 4` by hand - answered anyway.

No regression either: two manual cold boots (power on, nothing sent to the console) each gave
`g4: summary 16 pass / 0 fail` - including "the partition holds the image" - and
`verify: summary 5 pass / 0 fail`, and the NAT round came back `natround rc=0` with
`natverdict: PASS (the masquerade counted 2 packets / a ping behind the board came back with
0% loss / conntrack carries the client in 21 place(s))`.

## 6. The panel, mapped

| silkscreen | netdev | what it is |
|---|---|---|
| LAN1 | `eth0` | the 2.5G jack: RTL8226 on `14f02000.mdio:05`, NPPT + sgmii |
| LAN2 | `lan2` | 9132 UNI, PPU 2 / smac_1 / PHY 0x0b |
| LAN3 | `lan3` | 9132 UNI, PPU 3 / smac_2 / PHY 0x0c |
| LAN4 | `lan4` | 9132 UNI, PPU 4 / smac_3 / PHY 0x0d |

Three consequences worth writing down, because two of them contradict things this project
believed:

- **`lan1` has no jack on this SKU.** The 9132's first UNI is in the DTS and in the driver and
  on the panel there is nothing to plug into; that is why `txp 1` answered nothing in §3.
- **"eth0 = panel jack 4" is wrong** and it is what the earlier jack walks recorded. The 2.5G
  jack is LAN1. The walk that produced "jack 4" was done on an image where only `eth0` could
  send, so the numbering was never anchored to the silkscreen.
- **`eth0`'s carrier is a lie.** It reports `carrier=1` and `Link is Up - 1Gbps/Full` with no
  cable in it, because the board DTS gives it `fixed-link`. Every "is this the 2.5G jack"
  question has to be answered with traffic, not with `carrier`.

And the 2.5G lane is still dead, measured cleanly for the first time on the fixed kernel: with
the cable in LAN1, all four `lan*` at `carrier=0`, `eth0` given `192.168.9.2/24` at runtime,
the PC got **0/4** and the board's own counters read **`tx_bytes=1106, rx_bytes=0`** - the CPU
handed frames to the netdev and nothing came out. That is the old `eth0`/XMAC/uni-serdes
problem, unchanged by this fix and out of its scope.

## 7. Bench defects found on the way, all of them real

1. **`tools/gettrial.sh`'s run pin could never match.** It compared the *last* field of the tag
   against the run number, and the tag is `skd840n_<mode>_<run>_<release-id>`, so
   `gettrial.sh initramfs 185` answered "no published release carries trial-set.tgz" for a
   release that existed. Worked around by pinning nothing (newest = the run wanted);
   `tools/getitb.sh` matches on the third field instead.
2. **This PC's python decodes as GBK.** `json.load(sys.stdin)` on a complete 906 KB GitHub
   answer raised `Unterminated string starting at: line 4630 column 13`, which looks exactly
   like a truncated download. It is not: the pipe is read with the locale codec. Anything
   parsing non-ASCII JSON here must open the file with `encoding='utf-8'`.
3. **`q.py` read a live console as a dead one, and I asked the owner to re-seat hardware that
   was fine.** OpenWrt boots with `askfirst`: until something reads the tty there is no shell
   and **the line discipline echoes nothing**, so every send looked like a byte that never
   arrived. Two relay restarts did nothing, because the relay was healthy - `Get-PnpDevice`
   shows the CH340 continuously present since 10-23, and the `=== relay down 11:06:24 ===` I
   read as a hardware event was my own `relayctl stop`. `q.py` now hands the console a couple
   of CRs on its second attempt and says what a text-less exchange may mean.
4. **A LAN-egress sweep cannot be driven over ssh.** I ran the §3 sweep inline over ssh once;
   the first value moved the egress away from the cable, which took the ssh session down with
   it, and the remaining four iterations printed 0/4 for a value that demonstrably gave 4/4.
   That table was void, not evidence. The tool's header said this; the inline version ignored
   it.
5. **Windows ICS steals the bench address.** Turning the sharing on rewrites the wired adapter
   to `192.168.137.1` and drops the manual `192.168.9.25`, which `natround.sh` only puts back
   in its last step. So the NAT round's topology is *the cable in the jack that is about to
   become WAN* - the first re-run left the cable in LAN4, and the board then correctly failed
   to get a lease on an empty jack. Nothing was wrong with the image.
6. Two small ones: `portsall.sh`'s first version asked `timeout` to run a shell *function*
   (impossible - it silently never connected, so the script refused to run against a board that
   was answering), and its read-back originally tested a pipeline ending in `tail`, whose exit
   code is 0 on a dead board; it now tests the content.

## 8. Evidence files

- `staging/ctl/txpwalk.log` - §3, both sweeps.
- `staging/ctl/jackwalk.log` - the RAM-kit walk and the flash walk, one line per position.
- `staging/ctl/jackok.sh` - the four-column flash measurement.
- `staging/ctl/natround185b.txt` + `natverdict.py` output - the NAT round and its verdict.
- `staging/ctl/coldboot2.txt` - a full cold-boot gate, 16 + 5.
- `staging/ctl/ram185.log` - the RAM boot, including the byte-count check.
