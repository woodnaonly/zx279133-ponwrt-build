# What the blob's port-flow tags say about the ports the driver's table leaves alone

**2026-10-24.** A correction to docs/74, and the measurement that settles
whether it matters.

## The two tables, compared properly

Both blobs carry 64 PORT_FLOW entries in their tag stream, **in port
order** - entry *i* is port *i* - and the word the vendor builds from an
entry is

    word = 1 | ((value & 0x3fff) << 1) | (((value >> 16) & 0x3f) << 15) |
           (((value >> 22) & 0x7) << 21)

`staging/ctl/mcodetags.py` walks a blob's tag stream the way the driver
walks it (`zx279133_ppu_mcode_prepare()`) and prints this.

| port | this board's blob | the SR1010's blob | `zx279133_9132_port_flow[]` |
|---|---|---|---|
| 0 | 0x0000a6a5 | 0x00000463 | 0x0000a6a5 |
| 1..7 | 0x0000a6a5 | 0x00008015 | 0x0000a6a5 |
| 8 | 0x00039b11 | 0x00000001 | 0x00039b11 |
| 9 | 0x00000001 | 0x00000001 | **0x00000000** |
| 10 | 0x0000a6a5 | 0x00c0800b | 0x0000a6a5 |
| 11 | 0x00000001 | 0x00000001 | **0x00000000** |
| 12 | 0x00009f99 | 0x00ef8445 | 0x00009f99 |
| 13 | 0x00000001 | 0x00000001 | **0x00000000** |
| 14 | 0x0000a6a5 | 0x00c00459 | 0x0000a6a5 |
| 15 | 0x0000a767 | 0x00008001 | 0x0000a767 |
| 16..48 | 0x0000a6a5 | 0x0000a4d3 | 0x0000a6a5 |
| 49 | 0x00052825 | 0x0000a86d | 0x00052825 |
| 50 | 0x00052827 | 0x0000a86d | 0x00052827 |
| 51..54 | 0x00000001 | 0x0000a86d | **0x00000000** |
| 55..61 | 0x00000001 | 0x00000001 | **0x00000000** |
| 62 | 0x00000001 | 0x0000998d | **0x00000000** |
| 63 | 0x00000001 | 0x00008009 | **0x00000000** |

The 0x00000001 entries are what the two blobs have in common at the ports
they do not use: it is the word with every field zero and the enable bit
set, and it is what the SR1010's blob carries at port 8 and the ports it
does not use too.

The driver's table is this board's blob's table, exactly, on all 48 ports
the blob gives a real entry program to. It differs on the other 16 - 9, 11,
13 and 51..63 - where the blob says 0x00000001 and the driver writes
nothing at all, because `zx279133_9132_port_flow_apply()` skips every
zero:

    for (i = 0; i < 64; i++) {
            if (!zx279133_9132_port_flow[i])
                    continue;
            ret = zx279133_spa_indirect_write(eth, i, 0,
                                              zx279133_9132_port_flow[i]);
            ...
    }

## What docs/74 said, and what is wrong with it

docs/74 ended with:

> A port the vendor's table does not name - 9, 11, 13 and 51..63 - keeps
> whatever the chip reset left in it, which is what the vendor's board has
> too: the vendor's table does not touch them either.

The first half is right. The last clause is not: the blob's table names
all 64 ports, and it names those 16 with 0x00000001.

Whether that is a difference at all is one measurement, and it is the
measurement the board can make: read the SPA port attribute word 0 of a
port the driver leaves alone - `echo "r <port> 0" >/d` - and see what the
chip reset left there.

* If the reset value is 0x00000001, our board and the vendor's board have
  the same thing on those ports, and only the *table* differs.
* If the reset value is 0, our board differs from the vendor's on 16
  ports.

Sixteen ports that this board does not use, on a datapath that measures
perfect with the table as it is (ISU 7625 in and 7625 out, nothing dropped
by the ISU, ODMA 7625, RED 7578), is a fidelity gap and not a functional
one. It is recorded here so that the next round does not have to rediscover
it.

## What the faithful thing would be

Now that the build ships the board's own blob, the faithful thing is the
vendor's own behaviour: apply the blob's PORT_FLOW tags and stop keeping
a second copy of the table in the driver. The driver's tag walk already
reaches the PORT_FLOW case; it would take the entry's index as the port
and the vendor's formula for the word, which is what
`zx279133_spa_indirect_write(eth, port, 0, word)` wants.

That is a change with a measurement behind it, so it is not made on the
same round as the datapath fix.
