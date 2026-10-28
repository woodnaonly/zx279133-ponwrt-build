"""The SK-D840N bench, as an MCP server.

One tool call reads or writes the board's registers, the PHYs and the network
state, and hands back structured data instead of a screen full of serial
echo.  The board is reached through the serial relay that owns COM3, so this
server never opens the port itself; see bench.py for how that works.

Read tools are safe to call.  Write tools change hardware and refuse to do
anything unless they are called with confirm=True.
"""

import os
import sys
from typing import Optional, Union

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench                                    # noqa: E402
from bench import BenchError, BenchBusy, StateMismatch   # noqa: E402
import parsers                                  # noqa: E402


DEBUGFS = "/sys/kernel/debug/zx279133-9132"

mcp = FastMCP("sk-d840n-bench")

_bench = None


def the_bench():
    """The one bench this server talks to."""
    global _bench
    if _bench is None:
        _bench = bench.Bench()
    return _bench


# ------------------------------------------------------------------ helpers

def _dbg(command):
    """The shell line that runs one debugfs command on the board."""
    return "echo '%s' > %s" % (command, DEBUGFS)


def _num(value, name):
    try:
        return bench.parse_int(value, name)
    except ValueError as exc:
        raise ValueError(str(exc))


def _hex(value):
    return "0x%x" % (value & 0xFFFFFFFF)


def _bus_hint(value, kind):
    """The explanation that belongs with a read that came back all ones."""
    if value != 0xFFFF:
        return None
    if kind == "mdio":
        return ("the MDIO controller #0 (0x14f01000) answered 0xffff, which is "
                "what an absent PHY looks like.  The outer PHY (the RTL8226) "
                "is on controller #1 (0x14f02000) at PHY address 5 - read it "
                "with m45_read(5, ...) or with the kernel's mdio bus; see "
                "ci-build-repo/docs/92.")
    return ("the value came back all ones, which usually means the register "
            "is not on the bus that was addressed")


# The debugfs verbs are compiled into the kernel image, so an image that is
# older than the overlay's np.c does not have them and answers with
# -EINVAL.  "Invalid argument" on one of these verbs means exactly that.
_VERB_HINT = ("Invalid argument from the debugfs console usually means the "
              "running image does not have that verb yet: the overlay's "
              "np.c is newer than the image that is booted")


def _failure(command, timeout, result):
    """The error text for a command the board did not answer.

    A refusal and a silence are two different things and must not be
    reported as one: a refusal came back in a fraction of a second, and it
    says why.
    """
    notes = result.notes
    if notes and notes[0].startswith("the board refused the command"):
        refusal = notes[0].split(":", 1)[1].strip()
        if "Invalid argument" in refusal:
            refusal = "%s - %s" % (refusal, _VERB_HINT)
        return "the board refused %r: %s" % (command, refusal)
    return ("the board did not answer %r within %.0fs; the partial output is "
            "in the 'output' field" % (command, timeout))


def _kernel_call(command, expect, timeout=45.0):
    """Run one debugfs command on the board and bring back the answer."""
    the = the_bench()
    the.require_state("kernel")
    result = the.run(_dbg(command), expect=expect, timeout=timeout)
    if not result.matched:
        raise BenchError(_failure(command, timeout, result))
    return result


def _loader_call(command, expect, timeout=45.0, settle=2.0):
    the = the_bench()
    the.require_state("loader")
    result = the.run(command, expect=expect, timeout=timeout, settle=settle)
    if not result.matched:
        raise BenchError(_failure(command, timeout, result))
    return result


def _refuse(tool, what):
    """The answer a write tool gives when it is not given the key."""
    return {
        "ok": False,
        "refused": True,
        "tool": tool,
        "what": what,
        "hint": ("this tool changes hardware; call it again with confirm=True "
                 "when you mean it"),
    }


# ------------------------------------------------------------- read: status

@mcp.tool()
def bench_status() -> dict:
    """The state of the bench: where it is, whether the relay is alive, how
    big the log is, whether somebody else is using it, and which of the two
    states the board is in (loader or kernel).

    Call this first when the bench misbehaves.  'busy: true' means the
    relay's inbox holds a command of somebody else - do not send anything
    then."""
    return the_bench().status()


@mcp.tool()
def board_state(refresh: bool = False) -> dict:
    """Which of the two the board is in: 'loader' (the U-Boot prompt) or
    'kernel' (the ImmortalWrt shell).

    The probe is 'uname -a': the loader has no uname and answers
    "Unknown command 'uname' - try 'help'", the kernel answers with its
    uname line.  The answer is cached for ten seconds."""
    the = the_bench()
    state = the.board_state(refresh=refresh)
    return {
        "state": state,
        "evidence": the.state_evidence(),
        "loader_tools": state == "loader",
        "kernel_tools": state == "kernel",
        "note": ("the loader's md can only read what the kernel cannot (the "
                 "PON SerDes block at 0x16000000), and the kernel's debugfs "
                 "console can only read what the loader cannot (the NPPT "
                 "window); pick the tool that matches the state"
                 if state != "unknown" else
                 "the board did not answer the probe; it may be busy or the "
                 "relay may be down"),
    }


@mcp.tool()
def console_send(cmd: str, settle: float = 1.0, timeout: float = 15.0,
                 expect: Optional[str] = None) -> dict:
    """Send any command to the board and get the answer back as text.

    This is the escape hatch: anything the other tools do not cover goes
    through here.  'expect' is a regular expression; when it is given the
    call waits until the answer matches it, otherwise it waits 'settle'
    seconds and brings back whatever arrived.

    The commands that would hurt the board ('zxboot', 'up_mode get') are
    refused."""
    result = the_bench().run(cmd, expect=expect, timeout=timeout, settle=settle)
    return result.as_dict()


@mcp.tool()
def serial_tail(lines: int = 100) -> dict:
    """The last lines of the serial log, straight off the file.

    This is the record of everything the board has said, including the
    kernel's panic messages and the relay's own bookkeeping."""
    text = the_bench().tail_text(lines)
    return {"lines": text.split("\n"), "text": text}


# ------------------------------------------------------------ read: loader

@mcp.tool()
def uboot_md(addr: str, words: str = "0x40") -> dict:
    """Read memory at an absolute address, at the loader's prompt.

    'md <address> <count>' - and the count is a number of 32-bit words, not
    of bytes: 'md 0x16000000 0x40' dumps 64 words, which is 256 bytes.  This
    is U-Boot's own md, so the size is in items.

    This is the only place the PON SerDes block (0x16000000) and the Uni
    SerDes block (0x16100000) can be read at all: the kernel's /dev/mem
    refuses device addresses.

    Needs the board to be in the loader state."""
    the = the_bench()
    the.require_state("loader")
    address = _num(addr, "addr")
    count = _num(words, "words")
    if count < 1 or count > 1024:
        raise ValueError("words must be between 1 and 1024")
    # U-Boot's md prints four words to a line, so the last line of the dump
    # starts at this address.  Waiting for that line is what makes the
    # answer complete: waiting for the first line would cut the dump short.
    last_row = parsers.md_last_row_address(address, count)
    result = the.run("md %s %s" % (_hex(address), _hex(count)),
                     expect=r"(?<![\w.-])%08x:" % last_row,
                     timeout=90.0)
    rows = parsers.parse_uboot_md(result.text)
    values = [value for row in rows for value in row["values"]]
    return {
        "ok": bool(rows),
        "addr": _hex(address),
        "words_requested": count,
        "words_read": len(values),
        "bytes": len(values) * 4,
        "rows": rows,
        "values": values,
        "output": result.text,
    }


# ----------------------------------------------------------- read: the kernel

@mcp.tool()
def nppt_reg(offset: str) -> dict:
    """Read one word of the NPPT window (the 9132's registers).

    The offset is relative to the network processing block's base
    (0x19000000).  It must be a multiple of four: an unaligned offset is an
    alignment fault and it takes the kernel down with it.  It must carry the
    0x - without it the driver reads the number as decimal.

    Needs the board to be in the kernel state."""
    result = _kernel_call("reg %s" % _hex(_num(offset, "offset")),
                          r"soc reg 0x[0-9a-f]{8} \(nppt \+0x[0-9a-f]{6}\) = 0x[0-9a-f]{8}")
    rows = parsers.parse_soc_reg(result.text)
    return {"ok": bool(rows), "registers": rows, "output": result.text}


@mcp.tool()
def nppt_regs(offset: str, count: int = 64) -> dict:
    """Read a run of words of the NPPT window, one to 256 of them.

    The offset is relative to 0x19000000, must be a multiple of four, and
    must carry the 0x.

    Needs the board to be in the kernel state."""
    at = _num(offset, "offset")
    if at & 3:
        raise ValueError("the offset must be a multiple of four: an unaligned "
                         "read is an alignment fault and it takes the kernel down")
    if count < 1 or count > 256:
        raise ValueError("count must be between 1 and 256")
    last = at + 4 * (count - 1)
    result = _kernel_call(
        "regs %s %d" % (_hex(at), count),
        r"soc reg 0x%08x \(nppt \+0x%06x\) = 0x[0-9a-f]{8}" % (0x19000000 + last, last),
        timeout=90.0)
    rows = parsers.parse_soc_reg(result.text)
    return {
        "ok": bool(rows),
        "offset": _hex(at),
        "count": count,
        "registers": rows,
        "values": [row["value"] for row in rows],
        "output": result.text,
    }


@mcp.tool()
def soc_read(addr: str, count: int = 1) -> dict:
    """Read words by absolute address, outside the NPPT window.

    Only the blocks the driver allows are reachable: the two MDIO
    controllers, the PON SerDes, the PON SerDes PLL, the Uni SerDes and the
    two PCS blocks.  A read that leaves them is refused by the driver, and
    on this bus an unmapped address does not read as zero - it faults.

    Needs the board to be in the kernel state."""
    address = _num(addr, "addr")
    if count < 1 or count > 256:
        raise ValueError("count must be between 1 and 256")
    if not bench.soc_block_ok(address, count * 4):
        raise ValueError(
            "0x%x is not one of the blocks the driver will read (%s); a read "
            "outside them is refused on the board and faults the bus"
            % (address, ", ".join("0x%x" % start for start, _ in bench.SOC_BLOCKS)))
    last = address + 4 * (count - 1)
    result = _kernel_call(
        "soc %s %d" % (_hex(address), count),
        r"soc reg 0x%08x = 0x[0-9a-f]{8}" % last,
        timeout=90.0)
    rows = parsers.parse_soc_reg(result.text)
    return {
        "ok": bool(rows),
        "addr": _hex(address),
        "count": count,
        "registers": rows,
        "values": [row["value"] for row in rows],
        "output": result.text,
    }


@mcp.tool()
def spa_read(mem: str, addr: str) -> dict:
    """Read one register through the SPA's indirect window.

    'mem' picks the block and 'addr' the register inside it.  The SPA
    window starts at nppt + 0x8000: register 0x14 is the indirect command,
    0x15 its status, 0x16 the data latch and 0x17 the last value read, and
    those four are refused.

    Needs the board to be in the kernel state."""
    block = _num(mem, "mem")
    register = _num(addr, "addr")
    result = _kernel_call(
        "r %d %d" % (block, register),
        r"9132 spa: mem %d addr %d = 0x[0-9a-f]{8}" % (block, register))
    rows = parsers.parse_spa(result.text)
    return {"ok": bool(rows), "spa": rows, "output": result.text}


@mcp.tool()
def mdio_read(addr: str, reg: str) -> dict:
    """Read a register of one of the four internal PHYs, over the SoC's MDIO
    controller #0.

    'addr' is the MDIO address of the PHY, 0x0a to 0x0d for the four
    internal PHYs - not the DSA port number.  'reg' is the register number.

    The outer PHY (the RTL8226 on the 2.5G jack) is not on this bus: it is
    on controller #1, at PHY address 5.  Read it with m45_read.

    Needs the board to be in the kernel state."""
    phy = _num(addr, "addr")
    register = _num(reg, "reg")
    result = _kernel_call(
        "m %s %s" % (_hex(phy), _hex(register)),
        r"9132 mdio: addr %02x reg %02x = 0x[0-9a-f]{4}" % (phy, register))
    rows = parsers.parse_mdio(result.text)
    value = rows[-1]["value"] if rows else None
    return {
        "ok": bool(rows),
        "phy_addr": phy,
        "reg": register,
        "value": value,
        "registers": rows,
        "bus_hint": _bus_hint(value, "mdio") if value is not None else None,
        "output": result.text,
    }


@mcp.tool()
def m45_read(phy_addr: str, devad: str, reg: str) -> dict:
    """Read a Clause-45 register of the outer PHY (the RTL8226 on the 2.5G
    jack).

    'phy_addr' is the PHY's address on the outer PHY bus - 5 for the
    RTL8226.  'devad' is the clause-45 device address (0x1e is its vendor
    block, 0x1f the DSP block) and 'reg' the register inside it.

    Needs the board to be in the kernel state."""
    phy = _num(phy_addr, "phy_addr")
    devad = _num(devad, "devad")
    register = _num(reg, "reg")
    result = _kernel_call(
        "m45 %d %s %s" % (phy, _hex(devad), _hex(register)),
        r"outer phy %d devad 0x[0-9a-f]{1,2} reg 0x[0-9a-f]{1,4} = 0x[0-9a-f]{4}" % (phy,))
    rows = parsers.parse_m45(result.text)
    value = rows[-1]["value"] if rows else None
    return {
        "ok": bool(rows),
        "phy_addr": phy,
        "devad": devad,
        "reg": register,
        "value": value,
        "registers": rows,
        "output": result.text,
    }


@mcp.tool()
def xpcs_read(lane: str, devad: str, reg: str) -> dict:
    """Read a Clause-45 register of a MAC lane's PCS.

    'lane' picks the lane: 0 is the PCS of eth0's MAC (XMAC1), 1 is the LAN
    lane's PCS (XMAC0).  'devad' and 'reg' are the clause-45 address and
    register.

    Needs the board to be in the kernel state."""
    which = _num(lane, "lane")
    devad = _num(devad, "devad")
    register = _num(reg, "reg")
    result = _kernel_call(
        "xpcs %d %s %s" % (which, _hex(devad), _hex(register)),
        r"xpcs lane %d devad 0x[0-9a-f]{1,2} reg 0x[0-9a-f]{1,4} = 0x[0-9a-f]{4}" % (which,))
    rows = parsers.parse_xpcs(result.text)
    return {
        "ok": bool(rows),
        "lane": which,
        "devad": devad,
        "reg": register,
        "value": rows[-1]["value"] if rows else None,
        "registers": rows,
        "output": result.text,
    }


@mcp.tool()
def eram_read(sdt_id: str, index: str, width: str) -> dict:
    """Read one entry of an ERAM table (the switch's tables live there).

    'sdt_id' is the table id, 'index' the entry and 'width' how many bits of
    it to read.  The bare debugfs console prints the table list with 'cat'.

    Needs the board to be in the kernel state."""
    table = _num(sdt_id, "sdt_id")
    entry = _num(index, "index")
    bits = _num(width, "width")
    result = _kernel_call(
        "eg %s %d %d" % (_hex(table), entry, bits),
        r"eram table 0x%02x entry %d width %d = 0x[0-9a-f]+" % (table, entry, bits))
    rows = parsers.parse_eram(result.text)
    return {
        "ok": bool(rows),
        "sdt_id": table,
        "index": entry,
        "width": bits,
        "value": rows[-1]["value"] if rows else None,
        "entries": rows,
        "output": result.text,
    }


# ----------------------------------------------------------- read: the network

@mcp.tool()
def net_links() -> dict:
    """The netdevs of the board, with their flags, their state and their MAC.

    This is 'ip -o link show'."""
    the = the_bench()
    the.require_state("kernel")
    result = the.run("ip -o link show", expect=parsers.PROMPT_LINE_RE,
                     timeout=30.0, settle_after=0.3)
    links = parsers.parse_ip_link(result.text)
    return {"ok": bool(links), "links": links, "output": result.text,
            "notes": result.notes}


@mcp.tool()
def net_counters() -> dict:
    """The byte and packet counters of every netdev.

    This is 'cat /proc/net/dev'."""
    the = the_bench()
    the.require_state("kernel")
    result = the.run("cat /proc/net/dev", expect=parsers.PROMPT_LINE_RE,
                     timeout=30.0, settle_after=0.3)
    counters = parsers.parse_proc_net_dev(result.text)
    return {"ok": bool(counters), "counters": counters, "output": result.text,
            "notes": result.notes}


@mcp.tool()
def eth_stats(iface: str = "eth0") -> dict:
    """The driver's own counters for one interface ('ethtool -S')."""
    the = the_bench()
    the.require_state("kernel")
    result = the.run("ethtool -S %s" % iface, expect=parsers.PROMPT_LINE_RE,
                     timeout=30.0, settle_after=0.3)
    stats = parsers.parse_ethtool(result.text)
    return {
        "ok": bool(stats),
        "iface": iface,
        "stats": stats,
        "note": None if stats else "the board answered without any counter; "
                                 "ethtool may not be on it",
        "output": result.text,
        "notes": result.notes,
    }


@mcp.tool()
def dmesg_tail(lines: int = 100) -> dict:
    """The tail of the kernel log ('dmesg | tail -n N').

    This is where a panic shows up."""
    the = the_bench()
    the.require_state("kernel")
    if lines < 1 or lines > 2000:
        raise ValueError("lines must be between 1 and 2000")
    result = the.run("dmesg | tail -n %d" % lines, timeout=30.0, settle=2.0)
    return {"ok": True, "lines": result.text.split("\n"), "text": result.text}


# --------------------------------------------------------------- the snapshot

@mcp.tool()
def snapshot() -> dict:
    """One call that brings back the whole picture of the board.

    The board's state, five blocks of the NPPT window (the per-port blocks
    and the two XMACs), the four internal PHYs and the netdevs - the same
    ground win109.sh covers in its steps 9 to 13, without writing a script
    and waiting for a round.

    In the loader state only the loader's side can be read, so the kernel
    part is reported as skipped."""
    the = the_bench()
    state = the.board_state(refresh=True)
    out = {
        "state": state,
        "evidence": the.state_evidence(),
        "nppt": {},
        "phys": [],
        "links": [],
        "skipped": [],
    }
    if state == "loader":
        out["skipped"].append(
            "the board is at the loader's prompt: the NPPT window, the PHYs "
            "and the netdevs are the kernel's side and cannot be read now")
        return out
    if state != "kernel":
        out["skipped"].append("the board did not answer the state probe")
        return out

    for offset in (0x040000, 0x0c0000, 0x0e0000, 0x140000, 0x180000):
        try:
            rows = parsers.parse_soc_reg(the.run(
                _dbg("regs %s 64" % _hex(offset)),
                expect=r"soc reg 0x%08x \(nppt \+0x%06x\) = 0x[0-9a-f]{8}"
                       % (0x19000000 + offset + 0xFC, offset + 0xFC),
                timeout=90.0).text)
            out["nppt"][_hex(offset)] = [row["value"] for row in rows]
        except BenchError as exc:
            out["skipped"].append("nppt %s: %s" % (_hex(offset), exc))

    for phy in (0x0a, 0x0b, 0x0c, 0x0d):
        try:
            rows = parsers.parse_mdio(the.run(
                _dbg("m %s 0x0" % _hex(phy)),
                expect=r"9132 mdio: addr %02x reg 00 = 0x[0-9a-f]{4}" % phy,
                timeout=30.0).text)
            out["phys"].append({
                "phy_addr": phy,
                "reg": 0,
                "value": rows[-1]["value"] if rows else None,
            })
        except BenchError as exc:
            out["skipped"].append("phy %s: %s" % (_hex(phy), exc))

    try:
        out["links"] = parsers.parse_ip_link(
            the.run("ip -o link show", expect=parsers.PROMPT_LINE_RE,
                    timeout=30.0, settle_after=0.3).text)
    except BenchError as exc:
        out["skipped"].append("the netdevs: %s" % exc)

    return out


# --------------------------------------------------------------- write: the board

@mcp.tool()
def nppt_reg_write(offset: str, value: str, confirm: bool = False) -> dict:
    """Write one word of the NPPT window and read it back.

    This changes the switch's registers.  It needs confirm=True.

    The offset must be a multiple of four (an unaligned write is an
    alignment fault and it takes the kernel down) and must carry the 0x."""
    if not confirm:
        return _refuse("nppt_reg_write", "one word of the NPPT window")
    at = _num(offset, "offset")
    if at & 3:
        raise ValueError("the offset must be a multiple of four")
    number = _num(value, "value")
    result = _kernel_call(
        "regw %s %s" % (_hex(at), _hex(number)),
        r"soc reg 0x%08x \(nppt \+0x%06x\) = 0x[0-9a-f]{8}" % (0x19000000 + at, at))
    rows = parsers.parse_soc_reg(result.text)
    return {
        "ok": bool(rows),
        "offset": _hex(at),
        "written": _hex(number),
        "read_back": _hex(rows[-1]["value"]) if rows else None,
        "registers": rows,
        "output": result.text,
    }


@mcp.tool()
def mdio_write(addr: str, reg: str, value: str, confirm: bool = False) -> dict:
    """Write a register of one of the four internal PHYs and read it back.

    This changes the PHY's configuration.  It needs confirm=True.

    'addr' is the MDIO address of the PHY, 0x0a to 0x0d."""
    if not confirm:
        return _refuse("mdio_write", "a register of an internal PHY")
    phy = _num(addr, "addr")
    register = _num(reg, "reg")
    number = _num(value, "value")
    result = _kernel_call(
        "mw %s %s %s" % (_hex(phy), _hex(register), _hex(number)),
        r"9132 mdio: addr %02x reg %02x = 0x[0-9a-f]{4}" % (phy, register))
    rows = parsers.parse_mdio(result.text)
    return {
        "ok": bool(rows),
        "phy_addr": phy,
        "reg": register,
        "written": _hex(number),
        "read_back": _hex(rows[-1]["value"]) if rows else None,
        "registers": rows,
        "output": result.text,
    }


@mcp.tool()
def m45_write(phy_addr: str, devad: str, reg: str, value: str,
              confirm: bool = False) -> dict:
    """Write a Clause-45 register of the outer PHY (the RTL8226) and read it
    back.

    This changes the PHY's configuration.  It needs confirm=True."""
    if not confirm:
        return _refuse("m45_write", "a clause-45 register of the outer PHY")
    phy = _num(phy_addr, "phy_addr")
    devad = _num(devad, "devad")
    register = _num(reg, "reg")
    number = _num(value, "value")
    result = _kernel_call(
        "m45w %d %s %s %s" % (phy, _hex(devad), _hex(register), _hex(number)),
        r"outer phy %d devad 0x[0-9a-f]{1,2} reg 0x[0-9a-f]{1,4} = 0x[0-9a-f]{4}" % (phy,))
    rows = parsers.parse_m45(result.text)
    return {
        "ok": bool(rows),
        "phy_addr": phy,
        "devad": devad,
        "reg": register,
        "written": _hex(number),
        "read_back": _hex(rows[-1]["value"]) if rows else None,
        "registers": rows,
        "output": result.text,
    }


@mcp.tool()
def xpcs_write(lane: str, devad: str, reg: str, value: str,
               confirm: bool = False) -> dict:
    """Write a Clause-45 register of a MAC lane's PCS and read it back.

    This changes the lane's configuration.  It needs confirm=True."""
    if not confirm:
        return _refuse("xpcs_write", "a clause-45 register of a MAC lane's PCS")
    which = _num(lane, "lane")
    devad = _num(devad, "devad")
    register = _num(reg, "reg")
    number = _num(value, "value")
    result = _kernel_call(
        "xpcsw %d %s %s %s" % (which, _hex(devad), _hex(register), _hex(number)),
        r"xpcs lane %d devad 0x[0-9a-f]{1,2} reg 0x[0-9a-f]{1,4} = 0x[0-9a-f]{4}" % (which,))
    rows = parsers.parse_xpcs(result.text)
    return {
        "ok": bool(rows),
        "lane": which,
        "devad": devad,
        "reg": register,
        "written": _hex(number),
        "read_back": _hex(rows[-1]["value"]) if rows else None,
        "registers": rows,
        "output": result.text,
    }


@mcp.tool()
def spa_write(mem: str, addr: str, value: str, confirm: bool = False) -> dict:
    """Write one register through the SPA's indirect window and read it back.

    This changes the switch's registers.  It needs confirm=True."""
    if not confirm:
        return _refuse("spa_write", "a register of the SPA's indirect window")
    block = _num(mem, "mem")
    register = _num(addr, "addr")
    number = _num(value, "value")
    result = _kernel_call(
        "w %d %d %s" % (block, register, _hex(number)),
        r"9132 spa: mem %d addr %d = 0x[0-9a-f]{8}" % (block, register))
    rows = parsers.parse_spa(result.text)
    return {
        "ok": bool(rows),
        "mem": block,
        "addr": register,
        "written": _hex(number),
        "read_back": _hex(rows[-1]["value"]) if rows else None,
        "spa": rows,
        "output": result.text,
    }


@mcp.tool()
def eram_write(sdt_id: str, index: str, width: str, value: str,
               confirm: bool = False) -> dict:
    """Write one entry of an ERAM table and read it back.

    This changes the switch's tables - the VLAN action table, the flood
    table, the port isolation matrix.  It needs confirm=True."""
    if not confirm:
        return _refuse("eram_write", "an entry of an ERAM table")
    table = _num(sdt_id, "sdt_id")
    entry = _num(index, "index")
    bits = _num(width, "width")
    number = _num(value, "value")
    result = _kernel_call(
        "es %s %d %d %s" % (_hex(table), entry, bits, _hex(number)),
        r"eram table 0x%02x entry %d width %d = 0x[0-9a-f]+" % (table, entry, bits))
    rows = parsers.parse_eram(result.text)
    return {
        "ok": bool(rows),
        "sdt_id": table,
        "index": entry,
        "width": bits,
        "written": _hex(number),
        "read_back": _hex(rows[-1]["value"]) if rows else None,
        "entries": rows,
        "output": result.text,
    }


@mcp.tool()
def apply_xbasex(base: str = "0x140000", speed: int = 2,
                  confirm: bool = False) -> dict:
    """Apply the vendor's own 1000BASE-X sequence to the 9132's outer PHY.

    This writes seven registers of the lane's PCS and polls one of them -
    it changes the hardware.  It needs confirm=True.

    'base' is the nppt-relative base of the lane's PCS block and 'speed' the
    code (2 is 1000BASE-X)."""
    if not confirm:
        return _refuse("apply_xbasex", "the vendor's 1000BASE-X sequence")
    at = _num(base, "base")
    result = _kernel_call(
        "xbasex %s %d" % (_hex(at), speed),
        r"xbasex: done, base nppt\+0x%06x, speed %d" % (at, speed),
        timeout=120.0)
    return {
        "ok": True,
        "base": _hex(at),
        "speed": speed,
        "lines": parsers.parse_xbasex(result.text),
        "output": result.text,
    }


@mcp.tool()
def board_reset(hold_ms: int = 900, confirm: bool = False) -> dict:
    """Reset the board.

    The modem control lines are toggled by the relay, not by this server:
    the reset file carries the length of the hold in milliseconds and the
    relay does the DTR/RTS dance, whose timing was worked out the hard way.

    This resets the board.  It needs confirm=True."""
    if not confirm:
        return _refuse("board_reset", "the board's reset line")
    the = the_bench()
    if the.offline:
        return {"ok": False, "offline": True, "hint": "the offline mode resets nothing"}
    if hold_ms < 100 or hold_ms > 10000:
        raise ValueError("hold_ms must be between 100 and 10000")
    with open(the.paths.reset, "w", encoding="ascii", newline="") as stream:
        stream.write(str(hold_ms))
    return {
        "ok": True,
        "hold_ms": hold_ms,
        "hint": "the relay picks the file up within a moment; watch it in "
                "serial_tail()",
    }


if __name__ == "__main__":
    mcp.run()
