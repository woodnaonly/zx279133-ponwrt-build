docs: the switch engine was given a table set the chip never had

The board loads `np_133.ko`, and that module's `g_core_sdt_info` is the
set of tables the switch engine's microcode knows: 53 entries, each with
an id, a width and a number of entries.  This driver was creating a
different set - the generic `np.ko`'s, a module that is not on this board
at all - so every table the microcode looks up was declared with another
width, and the engine read it at the wrong address.

## What the vendor's set is

`g_core_sdt_info`, `.data+0x11be0`, 53 entries of 16 bytes:

| offset | meaning |
|---|---|
| +0 | the table id |
| +4 | the type: 1 = ERAM, 3 = hash, 4 = statistics |
| +8 | p3, the width of one entry |
| +12 | the number of entries |

p3 picks the width out of a table of eight: `{1, 32, 64, 128, 2, 4, 8, 16}`
bits.  The widths that matter here are 2 bits (table 0x18, the protocol
action table), 32 bits (table 0x19, the reason to CPU queue map) and 128
bits (table 0x7d, the mirror of the SPA port attributes).

Only ERAM takes a block from the ERAM pool, the pool is filled strictly in
list order, and the pool's first free block is 0x1000
(`se_smmu0_reg_table` +0x120), so every table's base follows from the
list.  The driver now creates the vendor's list, and the bases come out
the same as the vendor's:

| table | base | width | entries |
|---|---|---|---|
| 0x18 protocol action | 11432 | 2 bit | 8192 |
| 0x19 reason to CPU queue | 11560 | 32 bit | 256 |
| 0x1b port isolate | 11628 | 1 bit | 4096 |
| 0x1f group id | 11783 | 32 bit | 16384 |
| 0x7d port attribute mirror | 16545 | 128 bit | 64 |

## The protocol action table

`se_eram_table_set_by_sdt_info()` (np_133.ko 0x8ec98) does not write an
entry of a narrow table whole.  It reads the 16-byte window the entry
falls in, merges the value into it bit by bit and writes the window
back:

```
bit offset = width * index
window     = (base << 7) + (bit offset & ~0x7f)
entry      = word (bit offset >> 5) & 3, bits (bit offset & 0x1f) .. +1
```

The vendor's `port_reason_action_table_set()` builds the index as
`reason | (port << 7)`, and `tm_port_protocol_pktdeal_set()` puts the
action itself (0 or 2) into the low two bits of the byte it hands over.
The ports are the chip's own port numbers - 1 to 4 are the 9132's four
UNI jacks, 5 is XMAC0, the 9132's CPU port - which is what
`sw_port_change_to_chip_port()` converts the switch's ports into.

The driver was writing one word per (port, protocol) into a table of 32
entries, so 992 of its 1024 writes went past the end of the table, and
the ones that landed were read back as 128-bit entries where the engine
expects two bits.

## The three tables of the driver's own

The VLAN action table (id 0), the NPPT's own 16-bit protocol table (id 4)
and the port type table (id 6) are not in the vendor's list - those three
ids are free there - so they are appended after the vendor's tables and
keep their own bases.

The protocol table is the same table as 0x18 in a newer form: 16 bits
per (port, protocol) entry against the vendor's two.  The action is the
low two bits of the 16-bit entry, so the table is now written twice, once
in the driver's form and once in the form the engine reads.

## The port type table's captured contents

The factory's port type entries were written at a hard-coded block
number that came from the old list.  They now follow the table's base.
The one entry that belonged to a table the vendor does not have (the old
sdt 0x10) is gone.
