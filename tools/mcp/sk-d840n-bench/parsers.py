"""Parsers for the SK-D840N bench console.

The board says several different things over one serial line, and that line is
shared with whatever else is running on the bench, so every parser here works
on a *slice* of staging/ctl/serial.log and looks for a pattern inside a line
rather than at the start of it.  The kernel interleaves its printk lines with
the shell prompt ("root@ImmortalWrt:[  365.287320] soc reg ..."), and the
loader prints its prompt in front of the echo, so a parser anchored at the
start of a line would miss most of the real output.

Every sample used by the tests is a real line out of the log, taken on
2026-10-28; none of them was made up.

The formats come from the driver itself (zx279133-np.c) and from U-Boot:

    soc reg 0x%08x (nppt +0x%06x) = 0x%08x      np.c:1318
    soc reg 0x%08x = 0x%08x                     np.c:1643
    9132 mdio: addr %02x reg %02x = 0x%04x      np.c:1467
    9132 spa: mem %u addr %u = 0x%08x          np.c:1242
    outer phy %u devad %#02x reg %#04x = 0x%04x np.c:1549
    xpcs lane %u devad %#02x reg %#04x = 0x%04x np.c:1599
    eram table 0x%02x entry %u width %u = 0x%x np.c:2279
    eram block %u = %08x %08x %08x %08x
    nppt reg 0x%04x = 0x%08x
    9132 vlan: vid %u = %08x %08x %08x %08x
    <addr>: <word> <word> <word> <word>    <ascii>     U-Boot's md
"""

import re

# --------------------------------------------------------------------- noise

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# The things that sit in front of a real line: the loader's prompt, the
# board's shell prompt (it wraps, so the tail of it is "root@ImmortalWrt:~#"
# and the head of the next one is "~#"), and the kernel's timestamp.
_PROMPT_RES = (
    re.compile(r"^root@[\w.-]+:\S*\s?"),
    re.compile(r"^~#\s?"),
    re.compile(r"^=>\s?"),
    re.compile(r"^\[\s*\d+\.\d+\]\s?"),
)


def strip_ansi(text):
    """Drop the colour codes the board's shell puts around every field."""
    return _ANSI_RE.sub("", text)


def normalize(text):
    """CRLF and a lone CR both become LF, so one line is one line.

    The loader ends its lines with CR CR LF, so the run of CRs is collapsed
    first; otherwise every other line would come out with a blank one after
    it.
    """
    text = strip_ansi(text)
    text = re.sub(r"\r+\n", "\n", text)
    return text.replace("\r", "\n")


def lines_of(text):
    """The lines of a console slice, with the colour codes gone."""
    return normalize(text).split("\n")


def strip_prompt(line):
    """Whatever is in front of the payload on this line: prompts, timestamps."""
    previous = None
    while previous != line:
        previous = line
        for pattern in _PROMPT_RES:
            line = pattern.sub("", line, count=1)
    return line


def is_relay_marker(line):
    """True for the relay's own bookkeeping lines ("=== >>> ... sent: ... ===")."""
    stripped = strip_prompt(line).strip()
    return stripped.startswith("===") and stripped.endswith("===")


def strip_noise(text, echo=None):
    """The console slice without the relay's markers and without the echo.

    The board echoes every command it is given, so a slice always contains
    the command itself next to its answer; the echo is dropped when it is
    the line we sent.
    """
    wanted = echo.strip() if echo else None
    kept = []
    for raw in lines_of(text):
        if is_relay_marker(raw):
            continue
        if wanted is not None and strip_prompt(raw).strip() == wanted:
            continue
        kept.append(raw)
    return "\n".join(kept)


# ------------------------------------------------------------------- helpers

_HEX8_RE = re.compile(r"^[0-9a-fA-F]{8}$")


def _md_words(payload):
    """The hex words of one U-Boot md line, without the ASCII column.

    U-Boot prints the words separated by a single space and then the ASCII
    column separated by four, so the column is found by the gap and not by
    the content - an ASCII column that happens to look like a hex word must
    not turn into a register.
    """
    words = []
    position = 0
    for match in re.finditer(r"\S+", payload):
        gap = payload[position:match.start()]
        if words and len(gap) >= 2:
            break
        token = match.group(0)
        if not _HEX8_RE.match(token):
            break
        words.append(token.lower())
        position = match.end()
    return words


# ------------------------------------------------------------------ parsers

_MD_RE = re.compile(r"(?<![\w.-])([0-9a-fA-F]{8,9}):(\s.*)$")
_SOC_NPPT_RE = re.compile(
    r"soc reg 0x([0-9a-fA-F]{8}) \(nppt \+0x([0-9a-fA-F]{6})\) = 0x([0-9a-fA-F]{8})")
_SOC_ABS_RE = re.compile(r"soc reg 0x([0-9a-fA-F]{8}) = 0x([0-9a-fA-F]{8})")
_MDIO_RE = re.compile(
    r"9132 mdio: addr ([0-9a-fA-F]{2}) reg ([0-9a-fA-F]{2}) = 0x([0-9a-fA-F]{4})")
_SPA_RE = re.compile(r"9132 spa: mem (\d+) addr (\d+) = 0x([0-9a-fA-F]{8})")
_M45_RE = re.compile(
    r"outer phy (\d+) devad 0x([0-9a-fA-F]{1,2}) reg 0x([0-9a-fA-F]{1,4}) = 0x([0-9a-fA-F]{4})")
_XPCS_RE = re.compile(
    r"xpcs lane (\d+) devad 0x([0-9a-fA-F]{1,2}) reg 0x([0-9a-fA-F]{1,4}) = 0x([0-9a-fA-F]{4})")
_ERAM_RE = re.compile(
    r"eram table 0x([0-9a-fA-F]{2}) entry (\d+) width (\d+) = 0x([0-9a-fA-F]+)")
_ER_RE = re.compile(r"eram block (\d+) = ((?:[0-9a-fA-F]{8}\s*)+)")
_NPPT_RE = re.compile(r"nppt reg 0x([0-9a-fA-F]{4,8}) = 0x([0-9a-fA-F]{8})")
_VLAN_RE = re.compile(r"9132 vlan: vid (\d+) = ((?:[0-9a-fA-F]{8}\s*){4})")
_XBASEX_RE = re.compile(r"xbasex: (.*)$", re.M)


def md_last_row_address(address, count):
    """The address of the last line of a U-Boot md dump.

    U-Boot prints four words to a line, so the lines are sixteen bytes
    apart and the last one starts here.  Waiting for that line is what
    makes a dump complete: waiting for the first one cuts it short.
    """
    rows = (count + 3) // 4
    return address + 16 * (rows - 1)


def parse_uboot_md(text):
    """U-Boot's md output.

    A real line:

        16000000: e0000004 50a840a1 013e860f 0210c073    .....@.P..>.s...
    """
    rows = []
    for raw in lines_of(text):
        match = _MD_RE.search(raw)
        if not match:
            continue
        words = _md_words(match.group(2))
        if not words:
            continue
        rows.append({
            "addr": int(match.group(1), 16),
            "words": words,
            "values": [int(word, 16) for word in words],
        })
    return rows


def parse_soc_reg(text):
    """The driver's soc reg lines, with and without the nppt offset."""
    out = []
    for raw in lines_of(text):
        match = _SOC_NPPT_RE.search(raw)
        if match:
            out.append({
                "addr": int(match.group(1), 16),
                "nppt_offset": int(match.group(2), 16),
                "value": int(match.group(3), 16),
            })
            continue
        match = _SOC_ABS_RE.search(raw)
        if match:
            out.append({
                "addr": int(match.group(1), 16),
                "nppt_offset": None,
                "value": int(match.group(2), 16),
            })
    return out


def parse_mdio(text):
    """The four internal PHYs on the SoC's MDIO controller #0."""
    return [
        {"addr": int(m[0], 16), "reg": int(m[1], 16), "value": int(m[2], 16)}
        for m in _MDIO_RE.findall(normalize(text))
    ]


def parse_spa(text):
    """The SPA's indirect window."""
    return [
        {"mem": int(m[0]), "addr": int(m[1]), "value": int(m[2], 16)}
        for m in _SPA_RE.findall(normalize(text))
    ]


def parse_m45(text):
    """A Clause-45 register of the outer PHY (the RTL8226 on controller #1)."""
    return [
        {"phy_addr": int(m[0]), "devad": int(m[1], 16),
         "reg": int(m[2], 16), "value": int(m[3], 16)}
        for m in _M45_RE.findall(normalize(text))
    ]


def parse_xpcs(text):
    """A Clause-45 register of a MAC lane's PCS."""
    return [
        {"lane": int(m[0]), "devad": int(m[1], 16),
         "reg": int(m[2], 16), "value": int(m[3], 16)}
        for m in _XPCS_RE.findall(normalize(text))
    ]


def parse_eram(text):
    """One ERAM table entry (the es/eg verbs)."""
    return [
        {"sdt_id": int(m[0], 16), "index": int(m[1]),
         "width": int(m[2]), "value": int(m[3], 16)}
        for m in _ERAM_RE.findall(normalize(text))
    ]


def parse_er_blocks(text):
    """A run of ERAM blocks (the er verb)."""
    out = []
    for block, payload in _ER_RE.findall(normalize(text)):
        words = payload.split()
        out.append({
            "block": int(block),
            "words": words,
            "values": [int(word, 16) for word in words],
        })
    return out


def parse_nppt_reg(text):
    """The SPA window read through the nr/nd verbs."""
    return [
        {"offset": int(m[0], 16), "value": int(m[1], 16)}
        for m in _NPPT_RE.findall(normalize(text))
    ]


def parse_vlan(text):
    """One VLAN action table entry (the v verb)."""
    out = []
    for vid, payload in _VLAN_RE.findall(normalize(text)):
        words = payload.split()
        out.append({"vid": int(vid), "words": words,
                    "values": [int(word, 16) for word in words]})
    return out


def parse_xbasex(text):
    """The lines the xbasex verb prints."""
    return [line.strip() for line in _XBASEX_RE.findall(text) if line.strip()]


_IP_LINK_RE = re.compile(r"^(\d+):\s*([^:\s]+):\s*<([^>]*)>(.*)$")
_NETDEV_RE = re.compile(r"^\s*([^\s:]+):\s*((?:\d+\s+){15}\d+)\s*$")
_ETHTOOL_RE = re.compile(r"^\s*([A-Za-z_][\w.]*):\s+(\d+)\s*$")

# The shell's prompt on a line of its own.  A command's answer is finished
# when the shell prints the prompt again - that is the only end-of-output
# marker there is for the commands whose last line is not known in advance
# (ip -o link show, cat /proc/net/dev, ethtool -S).
#
# The prompt is glued to whatever comes next, which is usually the relay's
# next marker, so the marker is allowed after it.  The *echo* of the command
# is not allowed: that is what tells the two apart.  The line that carries
# the command's own "sent:" marker also matches, so the wait for the answer
# has to start after that marker and not at it.
PROMPT_LINE_RE = r"(?m)^(?:root@[\w.-]+:\S*|~#)\s*(?:===.*)?$"


def parse_ip_link(text):
    """`ip -o link show`: one netdev per line.

    A real line, with the colour codes already stripped:

        4: lan1@lan-cpu0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 ...
    """
    out = []
    for raw in lines_of(text):
        line = strip_prompt(raw).strip().replace("\\", " ")
        match = _IP_LINK_RE.match(line)
        if not match:
            continue
        name = match.group(2)
        peer = None
        if "@" in name:
            name, peer = name.split("@", 1)
        tail = match.group(4)
        mtu = re.search(r"\bmtu (\d+)", tail)
        state = re.search(r"\bstate (\S+)", tail)
        master = re.search(r"\bmaster (\S+)", tail)
        link = re.search(r"\blink/(\S+)\s+([0-9a-fA-F:]{17})", tail)
        out.append({
            "index": int(match.group(1)),
            "name": name,
            "peer": peer,
            "flags": [flag for flag in match.group(3).split(",") if flag],
            "mtu": int(mtu.group(1)) if mtu else None,
            "state": state.group(1) if state else None,
            "master": master.group(1) if master else None,
            "link": link.group(1) if link else None,
            "mac": link.group(2) if link else None,
        })
    return out


def parse_proc_net_dev(text):
    """`cat /proc/net/dev`: the counters of every netdev."""
    out = []
    for raw in lines_of(text):
        match = _NETDEV_RE.match(raw)
        if not match:
            continue
        values = [int(value) for value in match.group(2).split()]
        out.append({
            "iface": match.group(1),
            "rx_bytes": values[0], "rx_packets": values[1],
            "rx_errs": values[2], "rx_drop": values[3],
            "rx_fifo": values[4], "rx_frame": values[5],
            "rx_compressed": values[6], "rx_multicast": values[7],
            "tx_bytes": values[8], "tx_packets": values[9],
            "tx_errs": values[10], "tx_drop": values[11],
            "tx_fifo": values[12], "tx_colls": values[13],
            "tx_carrier": values[14], "tx_compressed": values[15],
        })
    return out


def parse_ethtool(text):
    """`ethtool -S <if>`: the driver's own counters."""
    out = {}
    for raw in lines_of(text):
        match = _ETHTOOL_RE.match(raw)
        if match:
            out[match.group(1)] = int(match.group(2))
    return out


# --------------------------------------------------------------- the state

_LOADER_RE = re.compile(r"Unknown command", re.I)
_KERNEL_RE = re.compile(r"Linux\s+ImmortalWrt|Linux version \d|^Linux \S+", re.M)
_ASH_RE = re.compile(r"-ash:.*not found", re.I)


def detect_state(text):
    """Which of the two the board is in right now: loader, kernel or unknown.

    `uname -a` is the probe.  The loader has no uname and answers
    "Unknown command 'uname' - try 'help'"; the kernel answers with its
    uname line.  When a slice carries both - the board has just booted -
    the later one wins, because that is the state the board is in now.
    """
    body = normalize(text)
    loader = _LOADER_RE.search(body)
    kernel = _KERNEL_RE.search(body)
    if loader and kernel:
        return "loader" if loader.start() > kernel.start() else "kernel"
    if loader:
        return "loader"
    if kernel:
        return "kernel"
    if _ASH_RE.search(body):
        return "kernel"
    return "unknown"


# The shell's own complaint, when it refused the command.  The board says
# "ash: write error: Invalid argument" when the driver refuses the request,
# and the shell's "not found" when the command is not on it.  Without this
# the answer would look like silence, and a refusal would look like a
# timeout.
_SHELL_ERROR_RE = re.compile(
    r"^(?:[a-z]+:\s+write error:.*"
    r"|[^\s:]+:\s+(?:not found|No such file(?: or directory)?|"
    r"Permission denied|Invalid argument|Read-only file system|Is a directory))$",
    re.I)


def find_shell_error(text):
    """The line in which the shell refused the command, or None."""
    for raw in lines_of(text):
        line = strip_prompt(raw).strip()
        if _SHELL_ERROR_RE.match(line):
            return line
    return None
