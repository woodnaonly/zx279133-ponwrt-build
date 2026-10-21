"""Work out what the ERAM is supposed to hold, and turn it into console commands.

    python tools/eram-map.py --map
    python tools/eram-map.py --plan > staging/ctl/eram-plan.txt

Why this exists: the ERAM is DRAM, so it comes up empty after a power cut and
the driver has to fill it in.  Every run of the driver that was built from the
wrong table list wrote its tables at the wrong blocks, and the switch engine
read the blocks it was told about and found nothing there.  The fix is to put
the tables where the 53-item list (np_133.ko's g_core_sdt_info) says they
belong, starting at block 0x3000 - the ERAM's own first free block, as the
vendor's allocator computes it and as the board confirms.

This script takes the table list straight out of the driver's source, so it
cannot drift away from what the driver does, and it emits the same writes the
driver's own init functions make - as raw ERAM block writes, so that they can
be applied to a board that is running an older build.

The addressing is the vendor's own, out of se_eram_table_set_by_sdt_info():

    bit_off = width * index
    addr    = (base << 7) + (bit_off & ~0x7f)
    word    = (bit_off >> 5) & 3
    bit     = bit_off & 0x1f

so one block is 128 bits and a table entry that is 128 bits wide is exactly
one block, while a table whose entries are 2 bits wide keeps 64 of them in one
block.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER_CANDIDATES = [
    os.path.join(HERE, "..", "..", "ci-build-repo", "kernel-overlays", "drivers",
                 "net", "ethernet", "zte", "zx279133-np.c"),
    os.path.join(HERE, "..", "kernel-overlays", "drivers", "net", "ethernet",
                 "zte", "zx279133-np.c"),
]


def driver_path():
    for p in DRIVER_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("cannot find zx279133-np.c")


DRIVER = driver_path()

# The vendor's mode -> bit width table: mode is p3 & 7.
BIT_LEN = [1, 32, 64, 128, 2, 4, 8, 16]

# The port attribute words, as the driver's own dump of the board shows them.
# This is the a19 column of the SPA memory dump: the attribute word of every
# memory id, which is what the MF extension table (ERAM table 0x7d) mirrors.
# Index = memory id.
SPA_ATTR = {
    0: 0x00060000, 1: 0x96040001, 2: 0x96040001, 3: 0x96040001,
    4: 0x96040001, 5: 0x96040001, 6: 0x148e0000, 7: 0x00000000,
    8: 0x00000000, 9: 0x00000000, 10: 0x00042000, 11: 0x00000000,
    12: 0x00000000, 13: 0x00000000, 14: 0x00060000, 15: 0x00040000,
}

def parse_proto_action(path=DRIVER):
    """The protocol action table, straight out of the driver's source: the
    action for each of the 128 protocol numbers, 0xff meaning the vendor has
    no entry for that protocol."""
    with open(path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    m = re.search(r"zx279133_9132_proto_action\[128\]\s*=\s*\{(.*?)\n\};",
                  src, re.S)
    if not m:
        raise SystemExit("the driver has no protocol action table")
    vals = [int(x) for x in re.findall(r"\b(\d+)\b", m.group(1))]
    if len(vals) != 128:
        raise SystemExit("the protocol action table has %d entries" % len(vals))
    return vals


def parse_sdt(path=DRIVER):
    """The table list, straight out of the driver's source."""
    with open(path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    m = re.search(r"zx279133_core_sdt_info\[\]\[4\]\s*=\s*\{(.*?)\n\};",
                  src, re.S)
    if not m:
        raise SystemExit("the driver has no zx279133_core_sdt_info table")
    body = m.group(1)
    # The driver's own tables come after the vendor's; they are not part of
    # the layout the hardware is told about.
    cut = body.find("/* this driver's own tables */")
    if cut > 0:
        body = body[:cut]
    items = re.findall(
        r"\{\s*(0x[0-9a-fA-F]+)\s*,\s*(\d+)\s*,\s*(0x[0-9a-fA-F]+)\s*,"
        r"\s*(0x[0-9a-fA-F]+)\s*\}", body)
    return [(int(a, 16), int(b), int(c, 16), int(d, 16))
            for a, b, c, d in items]


def table_map(items, eram_base=0x3000):
    """id -> (base block, bits per entry, depth in blocks)

    The pool starts at block 0x3000, which is where the ERAM starts - the
    board says so: below 0x3000 a write does not land and a read comes back
    with a page table entry out of the SoC's memory instead.
    """
    out = {}
    for sdt_no, typ, a, b in items:
        if typ in (1, 5):
            bit_len = BIT_LEN[a & 7]
            depth = (bit_len * b + 127) >> 7
            out[sdt_no] = (eram_base, bit_len, depth)
            eram_base += depth
    return out


class Eram(object):
    """The ERAM as a picture of 128-bit blocks, so that a write can be made
    the way the vendor makes it: read the window, change the bits, write it
    back."""

    def __init__(self):
        self.blocks = {}

    def block(self, index):
        return self.blocks.setdefault(index, [0, 0, 0, 0])

    def set_bits(self, index, bit_off, width, value):
        """Merge a value of `width` bits into the window at `index`, at
        `bit_off` from its start - the vendor's se_eram_table_set()."""
        w = self.block(index)
        for i in range(width):
            bit = bit_off + i
            word, pos = bit // 32, bit % 32
            if (value >> i) & 1:
                w[word] |= 1 << pos
            else:
                w[word] &= ~(1 << pos) & 0xffffffff

    def set_word(self, index, word, value):
        self.block(index)[word] = value & 0xffffffff

    def writes(self):
        """(block, w0, w1, w2, w3) for every block that was touched."""
        return [(b, self.blocks[b][0], self.blocks[b][1], self.blocks[b][2],
                 self.blocks[b][3]) for b in sorted(self.blocks)]


def build(items, proto_action=None):
    """The ERAM the driver is meant to leave behind."""
    tm = table_map(items)
    eram = Eram()
    if proto_action is None:
        proto_action = parse_proto_action()

    def put(sdt_id, index, width, value):
        base, bit_len, _ = tm[sdt_id]
        bit_off = bit_len * index
        eram.set_bits(base + bit_off // 128, bit_off % 128, width, value)

    # 0x7d, the MF extension table: one 128-bit entry per SPA memory id, the
    # first word carrying the port attribute word.
    base, _, _ = tm[0x7d]
    for mem_id, attr in SPA_ATTR.items():
        eram.set_word(base + mem_id, 0, attr)

    # 0x18, the switch's protocol action table: two bits per (port, protocol),
    # the index being proto | (port << 7).  2 hands the frame to the CPU.
    for port in range(1, 8):
        for proto, action in enumerate(proto_action):
            if action == 0xff:
                continue
            put(0x18, proto | (port << 7), 2, action)

    # 0x19, the reason map: reason 0 (OMCI) goes to CPU queue 7.  The queue
    # id is in bits 0..4 of byte 3.
    put(0x19, 0, 32, 7 << 24)

    # 0x22, the global config table: the CPU base queue id 0x193 in bits
    # 17..25 of word 3, plus the mul ip mode and the ipv6 crc32 mode.
    base, _, _ = tm[0x22]
    eram.set_word(base, 3, (0x193 << 17) | (1 << 16) | (1 << 13))

    # 0x27, the outport map: outport 15 - the CPU's own - is the real port 15
    # on queue 0x193.  The entry is 16 bits wide: the real port in bits 0..5,
    # the queue id in bits 6..14.
    put(0x27, 0x0f, 16, 0x0f | (0x193 << 6))

    # 0x23, the output VLAN check: on for every port the vendor turns it on.
    for p in list(range(0, 8)) + list(range(0x10, 0x31)):
        put(0x23, p, 1, 1)

    # 0x24, the port bind: no port is bound to another.
    for p in range(0, 64):
        put(0x24, p, 1, 0)

    # 0x25, the port PVID: none, because this is a transparent bridge.
    for p in list(range(0, 8)) + list(range(0x10, 0x31)):
        put(0x25, p, 16, 0)

    return tm, eram


def group_writes(writes):
    """Group the block writes that share a payload, so that the console
    command can be a short loop instead of one long list.

    A group is a set of blocks that lie at a constant stride; the stride can
    be 1 (consecutive) or 2 (every other one), which is what the protocol
    action table looks like: two blocks per port, alternating."""
    by_payload = {}
    for block, w0, w1, w2, w3 in writes:
        by_payload.setdefault((w0, w1, w2, w3), []).append(block)

    groups = []
    for payload, blocks in by_payload.items():
        blocks = sorted(blocks)
        run = [blocks[0]]
        for b in blocks[1:]:
            stride = b - run[-1]
            prev = run[-1] - run[-2] if len(run) > 1 else None
            if stride in (1, 2) and (prev is None or stride == prev):
                run.append(b)
            else:
                groups.append((payload, list(run)))
                run = [b]
        groups.append((payload, run))
    return groups


def emit_group(payload, blocks, con):
    """One console command (or a short loop) that writes these blocks.

    The values carry a 0x prefix: the console parses them with kstrtou32()
    and a base of zero, so a bare "96040001" would be read as the decimal
    number 96040001 and a bare "00060000" as the octal number 60000.
    """
    w0, w1, w2, w3 = payload
    values = "0x%08x 0x%08x 0x%08x 0x%08x" % (w0, w1, w2, w3)
    one = 'echo "ew %%d %s" > %s' % (values, con)
    if len(blocks) == 1:
        return one % blocks[0]
    seq = " ".join(str(b) for b in blocks)
    return 'for b in %s; do echo "ew $b %s" > %s; done' % (seq, values, con)


def serial_lines(items, con="/sys/kernel/debug/zx279133-9132"):
    """The console commands that write the tables, one line each, with the
    line kept short enough for the console's readline."""
    _, eram = build(items)
    writes = eram.writes()
    groups = group_writes(writes)
    lines = []
    cur = ""
    for payload, blocks in groups:
        cmd = emit_group(payload, blocks, con)
        if cur and len(cur) + len(cmd) + 2 > 700:
            lines.append(cur)
            cur = cmd
        else:
            cur = (cur + ";" + cmd) if cur else cmd
    if cur:
        lines.append(cur)
    return lines, writes


def oneline(items, con="/sys/kernel/debug/zx279133-9132"):
    """One console command that writes every table, short enough to be a
    single line.

    The console's line buffer is not long, and the board's power is cut every
    ninety seconds or so - a patch that has to be sent as three lines can
    straddle a power cut and lose the first of them.  So the tables that
    repeat (the port attributes, the protocol action table) are written by a
    shell loop over the numbers, and the rest are written one at a time.
    """
    tm, eram = build(items)

    def blocks_of(sdt_id):
        return [b for b, w in eram.writes()
                if any(b == tm[sdt_id][0] + i for i in range(0, 0))]

    cmds = []

    # 0x7d, the port attribute word: mem ids 1..5 all carry the same word.
    base = tm[0x7d][0]
    same = {}
    for mem in range(0, 16):
        w = eram.block(base + mem)
        same.setdefault(tuple(w), []).append(mem)
    for payload, mems in same.items():
        if len(mems) == 1:
            cmds.append('echo "ew %d 0x%08x 0x%08x 0x%08x 0x%08x" > %s'
                        % (base + mems[0], payload[0], payload[1], payload[2],
                           payload[3], con))
        else:
            seq = " ".join(str(m) for m in mems)
            cmds.append('for m in %s; do echo "ew $((%d+m)) 0x%08x 0x%08x '
                        '0x%08x 0x%08x" > %s; done'
                        % (seq, base, payload[0], payload[1], payload[2],
                           payload[3], con))

    # 0x18, the protocol action table: two blocks per port, and every port
    # gets the same two blocks.
    base18 = tm[0x18][0]
    even = eram.block(base18 + 2)
    odd = eram.block(base18 + 3)
    same_even = all(tuple(eram.block(base18 + 2 * p)) == tuple(even)
                    for p in range(1, 8))
    same_odd = all(tuple(eram.block(base18 + 3 + 2 * p)) == tuple(odd)
                   for p in range(0, 6))
    if same_even and same_odd:
        cmds.append('for p in 1 2 3 4 5 6 7; do echo "ew $((%d+2*p)) 0x%08x '
                    '0x%08x 0x%08x 0x%08x" > %s; echo "ew $((%d+1+2*p)) 0x%08x '
                    '0x%08x 0x%08x 0x%08x" > %s; done'
                    % (base18, even[0], even[1], even[2], even[3], con,
                       base18, odd[0], odd[1], odd[2], odd[3], con))
    else:
        for block, w0, w1, w2, w3 in eram.writes():
            pass

    # Everything else, one write each.
    skip = set()
    for p in range(1, 8):
        skip.add(base18 + 2 * p)
        skip.add(base18 + 1 + 2 * p)
    for mem in range(0, 16):
        skip.add(base + mem)
    for block, w0, w1, w2, w3 in eram.writes():
        if block in skip:
            continue
        cmds.append('echo "ew %d 0x%08x 0x%08x 0x%08x 0x%08x" > %s'
                    % (block, w0, w1, w2, w3, con))

    return cmds


def pack(cmds, width=900):
    """The commands, packed into lines that the console can take.  A line
    holds whole commands only - a shell loop has to stay in one piece."""
    lines = []
    cur = ""
    for c in cmds:
        if cur and len(cur) + len(c) + 1 > width:
            lines.append(cur)
            cur = c
        else:
            cur = (cur + ";" + c) if cur else c
    if cur:
        lines.append(cur)
    return lines


def sdt_nodes(items, eram_base=0x3000, stat_base=9 << 12):
    """The SDT descriptor of every ERAM table: the pair of words the driver
    hands to the switch chip to tell it where a table is and how wide one of
    its entries is.

    This is zx279133_se_sdt_eram_write()'s node:

        node[0] = depth << 2
        node[1] = (16 * (base & 0xffff)) | ((0x10 | (mode << 1)) << 24)

    and the descriptor is written to the chip's SMMU0 with the address
    register set to the table's id and the command register set to
    0xa8000000.  The console can do that too, with the driver's "nwp"
    command, which writes a list of (offset, value) pairs - so a table can
    be moved on a board that is already running.
    """
    out = []
    for sdt_no, typ, a, b in items:
        if typ not in (1, 5):
            continue
        bit_len = BIT_LEN[a & 7]
        depth = (bit_len * b + 127) >> 7
        out.append((sdt_no, depth, eram_base, a & 7))
        eram_base += depth
    return out


def sdt_commands(nodes, cmd=0xa8000000, addr_hi=0, cmd_lo=0):
    """The console commands that write those descriptors."""
    cmds = []
    for sdt_no, depth, base, mode in nodes:
        node0 = (depth << 2) & 0xffffffff
        node1 = ((16 * (base & 0xffff)) | ((0x10 | (mode << 1)) << 24)) & 0xffffffff
        cmds.append("nwp 0x48008 0x%x 0x48010 0x%x 0x4800c 0x%x 0x48004 0x%x"
                    % (sdt_no | addr_hi, node1, node0, cmd | cmd_lo))
    return cmds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", action="store_true",
                    help="print where every table starts")
    ap.add_argument("--plan", action="store_true",
                    help="print the console commands that write the tables")
    ap.add_argument("--serial", action="store_true",
                    help="print the console commands, short enough for the "
                         "console's readline")
    ap.add_argument("--oneline", action="store_true",
                    help="print one console command that writes every table")
    ap.add_argument("--sdt", action="store_true",
                    help="print the console commands that move the chip's "
                         "table descriptors")
    ap.add_argument("--addr-hi", default="0",
                    help="the high bits of the SMMU0 address register")
    ap.add_argument("--cmd-lo", default="0",
                    help="the low bits of the SMMU0 command register")
    ap.add_argument("--only", default="",
                    help="only these tables, as a comma separated list of hex ids")
    a = ap.parse_args()

    items = parse_sdt()
    tm, eram = build(items)

    if a.map:
        print("%d tables, ERAM pool from block 0x3000" % len(items))
        for sdt_id in sorted(tm):
            base, bits, depth = tm[sdt_id]
            print("  0x%02x  base %6d  %3d bits per entry  %4d blocks"
                  % (sdt_id, base, bits, depth))
        return 0

    if a.plan:
        writes = eram.writes()
        print("# %d blocks to write" % len(writes))
        for block, w0, w1, w2, w3 in writes:
            print('echo "ew %d %08x %08x %08x %08x" > '
                  '/sys/kernel/debug/zx279133-9132'
                  % (block, w0, w1, w2, w3))
        return 0

    if a.serial:
        lines, writes = serial_lines(items)
        print("# %d blocks in %d lines" % (len(writes), len(lines)))
        for l in lines:
            print(l)
        return 0

    if a.sdt:
        nodes = sdt_nodes(items)
        cmds = sdt_commands(nodes, addr_hi=int(a.addr_hi, 0),
                            cmd_lo=int(a.cmd_lo, 0))
        lines = pack(cmds, 850)
        print("# %d table descriptors in %d lines" % (len(cmds), len(lines)))
        for l in lines:
            print(l)
        return 0

    if a.oneline:
        cmds = oneline(items)
        lines = pack(cmds)
        print("# %d commands in %d lines" % (len(cmds), len(lines)))
        for l in lines:
            print(l)
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
