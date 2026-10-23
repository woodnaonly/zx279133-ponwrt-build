# Three rounds that failed for the same reason, and what the board actually says

Written 2026-10-23, between 07:30 and 08:00, on the bench. Three experiment
rounds (exp126 was earlier; exp127 was run three times) produced no verdict,
and in all three the cause was the *harness*, not the chip. This document
records what the failures taught, what the board's own registers say, and
which of the open questions are now closed.

Everything here is measured or quoted from the vendor's own artefacts; where
a statement is an inference it says so.

---

## 1. The harness: why five rounds produced nothing

The bench sends commands to the board over a 115200-baud PL011 console, one
file line at a time, through a relay that picks the file up and writes it to
the serial port.

### 1.1 The first failure: a burst of ten lines

`staging/ctl/serial.log`, 2026-10-23 05:59:35, one line:

```
ttyAMA ttyAMA0: 1 input overrun(s)
```

The console's receive FIFO overruns when a burst arrives. The byte that gets
lost is the newline, so the shell stops at its `>` continuation prompt and
**every later line is swallowed as part of a continuation**. Eighty seconds
of experiment produced nothing.

Fix: `runexp7.sh` sends one line per write (`CHUNK=1`).

### 1.2 The second failure: one line is not enough either

One line at a time does not help if the *previous* line is still running. The
experiment's queue loops (`i=0; while [ $i -le 319 ]; do ... done`) do
thousands of debugfs writes and take tens of seconds; the sender's fixed
1.2 s gap expires long before that, the next line arrives while the shell is
busy, and the FIFO overruns exactly as before.

Measured 2026-10-23 07:32:52, in the second exp127 round:

```
[  153.620661] ttyAMA ttyAMA0: 1 input overrun(s)
i=385; while [ $i -le 40=== >>> 07:32:55 sent: i=391; while [ $i -le 396 ]; ...
i=39
1; while [ $i -le 39; done
>
```

The shell sat at `>` for the rest of the round. Every reading after that
point - including the "after the pings" counters, the whole point of the
round - was lost.

Fix: `wait_prompt()`. A line is only sent after the shell's prompt has come
back, which is the board's own proof that it has finished with the previous
line. The wait has a ceiling (`PROMPT_WAIT`, 200 s) so a command that hangs
the shell costs time and not the round.

### 1.3 The third failure: the prompt walked out of the window

The first `wait_prompt()` looked for the prompt in the *last 20 kB* of the
console log. A queue loop that writes 320 queues prints 640 `soc reg ...`
lines - about 45 kB - so by the time the loop finished, the prompt was no
longer inside the window, the sender waited for a prompt it could not see,
and the round crawled.

Fix: the search starts *after the position the log had when the line was
sent*, so it is the prompt of *this* line that is being looked for, wherever
it is in the log.

The three failures are the same failure seen from three sides: the bench
must not type into a shell that is still working.

### 1.4 Two bugs in the experiment file itself

* `w()` was defined as `echo "regw $1 $2"`, which is an **NPPT direct
  write** - and it was then used as `w 15 0 0x0000a6a5`, which is a **SPA
  indirect write** (memory id, register, value). The two-parameter function
  silently turned the CPU-port programming into `regw 15 0`, which wrote zero
  into NPPT offset 15. The experiment file now separates them:
  `wr` = NPPT direct (`regw <off> <val>`), `sp` = SPA indirect
  (`w <mem> <addr> <val>`), `sr` = SPA indirect read.
* `fi()` is a **keyword** in ash. `-ash: syntax error: unexpected "fi"` -
  every call of the IN-buffer helper failed. The helper is now `fiq`.

None of this is the chip's fault, and none of it would have been visible
without reading the raw console log rather than the round's summary file.

### 1.5 The fourth failure: the vendor's shell is not the trial kernel

The runner asks the board for `uname -r` and looks for a "6." in the answer
to decide whether the trial kernel is up. The vendor's own printk lines carry
a timestamp - `[   16.013416]` - and "16." matches "6.", so a board sitting
in the vendor firmware was taken for a board running the trial kernel. The
runner then skipped the tftpboot, because it believed the trial kernel was
already running, and the round died with "the trial shell never came up".

Measured 2026-10-23 at 08:01:13. The test now looks for `6.18.`.

### 1.6 The fifth failure: the prompt is not a sign that the board is idle

ash prints the prompt after *every* command, not once per command line. A
queue loop that writes 320 queues runs 640 commands, so the console shows
640 prompts while the loop is still working - the transcript has

```
root@ImmortalWrt:[  190.524121] soc reg 0x19020014 (nppt +0x020014) = 0x00800008
~# [  190.524177] soc reg 0x1902001c (nppt +0x02001c) = 0x01800020
```

in the middle of the loop. Waiting for the prompt therefore returned after
the first iteration, the next line arrived while the loop was still
running, and the console's FIFO overran again - the shell spent the rest of
the round at its `>` continuation prompt. Measured 2026-10-23 at 08:36:18.

The board is idle when the prompt is there **and the console has gone
quiet**, so the runner now waits for a second of silence after the prompt
before it sends the next line.

---

## 2. What the chip's own registers say

### 2.1 The RED status words, decoded

`staging/ctl/tmp_redq_bits.py` decodes the two RED status words from the
field layout the vendor's own `red_monitor` uses.

| word | bit | field | our board | vendor board |
|---|---|---|---|---|
| 0x200b0 `red_core` | 4 | `red2qmg_pkt_len_err` | **1** | 0 |
| 0x200b0 | 21 | `req_que_disc` | **1** | 0 |
| 0x200b0 | 9..11 | `idm_band_bp_empty` | 7 | 7 |
| 0x200b0 | 18 | `red_used_in_gua_space` | 0 | 1 |
| 0x200b4 `drop_monitor` | 13 | `pktlen0_flt_drop` | 1 | 0 |
| 0x200b4 | 16 | `inqid_err_drop` | 0 | 0 |
| 0x200b4 | 17 | `drop_pkt` | 1 | 0 |
| 0x200b4 | 18 | `req_que_disc` | 1 | 0 |

Our board fails in the **RED's egress stage**: `in fwd cnt` (0x20204) rises,
`out fwd cnt` (0x20210) stays at zero, `out drop cnt` (0x20214) equals
`in fwd cnt`, and the per-class counter `in idm fwd cnt` (0x201c0) stays at
zero. On the vendor's board `in fwd == out fwd` and `in drop == out drop`.

The two set flags are the two ends of the same failure:
`red2qmg_pkt_len_err` says the RED handed the queue manager a packet whose
length the queue manager would not accept, and `req_que_disc` says the
request for a queue was discarded.

### 2.2 The measured baseline, second exp127 round

Read on the trial kernel of run127, before any intervention:

```
0x20004 RED_CFG            = 0x0000007e
0x200b0 red_core           = 0x00200e10
0x200b4 drop_monitor       = 0x00060000
0x200b8 discard cnt        = 0x00140000
0x201c0 in idm fwd cnt     = 0x00000000
0x20204 in fwd cnt         = 0x00000014
0x20210 out fwd cnt        = 0x00000000
0x20214 out drop cnt       = 0x00000042
```

`RED_CFG = 0x7e` is the **SR1010** value. This is not a mistake in the
overlay: the RED's base programming lives in the tree's
`zx279133_tm_prepare()`, which the overlay cannot reach, so the overlay
writes the vendor's values *again* afterwards
(`zx279133_red_vendor_fixup()`).

**Consequence for the experiments**: on a run127 kernel the RED's IN buffer
RAM, its OUT buffer RAM and its per-queue configuration are the *tree's*
SR1010 values, not the vendor's. An experiment that wants the vendor's RED
has to write all of them at run time; it cannot assume the kernel already
did it. Run130 is the first run where the overlay writes them itself.

---

## 3. Closed questions

### 3.1 The CPU is port 15, not port 5

Three independent lines of evidence, all from the vendor's own artefacts:

1. `print_p_port()` (`np_133.ko` `.text` 0xf6c80) prints the port namespace
   the vendor itself uses: `SPA_SMAC0..3_MEMID = 1..4`, `SPA_XMAC0_MEMID = 5`,
   **`SPA_CPU_MEMID = 0xf`**.
2. The vendor's IDM **transmit** descriptors carry `inport = 0x0f` - the CPU
   trap's SPA id - while their `output_port` is the target UNI jack's chip
   port, 1..4, never 5.
3. On the vendor's own board, XMAC0's registers read zero
   (`0x19140000..0x1914001c` all 0, `NPPT+0x0084` = 0): the vendor never
   programs the block our driver was calling the CPU port.

The driver still used PPU 5 for the DSA conduit, for the flood groups, for
the conduit's port-attribute word and for the MF mirror. That is now
`ZX279133_9132_CONDUIT_PPU 15`.

### 3.2 The LAN transmit descriptor's output port is the target jack, and the target jack is port 2

* The vendor's transmit descriptors name the **target UNI jack's** chip port
  (1..4). The driver used 5 - XMAC0, which has no peer.
* Which jack the test PC is in, measured two ways:
  * `trial-files/etc/rc.d/S26nat`: "the PC is in the PHY 0x0b port, which is
    DSA port 5, which is lan2";
  * the driver's own DSA map: DSA ports 4..7 are PPU 1..4 and MDIO 0x0a..0x0d,
    so DSA port 5 is PPU 2.

  PHY 0x0b is the second PHY, PPU 2 is the second chip port. Both roads
  arrive at **chip port 2**, so `ZX279133_LAN_TX_PORT` is 2.

### 3.3 The LAN path's fourth descriptor word is 0x003e0000

The vendor's own `dump_desc_tx()` names this word the GEM port's LLID id
plus its queue id. The vendor's LAN path writes `0x003e0000` and its PON
path writes `0x1f568623`. The driver wrote the PON value
(`zx279133_tx_pon_control` = `0x08000000`) on **both** paths.

### 3.4 The outport map pointed every frame at a queue that does not exist

The 9132's ERAM table 0x27 maps an outport to the real port the frame
leaves by and to the queue it lands on. The entry is 16 bits: the real port
in bits 0..5, the queue id in bits 6..14.

The vendor's `outport_and_real_port_map_table_init()` (`np_133.ko` 0xc06c8)
writes all 64 entries:

| outport | real port | queue id |
|---|---|---|
| 0..14 | the outport itself | **0** |
| 15 | 15 | 0x193 (403) |
| 16..23 | 15 | 0x1a3 (419) + a per-port delta |
| 24..48 | the outport itself | **0** |
| 49 / 50 / 51 | 49 / 50 / 51 | 0x1b1 / 0x1b3 / 0x1b5 |
| 52..63 | the outport itself | **0** |

This driver wrote the real port **15** everywhere and the queue id **0x1a3**
everywhere. That is what the vendor's printk says - `real_port: 15
real_queue_id:419` - but that print only covers outports 16..23, the ones
that are the IDM's own, so the other 61 entries were read off the wrong line
of the log.

Two things are wrong with that, and both matter:

* **the queue id.** 0x1a3 is 419, and the RED has 401 queues (0..400). A
  frame the switch egresses on any ordinary outport was handed a queue the
  RED cannot deliver on. The RED reports exactly that: `req_que_disc` and
  `drop_pkt`, with `in fwd cnt` rising, `out fwd cnt` stuck at zero and
  `out drop cnt` equal to `in fwd cnt`.
* **the real port.** The real port is the physical port the frame leaves by.
  The vendor maps outport N to real port N; this driver mapped everything to
  15, the CPU trap - so a frame the 9132 forwarded to a UNI jack was aimed
  at the CPU.

61 of the 64 entries had the wrong real port and 43 had the wrong queue id.

### 3.5 The CPU trap needs its own entry program, and no attribute word

The vendor's board reads a different set of words out of the trap port's SPA
memory than out of the four UNI jacks (docs/18):

| word | the four jacks | the CPU trap |
|---|---|---|
| 0 - the entry program | 0x0000a6a5 | **0x0000a767** |
| 1 | 0x0000088f | 0x0000088f |
| 2 - the untag SVID | 0 | 0 |
| 9 - the VLAN filter | 0 | 0 |
| 10 | 0x0fa0000e | 0x0fa0000e |
| 13 | 0x00008008 | 0x00008008 |
| 19 - the port attribute word | 0x96040001 | **0x00000000** |

A trap port is a sink: it has no traffic of its own to learn or forward, so
its attribute word is zero. Its entry program is a different one, and that
program is what the switch runs on a frame that is routed to the CPU.

The driver wrote the jacks' words into every port it configured, and when
the conduit was moved to port 15 it wrote **none at all** - the port loop's
index ran past the end of the flood table and returned early. So the trap
port kept whatever the chip came up with.

The vendor's own code says the same thing: `spa_init` (0x27834) runs a
per-memory-id loop and calls the trap port's helper with `mov w0, #0xf`,
and the entry program comes out of the microcode blob
(`np_downlaod_mcode`, 0xf1550, and the setter at 0xf141c).

---

## 4. Still open

| # | question | why it is open | how it closes |
|---|---|---|---|
| Q1 | does the vendor's RED configuration alone make the LAN path work? | the experiment that would answer it has now failed twice for harness reasons; the third attempt runs on 2026-10-23 at 08:00 | the round writes the vendor's `RED_CFG`, share maxima, watermarks, ISU thresholds and all three queue RAMs at run time, then the CPU port and the flood groups, then reads the counters |
| Q2 | which of the four UNI jacks is the right transmit target per client? | the driver has one global `lan_tx_port`; the vendor picks the target from the frame | the DSA tagger has to carry the target port to the conduit - the shape of that change is a separate piece of work |
| Q3 | the ERAM 0x1f flood group's entry index convention | our driver uses `p << 6` as an entry index; the vendor's `se_eram_table_set(0x1f, group_id, 1, &port_id)` uses the group id directly | the vendor's own write sites, decoded (in progress) |
| Q4 | whether the vendor's per-queue share list differs from the built-in default | the vendor reads nine `g_red_*_share_max` globals that the stock board leaves undefined; the built-in defaults it falls back to are exactly what the driver writes | the vendor's own board prints "default val is -1" for each of them, which is the fallback, not an override |
| Q5 | the BMU pool geometry (0x1800 vs the vendor's 0x3400) | the pools are laid out by the switch core from a global register, and the vendor's geometry needs about 32 MB of reserved memory, which collides with the IDM region | the reserved-memory map has to move; separate work |

---

## 5. The next run

`run131` and `run132` carry the corrections that the evidence above closes:

1. `ZX279133_9132_CONDUIT_PPU` 5 -> 15, and the trap port's own words
   (entry program 0xa767, attribute word 0) instead of the jacks' words
2. the outport map's 64 entries, as the vendor writes them
3. `ZX279133_LAN_TX_PORT` 5 -> 2
4. the LAN path's fourth descriptor word `zx279133_tx_pon_control` ->
   `ZX279133_LAN_TX_WORD4` (0x003e0000)

The DSA port map is **not** touched: which kernel netdev is which jack is
still not settled, and the map that is in the tree already agrees with the
measurement in §3.2.

Nothing in this document changes the acceptance criteria of G2. The board
has to carry a frame from a jack, through the RED, out of the IDM, into the
SoC, and back - twice, from a cold start.
