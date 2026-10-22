# 58 - The round that measured the wrong kernel, and the RED block nobody had compared

Date: 2026-10-23

Two rounds (`exp121.txt`, `exp122.txt`) were run against the **run127** trial
kernel.  Both produced real measurements, and every conclusion drawn from them
was wrong, for a reason that was not visible at the time: run127 predates
commit `9d86987`, which is where the driver gained the vendor's RED
programming, the vendor's per-queue buffers, the vendor's share maxima and the
board's own MAC.  This document records what the rounds actually showed, what
the vendor's `red_init` does, and what is still missing.

---

## 1. The bench round produced nothing, and the reason is the bench

`exp122` was supposed to switch the RED's "illegal queue id" drop off, program
the CPU's queues 0x191..0x193 and watch the counters.  The board's console log
(`staging/ctl/serial.log`, 05:59:35) shows what actually happened:

```
=== >>> 05:59:35 sent: ln -sf /sys/kernel/debug/zx279133-9132 /d
d() { echo "regs $1 $2" >/d; }
w() { echo "regw $1 $2" >/d; }
echo "===== MARK A baseline"
d 0x20038 1
d 0x2003c 1
d 0x2008c 1
d 0x200b0 8
d 0x200b4 8
d 0x20200 8 ===
```

Ten lines, one write, 190 bytes.  The console is a 115200 baud PL011 with a
sixteen-word receive FIFO and the USB-serial bridge hands the whole file to the
chip in one go, so the FIFO overruns.  The board says so itself:

```
[   45.082867] ttyAMA ttyAMA0: 1 input overrun(s)
```

The byte that carried the newline is the one that is lost, and the board's
shell is left waiting for the rest of the line:

```
root@ImmortalWrt:~# d 0x2008c 1=== >>> 05:59:37 sent: echo "===== MARK B ...
[   45.082867] ttyAMA ttyAMA0: 1 input overrun(s)
```

From that point on every command was swallowed as a continuation of the
unfinished one - the console shows the `>` prompt for the next eighty
seconds - so the counters were never read and the experiment never ran.  The
round's own summary line ("the experiment reached 'G2DONE-122'") was the
script's marker, not a result.

**The fix is in the sender, not the experiment.**  `staging/ctl/runexp7.sh`
now defaults to `CHUNK=1`: one line per write, with the same pause between
lines.  A single line of twenty bytes is 1.7 ms of wire time, which the FIFO
absorbs; a hundred and ninety bytes is 16.5 ms, which it does not.

The lesson for the measurement itself: **a round that reads nothing is not a
round that measured nothing.**  The `soc reg` lines that were missing from
`exp122`'s output were not suppressed by the console's rate limiter and not
lost to a dead board - the commands that should have produced them were never
run.

---

## 2. The kernel the rounds ran on

`exp121` and `exp122` both used `staging/trial/sk-d840n-initramfs-run127.itb`.
What the driver looked like in that build:

```
$ git show bbd0c00:kernel-overlays/drivers/net/ethernet/zte/zx279133.h | grep RED_CFG
#define ZX279133_RED_CFG			0x20004
#define ZX279133_RED_CFG_MASK		GENMASK(7, 0)
```

There is no `ZX279133_RED_VENDOR_CFG`: the constant that makes the driver write
`0x9e` into the RED's configuration word arrived in `9d86987` ("give the board
its MAC and program the RED the way the vendor does"), which is run128.  So on
run127 the RED was left with the **tree's SR1010 configuration**:

| what | run127 | run130 | vendor's board |
|---|---|---|---|
| `RED_CFG` (0x20004) | 0x7e | 0x9e | 0x9e |
| per-queue IN/OUT buffer words | the tree's | the vendor's | the vendor's |
| share maxima, watermarks | the tree's | the vendor's | the vendor's |
| the board's MAC | random per boot | `d0:45:8d:84:09:00` | factory |

`RED_CFG` bit 7 is `all_active_mode`; the tree's 0x7e clears it and the
vendor's 0x9e sets it.  The board agrees - the trial kernel's own boot log
reads `soc reg 0x19020004 = 0x0000007e` on run127 and the vendor's own dump
reads `red reg=0x1 val=0x9e`.

**So the "inqid_err_drop" that `exp121` measured was measured on a RED that
never had the vendor's configuration, on a board whose MAC changed on every
boot, and with the OUT buffer word sixteen times too large.**  It was a real
measurement of a configuration the vendor's board never runs.

`run130` is the first build that has all of it.  The measurement has to be
repeated there before anything else is concluded.

---

## 3. The vendor's `red_init`, decoded

`red_init` is `np_133.ko` 0x116f20, 1016 bytes
(`python staging/ctl/redx_mod.py np_133.ko red_init`).

### 3.1 The share maxima come from globals with built-in defaults

Every share maximum is read from an undefined global and falls back to a
constant when the global is `-1`:

```
adrp x0, g_red_all_in_share_max ;  mov w1, #0xc00 ;  ldr w0, [x0]
cmn  w0, #1 ;  csel w0, w0, w1, ne ;  bl red_set_in_share_max
```

The built-in defaults are `0xc00`, `0x900`, `0x500`, `0x100`, `0x0`,
`0x3000`, `0x2000`, `0x1000` and `0x2600` - **exactly the values the driver
hard-codes** (`ZX279133_RED_VENDOR_*_SHARE_MAX` in `zx279133.h`).  The
vendor's own boot log confirms the application does not override them either:

```
[7.678432] [init_pon_plat_np_rsv_mem_cfg] get g_red_cw_in_share_max error! default val is -1
```

### 3.2 The per-queue table has a default, and the default is what we program

`red_init` branches on `g_red_queue_in_share_ptr` / `g_red_queue_out_share_ptr`.
When they are NULL - which they are on a stock board - it walks its own table:

| queues | IN buffer (guard, max) | word | OUT buffer (guard, max) | word |
|---|---|---|---|---|
| 0x000..0x13f | 0x20, 0x600 | 0x01800020 | 0x40, 0x1000 | 0x01000040 |
| 0x140..0x17f | 0x20, 0x300 | 0x00c00020 | 0x40, 0x800 | 0x00800040 |
| 0x180 | 0x20, 0x100 | 0x00400020 | *not written* | - |
| 0x181..0x186 | 0, 0 | 0 | *not written* | - |
| 0x187..0x18c | 0, 0 | 0 | 0x20, 0x400 | 0x00400020 |

and then writes the four-word configuration of every queue 0x000..0x190
(401 queues) with `red_set_buffer_queue_cfg`:

```
001171e4  mov w0, #0x3fff
001171e8  mov w1, #0x80
001171f0  stp w0, w1, [x29, #0x50]
...
001172a8  mov w0, w20
001172ac  add x1, x29, #0x50
001172b4  bl  #0x621d4 <red_set_buffer_queue_cfg>
001172b8  cmp w20, #0x191
```

The nine words the vendor builds on the stack are packed by that function into
the four words the driver writes: `{0xff803fff, 0x0100ff80, 0x00100200,
0x00000020}`.  **The driver's per-queue programming is the vendor's.**

One difference remains: the vendor does not write the OUT buffer of queues
0x180..0x186, and the driver writes zero there.  Whether that matters is not
known.

### 3.3 `red_init` writes nothing else

The only other things `red_init` does are `red_set_cfg_enable(1)`,
`red_set_open_out_en(1)`, `red_set_share_mode(1)`,
`red_set_woe_share_mode(1)`, `red_set_all_active_mode(1)` - the last four
being the bits of `RED_CFG = 0x9e` - and the six NP1 ISU thresholds.

---

## 4. The RED block had never been compared

The block-by-block comparison (`staging/ctl/ourdiff.py`) walks the GREG, the
schedulers, the SOPC, the DMA and the QMG, and it never had the RED's 2048
words: the board had only ever had its first sixteen read back.  The vendor's
copy is `staging/ctl/vendor-serial-mods/tm-red.txt`.

`staging/ctl/redgap.py` does the comparison and lists the words the vendor's
board has and the driver never writes.  114 of the vendor's 2048 words are
non-zero; the driver writes 31 of them.  Most of the remaining ones are
read-only status and counter words, which no write can change.  The ones that
look like configuration are:

| word | NPPT | vendor | in the driver? |
|---|---|---|---|
| `RED_CFG` | 0x20004 | 0x9e | yes, since run128 |
| `RED_CFG` neighbour | 0x2000c | 0x7f | no |
| share pairs | 0x20044, 0x20048 | 0x0fff0fff | no |
| share masks | 0x20054, 0x20058 | 0xffffffff | no |
| - | 0x20068 | 0x00010001 | no |
| - | 0x20078 | 0x00000801 | no |
| `MINI_PKT_LEN` | 0x2008c | 0x00000040 | no (the tree's 0x20) |
| - | 0x20094 | 0x00000619 | no |
| - | 0x2009c | 0x07000700 | no |
| - | 0x200f0, 0x200f4, 0x200fc | 0x138, 0xffff, 0x10001 | no |
| - | 0x20100..0x20164 (16 words) | mixed | no |
| `RED_CPU133_CFG` | 0x2024c | 0x000a0001 | no |
| - | 0x20254, 0x20258, 0x2025c | 0x11111111, 0x12041204, 0x00400000 | no |
| - | 0x20278 | 0x70006000 | no |
| - | 0x20340 | 0x0000000b | no |
| NP1 thresholds | 0x2035c..0x20364 | 0x00100020 | partly |
| NP1 watermarks | 0x2036c..0x2037c | 0x00000010 | partly |
| threshold block | 0x20280..0x2033c | 0x0fff0fff (48 words) | no |

`staging/ctl/exp125.txt` writes the configuration-shaped ones at runtime, so
the question can be answered without a rebuild.

---

## 5. The three queue-id spaces

The vendor's `np_133.ko` has three different queue-id ranges, and they do not
overlap:

| space | range | evidence |
|---|---|---|
| RED / TM queues | 0..400 | `red_set_out_buffer_queue_cfg` 0x61b40 `mov w0,#0x190`; `red_set_buffer_queue_cfg` 0x62274 `cmp w22,#0x191`; `red_init` 0x1172b8 `cmp w20,#0x191` |
| USCH descriptor qid | 0..319 | `usch_desc` 0x7030c `cmp w22,#0x13f`, prints `"usch but qid >= 320 error!!!!"` |
| SSCH aging qid | 0..63 | `ssch_set_aging_qid` 0x6c788 `cmp w0,#0x3f`, prints `"qid 0x%x out fo range."` |

The CPU's own base queue id, `0x193` = 403, is **outside the RED's 400**, so it
belongs to neither the RED nor the USCH: it is the CPU/IDM space, the one the
outport map (`ERAM 0x27`, `outport_and_real_port_map_table_set` 0xc0598,
bits 0..5 = real port, bits 6..14 = qid) and the global config table
(`ERAM 0x22`, `glb_cfg_table_cpu_base_qid_set` 0xbdc5c) point at.

The QMG keeps its own queue-id counters, at NPPT `0x240c4` and `0x240c8`
(`qmg_req_empty_err_monitor` 0x68e60) - **a different block from the RED's
`in qid err cnt` at 0x200b8**.  Reading them separates "the RED refused the
queue id" from "the QMG did".

---

## 6. The CPU port is SPA memory id 15, not 5

The 9132's port namespace is the SPA memory id, and the vendor prints the
whole namespace itself (`print_p_port`, `np_133.ko` 0xf6c80):

```
SPA_PON0_MEMID = 0, SPA_SMAC0_MEMID = 1 ... SPA_SMAC3_MEMID = 4,
SPA_XMAC0_MEMID = 5, SPA_XMAC1_MEMID = 6, SPA_RGMII1_MEMID = 7,
SPA_LOOP_SOPC_MEMID = 8, SPA_PON1_MEMID = 0xe, SPA_CPU_MEMID = 0xf
```

The driver's "PPU" is that namespace: PPU 0 is the PON, PPU 1..4 are the four
UNI jacks, PPU 5 is XMAC0.  The vendor's own TX descriptors carry
`inport = 0x0f`, and the outport map sends `dest_port 15` to the CPU's queue
`0x193`.  **The CPU is the trap port 15.**

The driver treats PPU 5 as "the DSA conduit" and programs it, the flood
groups and the IDM TX descriptor's `output_port` with it.  On the vendor's
board XMAC0's registers read zero - the vendor never programs the block the
driver calls the CPU port - and the board's boot log says `mac 4 link down`
for it.

---

## 7. What happens next

1. **Measure on run130** (`staging/ctl/exp124.txt`): the RED's own
   configuration word, the ingress and egress counters before and after the
   PC's ping burst, the QMG's own queue-id counters, and the attribute words
   of ports 1..5 and 15.  The acceptance test is `in idm fwd cnt` (0x201c0)
   starting to move and `inqid_err_drop` (0x200b4 bit 16) staying clear.
2. If the RED still refuses the frames, **write the vendor's missing RED
   words at runtime** (`staging/ctl/exp125.txt`) and watch again.
3. Only then change the driver: the CPU port becomes PPU 15 (with its own
   entry program, attribute word and flood words, and the MF mirror), the
   flood groups point at 15, the IDM TX descriptor's `output_port` becomes the
   target UNI's chip port and its word 4 becomes the vendor's `0x003e0000`.

---

## 8. The files

| file | what it is |
|---|---|
| `staging/ctl/exp124.txt` | the run130 measurement |
| `staging/ctl/exp125.txt` | the runtime write of the vendor's missing RED words |
| `staging/ctl/go-round124.sh` | the bench round for the above |
| `staging/ctl/redgap.py` | the RED block comparison |
| `staging/ctl/redhead.py`, `staging/ctl/redmap.py` | the vendor's RED window, side by side |
| `staging/ctl/qidsym.py` | the `g_red_*` globals and where they live |
| `staging/ctl/spaports.py` | the vendor's SPA window, per port |
| `staging/ctl/lastboot.py`, `staging/ctl/lastreg.py` | the console log's last boot, and its register readings |
| `staging/ctl/re-qid.md` | the queue-id reverse engineering this document leans on |
| `staging/ctl/re-cpu-port-mac.md` | which MAC block the CPU port is (it is none of them) |
