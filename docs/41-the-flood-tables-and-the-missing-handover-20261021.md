# The live board, the 9132's flood tables, and what is still missing between the SoC and the chip

Written 2026-10-21, after the live-board round that followed `docs/40`.  Everything
below was measured on the board in front of us, or read out of the vendor's own
modules; nothing here is guessed.

---

## 0. The short version

| question | answer |
|---|---|
| What does SPA register 17 hold? | the port's **broadcast** flood word: its low half is the flood-count fields, its high half is the **broadcast flood group id** |
| What does SPA register 18 hold? | the port's **multicast / unknown-DA** flood word: its low half is the **multicast group id**, its high half is the **unknown-DA group id** |
| What are the group ids of a port p? | `p<<6` (unknown-DA), `(p+0x10)<<6` (broadcast), `(p+0x20)<<6` (multicast) - all three are read out of the live system's own registers |
| Where does the switch engine read them from? | ERAM table **0x7d**, one 128-bit entry per SPA memory id: word1 bytes 4..5 = the multicast group id, word1 bytes 6..7 = the unknown-DA group id, **word2 bytes 10..11 = the broadcast group id** |
| Was that right in this driver? | **no** - the broadcast group id was copied into the entry's *high* half, where no vendor setter ever puts it, so the broadcast flood ran on group `0x0410`, which is not a group id at all.  **Fixed in run120.** |
| What is the vendor's IDM TX destination port id on the LAN path? | **1** (`smac_0`), never 5 (`xmac_0`, the 9132's own CPU port) - see `staging/ctl/re-txport.md` |
| What does this driver write? | **5** - unchanged so far, but now a runtime knob (`txp`) |
| Did untagging the egress frames fix the data plane? | **no** - measured, see section 4 |
| Did pointing the CPU port's flood groups at PPU 2 fix it? | **no** - measured, see section 4 |
| Did clearing the CPU port's protocol-trap row fix it? | **no** - measured, see section 4 |
| Where do the frames stop? | **before the 9132's forwarding engine** - the frames leave the SoC (the IDM's TX done counter runs, the DSA netdev's tx counter runs) and **nothing comes back on any UNI port** (`lan2 rx = 0`) |

---

## 1. Reading the flood words

The vendor's own working system was read over the vendor's read-only
`tm_debug` interface (`echo -r spa_port_attr_smac_0 id=0x0 cnt=0xa >
/proc/pon/tm_debug/module_ind_ram`), and the raw values are in
`staging/ctl/vlive5.txt`:

```
ram id 1 (smac_0)   reg17 = 0x04400410   reg18 = 0x00400840
ram id 2 (smac_1)   reg17 = 0x04800410   reg18 = 0x00800880
ram id 3 (smac_2)   reg17 = 0x04c00410   reg18 = 0x00c008c0
ram id 4 (smac_3)   reg17 = 0x05000410   reg18 = 0x01000900
ram id 5 (xmac_0)   reg17 = 0x05400410   reg18 = 0x01400940   <- the CPU port
```

Every other word of all five ports is identical, and all five match this
driver's own values bit for bit (`docs/40`).

The two flood words decode as follows, and the pattern holds for all five
ports, which is what makes it an interpretation rather than a coincidence:

```
reg18 high half = p<<6           (the unknown-DA group id)
reg18 low  half = (p+0x20)<<6    (the multicast group id)
reg17 high half = (p+0x10)<<6    (the broadcast group id)
reg17 low  half = 0x0410         (the same on every port: the flood-count fields)
```

For the CPU port (p = 5) that gives the three group ids **320**, **1344** and
**2368**.

## 2. Where those group ids have to land

The vendor's own layout of one entry of the MF extension table (ERAM table
0x7d, one 128-bit entry per SPA memory id) is in
`staging/ctl/re-eram-7d.md` section 2.4, and every field in it is located by
the vendor's own setter:

```
word1 bytes 4..5    the multicast flood group id     port_attr_def_mul_flood_group_id_set
word1 bytes 6..7    the unknown-DA flood group id    port_attr_def_unk_da_flood_group_id_set
word2 bytes 10..11  the broadcast flood group id     port_attr_def_brd_flood_group_id_set
```

Word 1 is therefore SPA register 18 as it stands - the multicast id in the low
half, the unknown-DA id in the high half, exactly where the entry wants them.
Word 2 is SPA register 17, whose broadcast group id sits in the register's
*high* half, while the entry wants it in the *low* half.  Copying the register
over unchanged therefore put the broadcast group id into the entry's byte
12..13, where no setter ever writes, and left the broadcast flood of every
port running on group `0x0410` - a number that is not a group id at all.

That is fixed in run120: the halves are swapped on the way in.

## 3. The vendor's own IDM TX descriptor

`staging/ctl/re-txport.md` (650 lines, the vendor's `np_133.ko` disassembled)
settles two things that had been open:

* the descriptor's byte 0x0a - the vendor's own `dump_desc_tx()` names it -
  holds the **output port** in bits 0..5 and the **output port valid** flag in
  bit 6.  This driver's layout is the same: `ZX279133_IDM_TX_CONTROL`
  (`0x00400000`) is bit 22, and `(tx_port & 0xff) << 16` is the port;
* the vendor's **LAN** path writes **1** there - `smac_0`, the first UNI jack -
  and never 5, which is `xmac_0`, the 9132's own CPU port.  The vendor's own
  PON path writes 6 (`pon_0`) with the valid flag clear.

So the one value this driver has always written on the LAN path - 5 - is a
value the vendor never writes there, and the value the vendor does write
(1) is one this driver has never written.

The report's caveat 1 says the LAN value is per packet rather than a constant:
the vendor computes `output_port = skb->port_index + 1`, where the byte is the
ingress chip port for a frame the vendor received and `g_wan_port` (0 on this
board) for a frame it made itself.  The "1" is therefore a proven default, not
a proven constant, which is exactly why it has to be tried on the board.

## 4. What the live board says

The trial kernel (run119) was booted on the board over the serial console and
the 9132 was reconfigured at runtime through its own debugfs console.  The
board's own counters were read before and after, and the PC's counters were
watched at the same time.

**Experiment A - the frames that leave the CPU are untagged on the way out.**
The four UNI ports' untag SVID (SPA register 2, bits 18..29) was set to the
transport VID their DSA port carries (59..62), and the CPU port's three flood
groups were pointed at PPU 2 in ERAM table 0x1f (entries 320, 1344 and 2368
plus the mislaid 1040).

```
PING 192.168.9.25 (192.168.9.25): 56 data bytes
4 packets transmitted, 0 packets received, 100% packet loss

  eth0:       0 rx     1046 tx       9 packets
lan-cpu0:       0 rx     2686 tx      24 packets
  lan2:        0 rx     1380 tx      12 packets
 br-lan:        0 rx     1380 tx      12 packets
```

**Experiment B - the protocol trap on the CPU port.**  The whole trap row of
ERAM table 0x18 that belongs to port 5 (entries 640..767, 128 protocols) was
cleared to 0, in case the trap was taking the board's own frames back to the
CPU instead of letting them be forwarded.  The read-back confirms the row is
clear (`eram table 0x18 entry 640..767 width 2 = 0x0`).  The ping still got
nothing.

Two things follow, and they are the two things that matter now:

1. **The frames do not come back from the chip.**  `lan2 rx = 0` after twelve
   frames were transmitted, and the PC's receive counters did not move by one
   byte.  Nothing that is done *inside* the 9132's forwarding engine can be
   the whole story any more: the egress tables were pointed at the right port
   and the egress frames were made untagged, and still not one frame arrived.
2. **The suspect is the SoC-side handover, not the switch.**  The descriptor's
   output port is the one field that says *where the frame goes*, the vendor
   never writes 5 there, and this driver always has.

## 5. What run120 adds

Three runtime knobs in the 9132's debugfs console, because the remaining
questions are about what the chip wants and not about what the vendor's code
says:

```
txp <n>     the IDM TX descriptor's output port for the LAN path
tx4 <n>     the fourth metadata word of the IDM TX descriptor on the LAN path
             (the vendor writes 0x003e0000 there on the LAN path and
             0x1f568623 on the PON path; this driver has always written
             0x08000000 on both)
usv <0|1>   the four UNI ports' untag SVID: 0 = the vendor's 0, 1 = the
             transport VID
```

plus the broadcast flood group id fix of section 2.

With those, one boot of the board answers all of it: `txp 1`, `txp 2` and
`txp 5`, each with and without the vendor's fourth metadata word and with the
untag SVID on, and the PC's own counters watched from the other side.

## 6. The bench, and what it cost

The board's power is cut by a smart plug every minute or two, so the trial
kernel lives for a short window and every experiment has to fit inside it.
Three things had to be fixed to make that possible, and they are worth
writing down because they are not obvious:

* **the loader's window is about two seconds.**  `Hit any key to stop
  autoboot: 1 0` is printed and the board is gone.  The probe has to fire
  inside it, so it cannot be a slow scan of the log.
* **the relay does not type whitespace.**  A file that holds nothing but a
  space is sent as a space plus a carriage return, which is a keystroke but
  not one the autoboot counts in time; a file that holds a command is the
  only reliable probe.  `version` is harmless in both places.
* **the acknowledgement has to be the file, not the log.**  The relay takes
  `sercmd.txt`, writes it to the port and only then renames the file away, so
  "the file is gone" is the acknowledgement.  Waiting for the relay's own
  line in the log is seconds too slow: the board prints fast while it boots,
  the line scrolls out of the window, and the next probe fires long after the
  window closed.

`staging/ctl/trial_exp.sh` (boot and experiment in one go), `staging/ctl/ubnow.sh`
(boot from the loader prompt that is already there) and
`staging/ctl/runexp.sh` (the experiments on a kernel that is already up) are
the scripts that came out of that, and they live outside the repository on
purpose: they are the bench, not the product.

## 7. Still open

* the IDM TX descriptor's output port: 1, 2 or 5 - run120's `txp` answers it;
* the fourth metadata word: the vendor's `0x003e0000` or this driver's
  `0x08000000` - run120's `tx4` answers it;
* whether the frames the IDM sends ever reach the 9132's CPU port at all, if
  no output port value works.  The next witness would be the chip's own
  counters: the vendor's `sw_port_get_port_statistics*` is a stub on this
  board, so this driver's console would have to grow a way to read the 9132's
  per-port counters.
