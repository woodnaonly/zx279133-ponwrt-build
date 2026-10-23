# 68 - The vendor's live per-port attributes, and the two words nobody had read

Date: 2026-10-23.  Scope: the G2 gate - "the PC can reach the board over the
9132's LAN ports".

Every number below was read on a live board.  The reference values come from
the vendor's own firmware on the same board, on the same cable, with the same
PC.

---

## 1. What this capture is, and why it was missing

Until now the comparison against the vendor was made on the **NPPT register
window**: twenty-four blocks, two hundred and fifty-six words each, read with
`/proc/pon/tm_debug/module_reg` on the vendor's board and with the driver's
own `regs` command on ours (docs/66, docs/67).

That window is **not** where the 9132 keeps its per-port attributes.  They
live in the SPA block's *indirect* RAM: one twenty-word row per port.  The
vendor's own map of those rows is in `staging/ctl/vendor-indmap.txt`:

```
  spa  spa_port_attr_pon_0       reg(0x 15 27 20 0)  ram( 0  20  32)
  spa  spa_port_attr_smac_0      reg(0x 15 27 20 0)  ram( 1  20  32)
  spa  spa_port_attr_smac_1      reg(0x 15 27 20 0)  ram( 2  20  32)
  spa  spa_port_attr_smac_2      reg(0x 15 27 20 0)  ram( 3  20  32)
  spa  spa_port_attr_smac_3      reg(0x 15 27 20 0)  ram( 4  20  32)
  spa  spa_port_attr_xmac_0      reg(0x 15 27 20 0)  ram( 5  20  32)
  spa  spa_port_attr_xmac_1      reg(0x 15 27 20 0)  ram( 6  20  32)
  spa  spa_port_attr_rgmii       reg(0x 15 27 20 0)  ram( 7  20  32)
  spa  spa_port_attr_loop        reg(0x 15 27 20 0)  ram( 8  20  32)
  spa  spa_port_attr_pon_1       reg(0x 15 27 20 0)  ram(14  20  32)
  spa  spa_port_attr_trap(cpu)   reg(0x 15 27 20 0)  ram(15  20  32)
  spa  spa_port_attr_wifi_ssid_0 reg(0x 15 27 20 0)  ram(16  20  32)
  ... up to spa_port_attr_wifi_ssid_23
```

So the **ram id is the port id** our driver's debugfs `r <mem_id> <word>`
command uses, and the row is twenty words wide.  The ports of this board:

| ram id | port | what it is |
|---|---|---|
| 0 | pon_0 | the PON side |
| 1..4 | smac_0..smac_3 | the four internal PHYs, chip ports 1..4 |
| 5 | xmac_0 | the 9132's first SerDes MAC |
| 6 | xmac_1 | the 9132's second SerDes MAC |
| 7 | rgmii | the RGMII conduit |
| 8 | loop | the loopback port |
| 14 | pon_1 | the second PON side |
| 15 | trap | the CPU trap port - the one the SoC is reached through |
| 16..39 | wifi_ssid_0..23 | the Wi-Fi SSID ports |

**smac_1 is chip port 2**, and chip port 2 is the jack the PC is in: the PHY
that negotiated (0x0b, BMSR 0x796d), the port the vendor's own FDB learned
the PC's MAC on, and the port the vendor's own statistics count as "port id
2".

## 2. How the capture was taken

The vendor's firmware reads these rows with
`/proc/pon/tm_debug/module_ind_ram`:

```sh
dmesg -c
echo '-r spa_port_attr_smac_1 id=0 cnt=20' > /proc/pon/tm_debug/module_ind_ram
dmesg | tail -70
```

Two details cost an hour and are worth writing down:

* `cnt` may not exceed the row's depth.  `cnt=24` on a twenty-word row
  prints `set ERR! id=0 cnt=24 ram_depth=20` and **nothing else** - no
  values, no error return, just a success line.  `cnt=20` works.
* the CPU port's row is called **`spa_port_attr_trap(cpu)`**, with the
  `(cpu)` in the name.  `spa_port_attr_trap` finds no row and prints nothing.

The dmesg has to be cleared before each row: the ring buffer is smaller than
the dump, and a row that scrolled out of it reads as a row that was never
there.

The result is in `staging/ctl/vendor-spa-attr.json`, one row per port, one
entry per word.  `staging/ctl/vendor_spa_attr.py` takes the capture and
`staging/ctl/parse_vendor_spa.py` turns it into that table.

## 3. The vendor's rows

```
               w0       w1       w10      w13      w17      w18      w19
pon_0      0000a6a5 0000088f 0fa0000e 00000000 04000000 00000800 90040000
smac_0     0000a6a5 0000088f 0fa0000e 00008008 04400410 00400840 96040001
smac_1     0000a6a5 0000088f 0fa0000e 00008008 04800410 00800880 96040001
smac_2     0000a6a5 0000088f 0fa0000e 00008008 04c00410 00c008c0 96040001
smac_3     0000a6a5 0000088f 0fa0000e 00008008 05000410 01000900 96040001
xmac_0     0000a6a5 0000088f 0fa0000e 00008008 05400410 01400940 96040001
xmac_1     0000a6a5 0000088f 0fa0000e 00008008 05800410 01800980 96040001
rgmii      0000a6a5 0000088f 0fa0000e 00008008 05c00410 01c009c0 96040001
loop       00039b11 0000088f 0fa0000e 00000000 06000000 02000a00 00000000
pon_1      0000a6a5 0000088e 0fa0000e 00000000 07800000 03800b80 00000000
trap(cpu)  0000a767 0000088f ...        ...      ...      ...      00000000
```

Words 2 to 9 and 11 to 16 are **zero on every port**.

Four things in that table have never been looked at on our own board:

1. **Word 17 and word 18 are per port.**  Their second byte counts
   `0x40, 0x80, 0xc0, 0x00, 0x40, 0x80, 0xc0` from smac_0 upwards - a
   stride of 0x40 that wraps - and their top byte groups the ports
   (`0x04` for pon_0 and smac_0..3, `0x05` for xmac_0, xmac_1 and rgmii,
   `0x06` for loop, `0x07` for pon_1).  That is the shape of a **queue
   allocation per port**, and the frame's own path depends on it: the RED
   hands the frame to a queue, and a queue that does not exist is a
   `req_que_disc`.
2. **Word 13** is `0x8008` on every MAC port and `0` on the PON ports and on
   the loop port.
3. **Word 19** is `0x96040001` on every MAC port and `0` on pon_0, pon_1,
   loop and trap.
4. **Word 0** is `0xa6a5` everywhere except the loop port (`0x39b11`) and
   the CPU trap port (`0xa767`).

## 4. What our own board says

Round 169 reads the same twenty words for ten of our ports with the driver's
own `r <mem_id> <word>` command and prints the RED's counters beside them,
so the two tables can be compared word by word:

```sh
for m in 0 1 2 3 4 5; do for w in $(seq 0 19); do echo "r $m $w" >/d; done; done
for m in 6 7 14 15; do for w in $(seq 0 19); do echo "r $m $w" >/d; done; done
echo regs 0x020000 8 >/d      # the RED's own configuration word
echo regs 0x0200b0 8 >/d      # the RED's core status and the discard counter
echo regs 0x020200 16 >/d     # in fwd, out fwd, out drop
```

What is known already, from the rounds before this one:

* our **word 0** matches the vendor's on every port that was read
  (`0xa6a5` on the four UNI ports, `0xa767` on the trap port, and the
  round-166 readings of mem 0..7 agree);
* our **words 2 and 3** are zero, which is what the vendor has everywhere;
* our **word 19** was `0x96040001` on smac_1 and `0` on the trap port in the
  round-166 reading, which is what the vendor has;
* our **word 10** was `0x0fa0000e` and **word 13** was `0x00008008` on the
  XMAC row, which is what the vendor has.

So the parts that were never compared are **words 17 and 18**, and they are
the ones that look like a queue map.

## 5. The FDB, and two more port facts

The vendor's FDB (`echo 1 > /proc/pon/tm_debug/tm_mactable_fdb_show`) has
**one entry**:

```
  hash table 0 :
    store in zcell. zgroup_id : 0  zblock_id : 0, zcell_id : 0, addr : 0x57
    sdt_no : 50, hash_table_id :0, hash_table_width : 1  hash_key_size : 8
    key :  mac_addr = 84:47:09:5b:bd:2c   vid = 0x0000
    result : sa_mac_ctrl = 0x0  da_mac_ctrl = 0x0  vid = 0x0000
             port_id = 2  port_is_ssid_flag = 0x0
  ddr_items 0   ram_items 1   reg_items 0
```

`84:47:09:5b:bd:2c` is the PC's own address, learned on **port 2** - the
jack the PC is in.  The board's own address is not in the table: the CPU
port matches the destination address itself, out of its own SPA row.

Two more facts from the same board, both about port 2:

```
  sw_port_get_port_isolate  ->  Isolate.enable = 0
  sw_vlan_get_port_pvid     ->  Pvid.pvid = 0   Pvid.priority = 0
```

## 6. The files of this round

| file | what it holds |
|---|---|
| `staging/ctl/vendor_spa_attr.py` | takes the capture over ssh |
| `staging/ctl/parse_vendor_spa.py` | turns the raw dmesg into the table |
| `staging/ctl/vendor-spa-attr.txt` | the raw capture, thirty-six ports |
| `staging/ctl/vendor-spa-attr.json` | the table: port -> word -> value |
| `staging/ctl/vendor-tables.txt` | the `/proc/pon/tm_debug` inventory |
| `staging/ctl/exp169.txt`, `go-round169.sh` | the reading round on our board |
