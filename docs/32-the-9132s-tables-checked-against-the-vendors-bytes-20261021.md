# 32 — the 9132's tables, checked against the vendor's own bytes (2026-10-21)

This is the audit that closes the questions docs/30 and docs/31 left open.
Three of them were the difference between "the data path works" and "the data
path cannot work", and all three are now answered from the vendor's own
bytes, not from a guess.

## 1. The ERAM pool starts at block 0x3000

**Answered.** docs/31 has the board's own measurement (a write below 0x3000
does not land, a write at 0x3000 comes back unchanged) and the vendor's own
reason for it: `se_smmu0_skip_ram_for_qmg()` makes the allocator's cursor
jump over the block the queue manager owns - skip base 0x1000, skip size
`ram_index << 12` - and with `ram_index = 2` that is 0x1000 + 0x2000 =
**0x3000**.

And the layout is the vendor's, item for item.  The vendor's
`g_core_sdt_info` (np_133.ko, 53 entries) and the driver's
`zx279133_core_sdt_info` (the same 53, plus 0x00, 0x04 and 0x06 at the
end) put through the same allocator with the same pool start give **the same
base block and the same depth for every one of the 53**.  No table differs.
`staging/ctl/vsdt.py` prints the comparison; `tools/eram-map.py` prints the
map out of the driver's own source.

## 2. The protocol action table (ERAM 0x18) — checked byte for byte

**Answered, and it is right.**

The driver fills 0x18 from `zx279133_9132_proto_action[128]`, and its comment
says the 77 (protocol, action) pairs came out of switch.ko's own `.rodata`.
That claim was checked against the module:

* `sw_init_port_protocol_action()` (switch.ko 0x2370) walks the ports 1..7
  and 0x10..0x30, skipping the WAN port (`g_wan_port_id`), and calls a static
  setter for each.
* The setter (0x22a8) `memcpy()`s **0x268 bytes** out of `.rodata+0x498` and
  then walks them in **8-byte groups**, handing
  `tm_port_protocol_pktdeal_set(port, action, protocol)` each one.
  0x268 / 8 = **77 groups** - the 77 pairs.

The pairs are (protocol, action), with the protocol in the first word and the
action in the second:

    protocol 0x00 0x01 0x08 0x09 0x0a 0x0b 0x0c 0x0d 0x10 .. 0x34   -> action 2
    protocol 0x35 .. 0x47                                          -> action 0
    protocol 0x48 0x4a 0x4b                                        -> action 2
    protocol 0x49 0x4c 0x53                                        -> action 0
    protocol 0x51 0x52 0x54 .. 0x58 0x60 0x61 0x62                  -> action 2

(the list is 76 distinct protocols, 0x23 appearing twice) and compared with
the driver's array: **zero differences**, in both directions - the driver
writes nothing the vendor does not write, and misses nothing the vendor
writes.

`staging/ctl/swproto77.py` dumps the vendor's 77 pairs and
`staging/ctl/swat.py` disassembles a range of a module, if the check is
wanted again.

**A trap worth knowing.**  The vendor's own live getter
(`port_proation_get` on `/proc/pon/tm_debug`) prints `action:1` for
protocols 0, 1, 8 and 9 and nothing at all for 16, 30, 33, 50, 53, 60, 75
and 96.  That is **not** the raw two-bit action: the raw action for those
protocols is 2, and the protocols it reports "none" for are in the vendor's
table.  The getter prints its own enumeration, over a table that is not this
one.  Reading it as the raw value is what made the protocol action table look
wrong for a while.

## 3. The port isolate matrix (ERAM 0x1b) — checked against the vendor

**Answered, and it is right.**

`sw_init_port_isolate()` (switch.ko 0x26bc) does two things:

1. for every port 0..63, `zxic_api_sw_set_port_isolate_enable(port, 0)` -
   isolation off;
2. for every (destination, source) pair, `zxic_api_sw_port_set_port_isolate`
   with **1 on the diagonal and 0 everywhere else**.

That is exactly what `zx279133_9132_isolate_init()` writes, so the driver and
the vendor put the same bits in the same places.

The vendor's isolation setup has **two** halves, and both are covered:

* the matrix itself - `tm_port_set_port_isolate(dst, src, value)` goes to
  `port_isolate_table_set()`, which is the ERAM table 0x1b the driver fills;
* the per-port **enable** - `tm_port_set_port_isolate_enable(port, enable)`
  goes to `port_attr_def_isolate_en_set()`, which is the **port attribute
  word**, the same word ERAM table 0x7d mirrors.  The vendor writes 0 for
  every port, and the attribute word the driver writes, 0x96040001, has
  bit 27 (the isolate enable) clear - as it has bit 31 (SA learning) and
  bit 28 (DA lookup) set.

So the polarity argument does not even have to be settled: the vendor's own
board forwards, and the vendor's own bytes are the ones the driver writes, so
the driver gets the vendor's behaviour whatever the bit means.

For the record the meaning is the obvious one, and the driver's own comment
had it the other way round: it says "an empty isolation matrix says that no
port may talk to any other", but an empty (all zero) matrix has nothing
isolated, which is why the ERAM coming up empty after a power cut is not a
problem for this table and why the vendor fills it in at all.  (The
diagonal-1 is what a switch needs: a port must not forward a frame back out
of the port it came in on.)

## 4. What is left

The three tables above are the ones whose *content* could have been wrong.
They are not.  What remains unknown is not in the tables:

* whether the frame the 9132 hands to the CPU lands on a queue the CPU polls.
  The driver reproduces the vendor's whole queue chain - the QMG, the RED
  reorder block, SSCH, USCH, WOE and the IDM's own init image - so the
  mapping from the switch's CPU base queue (0x193) to an IDM RX ring is the
  vendor's, but it has never been watched end to end on this board.
* the per-port counters of the 9132 itself, which the vendor's own console
  can read (`sw_port_get_port_statistics1..5`) and the mainline driver
  cannot yet.

Both are answered by measurement, not by reading, and the measurement
(`staging/ctl/accept5.sh`) is one script away: the lane's tx frames, the
LAN port's rx, the CPU port's rx, and the ERAM the driver actually left.
