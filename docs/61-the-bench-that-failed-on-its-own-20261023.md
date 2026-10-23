# The bench that failed on its own, and the five knobs the run 133 round turns

**2026-10-23.** Three of the last four rounds measured nothing at all, and
none of the three was the board's fault.  This note records what the bench
did, what it cost, and what the next round measures.

## 1. Runs 130, 131 and 132 never built

Run 130 added the BMU's BPPE table commands to the debugfs console.  The
command reads the table as sixteen-bit big-endian indices, and it was
written that way with

```c
writew(cpu_to_le16(i), eth->bmu_mem + 2 * i);
```

`cpu_to_le16()` is fine; the mistake was `iowrite16le()`, which does not
exist.  The kernel's `include/linux/io.h` offers `iowrite8()`, `iowrite16()`,
`iowrite32()` in the host's own byte order and `iowrite16be()`,
`iowrite32be()`, `iowrite64be()` in the other one.  There is no little-endian
byte-order accessor, because `writel()` on a little-endian machine *is* the
little-endian access.

So runs 130, 131 and 132 all failed to compile the module, and the releases
page stayed on run 129 for four hours.  Run 133 (`3b07706`) fixed the line
and the run went green on the initramfs job.

The gate now catches it: `tools/prepush.py` rejects any `iowrite*` or
`ioread*` whose suffix is a little-endian variant, and any access with a bit
width the kernel does not have.

## 2. The serial relay was not there

At 11:07 the round started, and nothing came back.  The relay - the process
that owns COM3 and puts one line on the wire at a time - had died at some
point after 07:14.  The runner waited the full four hundred and twenty
seconds for a command that was never sent, and the power window was gone.

The relay is a long-lived process and nothing was watching it.  The plug
watcher had gone the same way.

## 3. The TFTP server was not there either

The next round, at 11:13, got as far as the loader and no further:

```
TFTP from server 192.168.9.25; our IP address is 192.168.9.1
Loading: *TTTTT
Retry count exceeded; starting again
```

The server on the PC had died as well.  It was started by hand at some
point on 2026-10-12 and never again.

Two things came out of this.  The runner now repeats the `tftpboot` command
five times instead of sending it once and waiting five minutes: the loader
sits at its prompt after its own retry count and takes a fresh `tftpboot`
happily, so a server that comes up late is no longer fatal.

And `tools/benchwatch.ps1` now watches all three of them - the relay, the
plug watcher and the TFTP server - and starts whichever is missing every
twenty seconds.

## 4. What the run 133 round measures

Run 133 carries the RED's vendor configuration including the
`all_active_mode` bit, the CPU trap as the conduit with its own entry
program, the vendor's outport map, the target jack's chip port in the
transmit descriptor and the vendor's LAN descriptor word.  What it does
*not* carry is the trap port's attribute word, because that fix is in the
commit after it.

So the round turns five knobs at runtime, one at a time, and reads the
chip's own counters around each one.  The counters are the switch engine's
and the IDM's, not the console's: `0x200b0` is the RED's counter block,
`0x201c0` the IDM's forwarded count, `0x20204` the switch's own statistics
and `0x280180` the traffic manager's.

| Step | Knob | Command | What it separates |
| --- | --- | --- | --- |
| baseline | none | `cnt2` | what the chip does on its own |
| P0 | the board's own address | `ip link set dev br-lan address d0:45:8d:84:09:00` | the address mismatch from everything else |
| P1 | the trap's attribute word | `fa 15 0x00000000` | the trap's learning and lookup flags from the rest |
| P2 | the CPU queue id | `cqid 3 3` | the queue the frames land on from the queue they are delivered on |
| P3 | the BPPE table | `bppw 16 2` | the RED's input path's buffer map from the rest |
| P4 | the protocol table | `trapall 1..7 2` | the protocol action table from the rest |

The first two are the fixes the next commit carries, applied at runtime; the
last three are the questions that are still open.

Every step is followed by forty seconds of pings from the PC and a second
reading of the counters, and the receive descriptors of the IDM's queues 0
and 8 are dumped at every marker - that is the one piece of evidence that
says whether a frame ever reached the IDM at all, and which queue it landed
in.

## 5. Run 133 moved nothing, and the reason was above all of them

Run 133 came up, booted, and answered the PC's pings with silence.  The
counters were read at every marker of the round and they did not move by
one:

```
0x200b0  0x00200e00 -> 0x00200e00
0x201c0  0x00000000 -> 0x00000000
0x20208  0x00000004 -> 0x00000004
0x20210  0x00000000 -> 0x00000000
0x20214  0x00000004 -> 0x00000004
```

through the board's address, the trap's attribute word, the CPU queue id,
the BPPE table and the protocol action table, and through the trap's DMAC
register and the trap's eth type register after that.  The PC's own side
was not the problem: the wired adapter's sent-byte counter grew by
exactly 74 bytes per echo request, and the neighbour entry was pinned to
`d0:45:8d:84:09:00`, which is the address every netdev on the board
carries.

Two things in run 133's own kernel log say what happened.

The driver printed the whole 9132 set-up **once**:

```
9132 UNI ports 1..4 and the CPU port 15 up (the vendor's configuration)
9132 switch ports 1..7 have the protocol action table at ERAM block 19624
9132 outport map: ERAM table 0x27 at block 24071 holds the vendor's 64 entries
```

and the board's diagnostic dump, printed at the end of the same probe,
read the RED's configuration word as the vendor's `0x9e`.  Thirty-four
seconds later the board read back `0x7e`.

`0x7e` is the tree's value - `zx279133_red_prepare()` writes it and
nothing else in this overlay does.  So `zx279133_red_prepare()` ran a
second time, after the probe.

And it ran a second time because something asked for the hardware to be
prepared again.  The tree's `zx279133_tm_prepare()` starts by resetting
the whole NPPT - it clears `SDET_RESET_N` and `PON_RESET_N` and waits for
the global initialisation - and the 9132's configuration lives inside
that NPPT.  A prepare that runs again resets the chip and then has to put
all of it back.

It did not put it back.  The second prepare failed after
`zx279133_red_prepare()` and before the 9132's own set-up, so the chip
was left reset and empty: no protocol action table, no outport map, no
trap words, no flood groups.  A frame from the jack has nowhere to go,
and no counter moves because no frame is ever looked at.

The fix is in two parts.  `zx279133_hardware_prepare()` returns
immediately when the hardware is already prepared - a prepare after an
unprepare is still a full one, because the unprepare clears the flag.
And `zx279133_red_vendor_fixup()` corrects the tree's saved values as
well as the registers, because the tree's `zx279133_red_restore()` writes
the saved values back, and the saved configuration word is the tree's.

## 6. The isolation matrix had an arithmetic error

The index is `src | (dst << 6)`, so a window holds the 64 sources of two
destinations: destination `2w` owns its first half, and destination
`2w+1` owns its second half, which puts its diagonal at bit
`64 + 2w + 1 = 2w + 65`.

It was written as `w + 65`.  That is the diagonal of destination `2w+1`
only for `w == 0`; for every other `w` it left the diagonal of the odd
destinations clear and set a bit that belongs to a different pair -
`(8, 15)` among them, which is a port and the CPU's own.

## 7. The two ports that are the same port

The 9132 has sixty-four ports.  The CPU trap is port 15 - the vendor's
`print_p_port()` names `SPA_CPU_MEMID` as `0xf`, and the IDM's transmit
descriptors carry `inport = 0x0f`.

The switch's own protocol action table (SE ERAM table 0x18) is indexed by
`reason | (port << 7)`, and the driver fills it for ports 1 to 7.  That is
the four UNI jacks and the CPU port the vendor's switch.ko calls XMAC0 -
a *different* CPU path, the one that goes through the RTL8372N and the
SoC's GMAC.

Both are named "the CPU port" in the vendor's own code, and they are not
the same port.  Everything in this note is about port 15.

## 6. Still open after this round

* The BMU's pool geometry: the tree's driver reserves 0x1800 buffers and
  the vendor's own numbers want 0x3400, which needs about 32 MB of reserved
  memory, and that overlaps the IDM's region at 0x9d700000.
* The switch's MAC table - the CPU's own entry in it - has never been read
  back from a working board.
* Which kernel netdev belongs to which 9132 jack.  The vendor's
  `SW_ETH_UNI` is `{4,1,2,3}` and the board's own observation says
  otherwise.
