# The bench was measuring nothing, and what the board said once it was

Date: 2026-10-23. Scope: G2, the LAN path of the SK-D840N.

This note records a round in which three separate experiments produced
conclusions that were all wrong, in the same way, and what the board said
once the mistake was found.  The measurements are the useful part; the
mistakes are the useful part too, because they are the kind that survive
a dozen rounds if nothing writes them down.

## 1. The PC was not talking, and the bench did not know

Three rounds in a row measured a board that was not being asked
anything, and each of them produced a clean, confident and wrong
conclusion.

**Round 136 (12:55).**  The PC side of a round is started by a small
script that waits for a marker in the console log before it pings.
Round 136's command file wrote `===== P0 the counters before the pings`
as its marker, and the script waits for `P0 before`.  The two strings
are not the same, so the pings never started, and the +20 packets the
SMAC1 block showed over the window were the board's own traffic.  The
round was read as "frames reach the chip and stop there".

**Round 138 (13:11).**  The runner gives up on the shell after 150
seconds and exits.  It gave up at 13:12:09, which killed the pinger
with it, and the board - whose kernel was up and whose DSA ports were
up - ran for the rest of the window without a single packet from the
PC.  A round that was supposed to compare counters before and after
forty seconds of pings compared two silent samples.

**13:14 to 13:23, by hand.**  The pings were restarted with a
PowerShell `Start-Job`.  A `Start-Job` belongs to the PowerShell
process that created it, and every shell command in this project runs in
a process of its own, so the job died with the command that made it.
The PC's transmitted byte counter stood still for eight seconds while
the log showed a ping every 0.8 seconds.

The symptom that proves it, and the one to check first from now on:

```
Get-NetAdapterStatistics -Name '以太网'
```

The transmitted byte counter has to move while the pings run.  If it
does not, nothing that the board says about the counters means
anything.

**What fixed it.**  `staging/ctl/macfix.py` now installs the neighbour
entry first and does not wait for the kernel's "ZZMAC" line, which the
trial image never prints.  A new `ARP_PROBE=1` mode drops the entry
instead of installing it, so the PC has to ask the board for its
address, and it reports the neighbour state on every round of the
ping.  `staging/ctl/g2round6.sh` takes the same mode and then runs
macfix.py's loop instead of its own pinger.

## 2. What the board said, with the PC actually talking

Window 13:10:41 to 13:25:36, the run133 kernel, the PC in the jack
that DSA calls lan2.

| reading | value | meaning |
|---|---|---|
| PC transmitted bytes | moving | the PC was talking |
| board's `eth0` RX | 0 | nothing arrived on the SoC's own MAC |
| board's `lan-cpu0` RX | 0 | nothing arrived on the DSA CPU port |
| board's `lan1..lan4` RX | 0 | nothing arrived on any jack |
| board's `lan2` TX | 28 packets | the board thinks it sent 28 |
| XMAC0's own TX frames / TX good | 0 / 0 | the MAC moved nothing |
| SMAC1's counters at +0x714 | +1 packet in 147 s | the jack the PC is in |
| ERAM 0x1f, the flood group table | entry 0, 1 and 15 all 0x0 | nothing is flooded anywhere |
| SPA memory 1, 2 | 0xa6a5, 0xa6a5 | the UNI ports' entry programs |
| SPA memory 2, address 19 | 0x96040001 | the UNI port's attribute word |
| SPA memory 5, address 0 | 0x8015 | the CPU port's entry program |
| ERAM 0x18, groups 0..7 | the vendor's table B, byte for byte | the action table is right |
| XMAC0's link word at +0x84 | 0x0000 | the MAC says there is no lane |
| XPCS0's status at +0x90 | 0x380b | the PCS says the lane is up |

**The two words that do not agree** are the XMAC's own link word and
the PCS's status word.  The SerDes lane is up and the MAC does not know
it.  That is the first thing in the chain that is broken, and it is
between the board and the chip, not inside the chip.

**The flood group table being empty** is the second thing.  The port
attribute word 0x96040001 says "unknown-DA flood number 1", and the
group that number points at is empty, so an unknown unicast frame -
and every broadcast - has nowhere to go.  The CPU port is in no flood
group, so a frame addressed to the board is dropped inside the chip.
Writing 0x8000 into ERAM 0x1f entry 0 does put the CPU port into the
group, and the write reads back; whether that is the right bit is not
yet established.

## 3. The DSA CPU port is in no bridge

`br-lan` carried lan1, lan2 and lan4.  The DSA CPU port, `lan-cpu0`,
was in no bridge at all:

```
trial: link: lan-cpu0 oper=up carrier=1 flags=0x1303 rx=0 tx=12 brif= brport=
```

A bridge without the CPU port forwards between the jacks and never
reaches the host, so a frame that the chip hands to the CPU is
delivered to a netdev that belongs to no bridge and is dropped there.
The board's address is on the bridge and the frame arrives on the CPU
port; those two facts are in different places.

The kernel refuses the obvious fix: `ip link set lan-cpu0 master
br-lan` answers `RTNETLINK answers: Invalid argument`, and so does
`brctl addif`.  The same command against a bridge made by hand
(`ip link add name brtest type bridge`) succeeds, so the netdev is
willing and `br-lan` is the object that says no.  `vlan_filtering 0`
does not change the answer, and neither does taking the port out of
`brtest` first.  What is special about `br-lan` is still open.

## 4. Two corrections to what the tree was told

**The protocol action table is indexed by the chip's PPU number, and
the vendor's own load path uses the raw switch port.**  The two
numberings are one apart, and the table has a group of 128 entries per
port, so a frame can look up a group the driver never wrote.  The
driver wrote the PPU numbers 1..7; it now writes 0..7, which covers
both readings.  The table's contents were already right: the 128-byte
action array is the vendor's table B, byte for byte, and the chip's
ARP is protocol 17.

**The SOPC publish array is two channels long, not four.**  The words
the driver read for channel 0 are SMAC0's ready and send words - the
SMACs sit in front of the four internal PHYs and their array starts
eight words earlier - and the word for channel 3 was a write past the
end of the send array, into the middle of the SOPC's own register
block.  The live board reads 0x342a4 = 0, 0x342a8 = 1, 0x342b0..0x342bc
= 1 and 0x342c0..0x342c8 = 1, which is what two channels explain.

## 5. The MAC that faces the chip had never been reset

The board's own counter said fifty-one packets sent; the XMAC's own
transmit counter said zero.  The frames were queued into the IDM's
descriptor ring and nobody took them out of it.

The reason is in the NPPT's SOPC array, and the vendor's own code says
so.  A MAC is published - its send-enable is written - only after its
own "clear-over-ready" word says it is ready.  The live board reads:

| word | address | value |
|---|---|---|
| XMAC0 ready | 0x342a4 | **0** |
| XMAC1 ready | 0x342a8 | 1 |
| SMAC0..3 send | 0x342b0..0x342bc | 1, 1, 1, 1 |
| XMAC0, XMAC1, SMAC6 send | 0x342c0..0x342c8 | 1, 1, 1 |

XMAC0's ready bit is not set.  A MAC that has not attached its output
queue does not take a descriptor.

And the driver never reset it.  The lane's bring-up had two branches:
one that programmed the PCS and then the MAC, and one that inherited the
bootloader's PCS state because the PCS already said the link was up -
which it did, 0x380b.  The inheriting branch programmed the MAC but
skipped its reset, on the grounds that "the soft reset word is shared
with the PCS, and the XMAC is at its reset state already".

The word is shared; the bit is not.  XMAC0's reset is bit 10 of that
word, and pulsing it touches the XMAC alone - which is why the other
branch can pulse it without touching the PCS.  The vendor's bootloader
resets XMAC0 before it configures it ("xmac 0 speed set 2 in sgmii"),
and so does the vendor's Linux, whose XMAC0 control words read zero
because they were reset and never programmed again.

So the inheriting branch now pulses the reset too.  The board's own
DTS already says the same thing in another place: "mainline registers
eth0 but never moves traffic through it ... Only U-Boot has moved
traffic (11 MB) - so jack 4 is optional, not the bring-up gate."

## 6. What is still unknown

| item | why |
|---|---|
| whether the frame reaches the 9132 at all | the SMAC1 counters moved once in 147 s, and the PC was talking; the counter's own meaning is not established |
| why the XMAC's link word is 0 while the PCS's status says up | the XPCS's vendor registers read 0, because the driver inherits the bootloader's PCS instead of programming it |
| why the kernel refuses lan-cpu0 on br-lan but allows it on a bridge of its own | the difference has not been found |
| which bit of the flood group table is the CPU port | the table is empty on this board, so there is nothing to compare against |

## 6. The changes that went out

run134 (`2088342`): the NPPT is not reset a second time, a failing
`tm_prepare()` does not stop the 9132 from being programmed, the RED
and QMG words are restored to the vendor's, the isolation matrix's
diagonal is written with the right arithmetic, and the trap port's
attribute word is zero.

run135 (`0ac4024`): the tree's own `tm.c` is carried in the overlay so
that the second prepare does not reset the NPPT, so that a failing step
says which one it was, and so that the debugfs console refuses an
unaligned register read - the round that tried it crashed the board.

run136 (`ed7fa48`): the DSA CPU port joins the LAN bridge, the
protocol action table covers groups 0..7, and the SOPC publish array is
two channels long.
