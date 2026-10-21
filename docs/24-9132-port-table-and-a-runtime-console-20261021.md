# 24 — The 9132's real port table, and a console to change it (2026-10-21)

## 0. What this document settles

Three questions had been open since G1, and all three are now answered by
measurement rather than by inference:

1. **What does the vendor's own working system put in the 9132's per-port
   words?**  The answer is now read out of the running 4.19 system, per port,
   all twenty words.
2. **Which bit of which word is which field?**  The answer is
   `np_133.ko`'s own descriptor table, `spa_port_attr_table`, decoded.
3. **Does the 9132 forward, or does Linux?**  The answer is that the 9132
   forwards, in hardware, and that this is why the DSA design needs the
   conduit port configured — which nothing had done.

And because those answers could only be acted on with another 70-minute
build, the driver gained a runtime console: `zx279133-9132` under debugfs,
which reads and writes any word of any port.

## 1. The vendor's live 9132 configuration

### 1.1 How it was read

The vendor's `np_133.ko` exports the 9132's per-port attribute RAM by name
through `/proc/pon/tm_debug/module_ind_ram`:

```
echo spa_port_attr_smac_0 > /proc/pon/tm_debug/module_ind_ram
```

The names are the port names, and the value of each name is the SPA memory
id: `spa_port_attr_pon_0` (0), `spa_port_attr_smac_0..3` (1..4),
`spa_port_attr_xmac_0/1` (5, 6), `spa_port_attr_rgmii` (7),
`spa_port_attr_loop` (8), **`spa_port_attr_trap(cpu)` (15)**,
`spa_port_attr_vir_err` (63).

Only read forms were used.  The `-w` form was never used, and nothing on the
board was changed.

Two things about the vendor's interface cost an hour and are worth writing
down:

* **`id` and `cnt` are hex.**  `echo -r spa_port_attr_smac_0 id=17 cnt=3`
  asks for word 0x17 = 23 of a 20-word table and is rejected *silently*;
  `id=0x11 cnt=3` asks for words 17..19 and works.
* **The whole-table form overflows the kernel ring buffer.**  It prints
  twenty words as four lines each.  Dumping eleven ports and reading `dmesg`
  at the end lost the first five ports to a wrap — the log simply had a hole
  where they were.  Reading one port per SSH connection, in two ranged
  reads of ten words, does not.

The reader is `staging/ctl/vlive9132.sh`; the parser is
`staging/ctl/vlive9132.py`; the captured dump is
`staging/ctl/vlive9132-20261021.txt`.

### 1.2 What the live board says

The words that are not zero, per port (memory id in brackets):

| port | a1 | a2 | a10 | a13 | a17 | a18 | a19 |
|---|---|---|---|---|---|---|---|
| smac_0 [1] | 0x88f | 0 | 0xfa0000e | 0x8008 | 0x4400410 | 0x400840 | 0x96040001 |
| smac_1 [2] | 0x88f | 0 | 0xfa0000e | 0x8008 | 0x4800410 | 0x800880 | 0x96040001 |
| smac_2 [3] | 0x88f | 0 | 0xfa0000e | 0x8008 | 0x4c00410 | 0xc008c0 | 0x96040001 |
| smac_3 [4] | 0x88f | 0 | 0xfa0000e | 0x8008 | 0x5000410 | 0x1000900 | 0x96040001 |
| xmac_0 [5] | 0x88f | 0 | 0xfa0000e | 0x8008 | 0x5400410 | 0x1400940 | 0x96040001 |
| rgmii [7] | 0x88f | 0 | 0xfa0000e | 0x8008 | 0x5c00410 | 0x1c009c0 | 0x96040001 |
| trap(cpu) [15] | 0x88f | 0 | 0xfa0000e | 0x8008 | 0x07c00000 | 0x03c00bc0 | 0x00000000 |
| pon_0 [0] | 0x88f | 0 | 0xfa0000e | 0x8008 | 0x04000000 | 0x00000800 | 0x90040000 |

Two of these are answers to open questions:

* **a19 is 0x96040001 on every port that carries traffic and 0 on the CPU
  trap port.**  That is the word mainline already writes (D2..D8 of docs/15
  are its bits), so the attribute word was never the problem.
* **a2 — the untagged SVID — is 0 everywhere, and the vendor's VLAN tag
  mask is 0 everywhere** (`tm_port_vlan_filter_get` prints `pp_vlTagMsk:0`
  for all eight ports).  The vendor's 9132 is a *flat, untagged* learning
  bridge.  That is not a contradiction with the PVID mainline writes; it is
  a different configuration of the same chip, and section 3 says which one
  the DSA design needs.

The a17/a18 columns are the vendor's flood tables.  Their shape is
`a17 = (memory id + 16) << 22 | flags` and
`a18 = (mask << 22) | (mask << 6) | 0x800`, and the mask names the
"host side" ports of the vendor's topology (memories 6, 7, 8, 9 = XMAC1,
RGMII1, LOOP_SOPC and one more).  **No UNI port's mask contains another UNI
port, and none contains memory 5.**  In the vendor's board that is correct:
the vendor is a multi-LAN router, each UNI jack is its own network, and the
jacks are not supposed to see each other's broadcasts.  In *our* topology
memory 5 is the conduit, and the masks have to say so.

## 2. The field layout, decoded

`np_133.ko` carries the 9132's per-port descriptor table in `.data`:
`spa_port_attr_table`, 0x4a0 bytes, 74 entries of 16 bytes, each entry
being `{u32 id, u32 mask, u32 bit offset, u32 word address}`.  It has no
pointers, so it decodes without relocation work — `staging/ctl/pattr.py`
prints it.

The same numbering is what the board's own bootloader prints
(`spa_port_attr_op help` lists 74 field names), so **the bootloader and the
vendor module agree on the layout**, and the U-Boot command can be used to
read and write individual fields with no kernel at all.

The parts that matter here:

| id | field | word | bits |
|---|---|---|---|
| 1 | PC | 0 | 1..14 |
| 5 | OUTPUT_PORT | 0 | 24..29 |
| 10 | **VLAN_FILTER_MOD** | 1 | 12..13 |
| 11 | QUEUE_ID | 1 | 14..22 |
| 14 | DFT_PRI | 1 | 25..27 |
| 16 | **PORT_PVID_UNTAG** | 2 | 18..29 |
| 41 | TAG_FILTER | 9 | 24..29 |
| 68..71 | USER_DEF_AREA0..3 | 16, 17, 18, 19 | whole word |
| 72 | LLID | 15 | 12..27 |
| 73 | LLC_EN | 1 | 28 |

Three consequences:

* **The VLAN filter is a two-bit *mode* in word 1, not a VID bitmap.**  The
  "tag mask" the vendor's getter prints is somewhere else entirely, and the
  earlier reading of word 9 as "the per-port VLAN filter mask" in docs/15
  is wrong: word 9 holds the IP rate-limit fields and `TAG_FILTER`.
* **The PVID mainline writes is in the right place.**  `PORT_PVID_UNTAG` is
  word 2 bits 18..29, exactly what `zx279133_9132_port_init()` writes.
* **Words 17, 18 and 19 are the vendor's *user areas*.**  The flood tables
  and the attribute word live in user-defined space, which is why their bit
  meanings come from the vendor's switch API and not from this table.

## 3. The 9132 forwards in hardware, and that is the whole story

The decisive measurement is short.  On the running vendor system:

```
$ brctl show
bridge name    bridge id                STP enabled    interfaces
br-lan         7fff.d0458d840900        no             eth1
```

`br-lan` contains **only** `eth1` — the 9132's SMAC1, the jack the PC is
plugged into.  The 9132's CPU port (`sw`) is *not* a bridge member, and
`eth2`/`eth3` are not either.

So the frames the PC sends do not go `eth1 -> br-lan -> sw`.  They go
`eth1 -> 9132 -> 9132 -> sw`, through the chip's own forwarding: the jack
learns the PC's MAC, and the chip routes.  The vendor's `br-lan` is a bridge
with a single member.

**That is the gap in mainline.**  DSA wants the opposite arrangement: a
conduit that carries every VLAN, and per-port netdevs that the switch
itself separates.  For that, the 9132's conduit port has to be configured
like a port — and it never was.

The DSA driver numbers its ports 4..7 for the four jacks and 8 for the
conduit, and maps a DSA port to its PPU with
`ZX279133_SWITCH_DSA_PPU(port) = port - 3`.  Port 8 therefore lands on PPU
5, which is XMAC0: the SGMII side on which the SoC's own MAC reaches the
9132.  `zx279133_9132_port_init()` wrote PPU 1..4 and stopped.

### 3.1 What run101 writes

* The four jacks are unchanged: attribute word `0x96040001` (learn, lookup,
  broadcast and multicast flood, input VLAN check) and the PVID set to the
  transport VID.
* **PPU 5, the conduit, is now written too**: the same attribute word
  *without* the input-VLAN-check bit, and with no PVID.

Both differences are forced, not chosen:

* The DSA tagger puts the transport VID on the frame itself —
  `net_dsa_tag_zx279133_rtl8372n.c` says so in as many words: *"The switch
  removes the service tag before transmission"*.  So the jack must be an
  access port whose PVID is that VID, and the conduit must let a tagged
  frame through instead of checking it against an empty filter.
* The conduit is a trunk: it carries all four transport VIDs, so it must
  not have a PVID of its own.

### 3.2 The corrected memory map

The comment in `zx279133-np.c` used to say *"PPU 0 is the CPU port"*.  The
bootloader's own table says otherwise:

```
PON0=0  SMAC0=1  SMAC1=2  SMAC2=3  SMAC3=4  XMAC0=5  XMAC1=6  RGMII1=7
LOOP_SOPC=8  XMAC_B=10  PON_PTL=12  PON1=14  CPU=15  WIFI0=16  WIFI33=48
WOE0..5=49..54  VIR_ERR_PORT=63  PKT_TYPE_TABLE=64
```

So memory 0 is PON0, the CPU trap port is memory 15, and the four jacks are
memories 1..4 — which the driver already had right.

## 4. A console for the 9132

Everything above is a register write, and until now every register write
cost a 70-minute build.  The driver now grows a debugfs file:

```
$ cat /sys/kernel/debug/zx279133-9132
mem   a0    a1    a2    a9    a10   a13   a17   a18   a19
  0  0000a6a5 0000088f ...
  1  0000a6a5 0000088f ...
...

$ echo "r 1 19" > /sys/kernel/debug/zx279133-9132      # read one word
$ echo "w 1 19 0x96040001" > /sys/kernel/debug/zx279133-9132   # write, read back
```

`mem` is the SPA memory id, `addr` is the word in that port's twenty-word
attribute table.  The value is read back and printed, because the SPA
indirect path is the only way this driver talks to the 9132 and a word that
does not read back as written means the write never reached the chip.

With this, the remaining questions about the 9132 — does a flood reach the
conduit, does the transport VID survive, is the port learning — are
answerable inside one boot instead of one per build.

## 5. Two tools that stopped telling the truth

Both of these cost a window of board time each, so they are written down.

### 5.1 The serial recorder wedged in a read

`tools/serrelay.ps1` polled `BytesToRead` and then called `Read()`.  When
the board powered off, the CH340 reported bytes that never arrived, `Read()`
blocked, and the relay sat there holding COM3 — not logging, not sending,
and looking perfectly healthy in the process list.  The board's console
went dark for the whole time (2026-10-21 09:46 onwards) while the power log
kept saying "the board is fine".

The fix is a 200 ms read timeout: a read that blocks forever becomes a
`TimeoutException` the loop survives, and a genuine error reopens the port
instead of ending the recorder.

### 5.2 The power oracle could hang

`staging/ctl/jackmap.sh` polls the board over ssh.  When the board sits at
the U-Boot prompt the TCP connection is accepted but no banner is sent, and
`ConnectTimeout` only covers connection setup — so ssh blocked forever and
the recorder stopped writing, leaving a 23-minute hole in the power log that
looked like "nothing happened".

`staging/ctl/powermon.sh` replaces it.  It probes with `ping`, which cannot
block, and uses the serial log as the independent witness for a power cycle:
a power cycle always reprints the U-Boot banner.  That also resolves the
ambiguity a bare ping cannot — the board does not answer a ping while it is
at the prompt, so the last banner seen says whether "no answer" means "no
power" or "at the prompt".

## 6. Reproduction

```sh
# the vendor's live per-port words, read only
cat staging/ctl/vlive9132.sh | ssh -i ~/.ssh/id_onu root@192.168.9.1 \
    "sh -s spa_port_attr_smac_0 spa_port_attr_trap\(cpu\)"
python staging/ctl/vlive9132.py staging/ctl/vlive9132-20261021.txt

# the field layout
python staging/ctl/pattr.py staging/vendor-full/kmodule/np_133.ko

# the mainline console, on a booted mainline kernel
cat /sys/kernel/debug/zx279133-9132
echo "w 5 19 0x94040001" > /sys/kernel/debug/zx279133-9132
```
