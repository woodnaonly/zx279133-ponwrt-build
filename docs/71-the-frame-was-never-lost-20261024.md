# The frame was never lost: it was counted in the wrong place

Date: 2026-10-24 (rounds 191-197)

## 0. What this round changed

Everything up to round 190 said the same thing: the PC's frames arrive at the
9132's SMAC1, they are counted there, and they never come out.  The frame
was never lost.  The counter that was read to decide that was in the wrong
place.

## 1. The address that was wrong

The vendor's own two-pass register dump is the oracle.  Its SDET module
carries `reg_cnt = 0x180` words and its counters are the words 0x60 and 0x61:

```
sdet reg=0x60 val=0xfc5bfc5b     ->  NPPT + 0x2180
sdet reg=0x61 val=0xfc5cfc5c     ->  NPPT + 0x2184
```

Both halves of each word are 16-bit counters that wrap.  Over the vendor's
99-second flood they went from 64603 to 28674, which is 29607 frames - the
number of frames the flood sent.

An earlier round read **NPPT + 0x21800** for the same counter, which is a
hole between the modules, not a register: it reads zero on both boards.  Every
conclusion of rounds 184-190 that was based on it - "the frame never reaches
the switch" - was an artefact of that address.

`staging/ctl/vendor-fresh.json` (the two-pass dump) and
`staging/ctl/vendor-vdump.txt` are the evidence.

## 2. The chain, measured with the right addresses

`staging/ctl/exp191.py` reads fifteen counters of the chain, before and after
a controlled flood, and prints what moved.  The addresses are the vendor's
own; the deltas are the board's:

| address | counter | frames |
|---|---|---|
| 0x080780 | SMAC1 rx packets | **+9000** |
| 0x002180 | SDET in_sop | **+9000** |
| 0x002184 | SDET in_eop | +9000 |
| 0x004040 | SIPC in_sop | **+9000** |
| 0x008c08 | SPA | **+9000** |
| 0x00c0f0 | SMCT | +27815 |
| 0x010080 | ISU | 0 |
| 0x014044 | ODMA | 0 |
| 0x020204 | RED in forward | 0 |
| 0x0240cc | QMG | 0 |
| 0x034084 | SOPC | 0 |
| 0x038104 | DMA | 0 |

So the frame reaches the switch, is handed from the SMAC to the SDET, to the
SIPC, to the SPA, into the SMCT - and stops there.  The SMCT's own output
counters (0x00c104, 0x00c108, 0x00c118, 0x00c11c, 0x00c120) do not move
either, so the frame never gets from the SMCT to the ISU.

The vendor's two-pass dump says exactly which words move on a working board,
and the two sets line up word for word up to that point:

| block | vendor's moving words | ours |
|---|---|---|
| SPA | 0x8c00, 0x8c04, 0x8c08, 0x8c98, 0x8c9c, 0x8ca0 | the same six |
| SMCT | 0xc0f0, 0xc0f4, **0xc104, 0xc108, 0xc118, 0xc11c, 0xc120** | only 0xc0f0, 0xc0f4 |
| ISU | **0x10080, 0x1008c** | none |
| ODMA | **0x14044, 0x14048, 0x1404c** | none |
| RED | 0x20094, 0x200a4, 0x20100, 0x20118, 0x20154, 0x20158, 0x20160, 0x20164, **0x201c0, 0x201d8, 0x20204, 0x20210, 0x2021c, 0x2022c, 0x20230**, 0x203d8 | none |

The vendor's own statistics name the chain and the numbers agree with the
register dump (`staging/ctl/vendor-vstats.txt`):

```
SMAC1 up frame total 1807
SDET  in_sop 1807, in_eop 1807, success_in_eop 1807, success_out_eop 1807
SIPC  sipc to spa pkt cnt 1809
SPA   SIPC2SPA PKT CNT 1809, SPA2SMCT PKT CNT 1809, SPA2ISU DESC CNT 1809
ISU   spa in cnt 1809, out to ppu unicast cnt 1809
ODMA  ppu in cnt 1809, read smct cnt 1809, out to red cnt 1809
MCODE protocol action trap cnt 1807, port id 15 output brd cnt 1806
```

So the vendor's frames are trapped to the CPU by the protocol action table
and come out of the trap port.

## 3. The configuration around the break

Every word of the SMCT's configuration is the vendor's, with one exception
(`staging/ctl/cmpblk.txt`):

```
0x00c064   vendor 0x10000200   ours 0x30000200
```

That word cannot be written from the console - the write does not stick - so
it is a status word, and its one extra bit is a symptom of the stall rather
than its cause.

The SPA's, the ISU's and the SMCT's other words are the vendor's.  The
difference is therefore not in the switch engine's registers but in what the
switch engine reads out of the ERAM.

## 4. The ERAM, and the port banks

The ERAM is the switch engine's own memory.  It is not in the NPPT window and
it was never compared with the vendor's, because the vendor's own dump of it
("-sys -ind_ram nppt") prints every table of every subsystem and one of them
is the reorder module's descriptor-refcount table with depth 16384 - that
dump runs for hours.

Reading it one entry at a time works (`staging/ctl/vendor_eram3.sh`).

What the board's own ERAM holds was read out entry by entry
(`staging/ctl/exp194.py`, `staging/ctl/exp195.py`), and one table is short of
what the vendor has.

**The protocol action table (ERAM 0x18) is indexed by (port << 7) | reason,
and the 9132 numbers its ports in three banks.**  The vendor's own flood
words say so, and they are the vendor's values for every jack:

| port p | unknown-DA group | broadcast group | multicast group |
|---|---|---|---|
| 1 (smac_0) | 0x0040 = p<<6 | 0x0440 = (p+0x10)<<6 | 0x0840 = (p+0x20)<<6 |
| 2 (smac_1) | 0x0080 | 0x0480 | 0x0880 |
| 3 (smac_2) | 0x00c0 | 0x04c0 | 0x08c0 |
| 4 (smac_3) | 0x0100 | 0x0500 | 0x0900 |

So a jack that is port 2 is also port 0x12 and port 0x22, and the protocol
action table needs three groups for it.  The vendor's load path writes the
groups 0..7 **and** the groups 0x10..0x30.  This driver wrote the groups
0..7 only, so the two groups the frame really looks up were empty - and a
frame that finds no action is neither forwarded nor trapped.

`staging/ctl/exp197.py` writes action 2 ("give this frame to the CPU") into
every entry of the two groups of the jack the PC is on, by hand, and then
watches the chain:

```
group 0x12 entry 2304 = 0x0   ->   0x2
group 0x22 entry 4352 = 0x0   ->   0x2
```

## 5. What this round does not yet explain

With the two groups filled in by hand, the frame still does not come out of
the trap port: the RED's in-forward counter stays at zero and the DMA's stays
at zero.  The ISU's counters move, but not by the number of frames that were
sent.

So the missing port groups are a real gap - the vendor writes them and this
driver did not - but they are not the whole of it.  What is still missing is
in the ERAM: the vendor's tables, read entry by entry, are the next thing to
compare.

## 6. Files

| file | what |
|---|---|
| `staging/ctl/exp191.py` | the chain, measured with the vendor's addresses |
| `staging/ctl/exp192.py` | the blocks around the break, before and after a flood |
| `staging/ctl/exp193.py` | the SMCT word that differs from the vendor |
| `staging/ctl/exp194.py`, `exp195.py` | our ERAM tables, entry by entry |
| `staging/ctl/exp197.py` | the protocol action table's missing port groups |
| `staging/ctl/cmpblk.py` | our blocks against the vendor's, word for word |
| `staging/ctl/vendor_eram3.sh` | the vendor's ERAM, one entry at a time |
| `staging/ctl/vendor-fresh.json` | the vendor's two-pass dump |
| `staging/ctl/vendor-vstats.txt` | the vendor's named statistics |
