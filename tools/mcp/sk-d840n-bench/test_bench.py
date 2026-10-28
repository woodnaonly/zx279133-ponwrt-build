"""The parsers' unit tests, and the bench's own bookkeeping.

Every sample below is a real line out of staging/ctl/serial.log, taken on
2026-10-28; the three that are not marked as captured come from the print
format in zx279133-np.c instead, because the verb behind them has never been
run on the board.

    pytest ci-build-repo/tools/mcp/sk-d840n-bench/test_bench.py
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench
import parsers


# ------------------------------------------------------------- the samples

# The loader's md, with the relay's marker and the board's echo around it.
MD_SAMPLE = """\
=> === >>> 05:52:27 sent: md 0x16000000 0x10 ===
md 0x16000000 0x10
16000000: e0000004 50a840a1 013e860f 0210c073    .....@.P..>.s...
16000010: 00000048 00000000 00ff8000 00238520    H........... .#.
16000020: 80000200 00030000 0c633830 00000007    ........08c.....
16000030: 00000020 00002000 00000000 00ff0000     .... ..........
=> === >>> 05:53:44 sent: md 0x142200 0x40 ===
"""

# The region listing of the loader's own md, which is not a register dump.
MD_REGION = """\
16000000-1600ffff : 16000000.phy serdes
16100000-161fffff : 16100000.phy serdes
"""

# The kernel's soc reg, with the printk timestamp and the prompt in front
# of every other line - the kernel interleaves its own output with the
# shell's prompt.
SOC_NPPT_SAMPLE = """\
root@ImmortalWrt:~# === >>> 13:46:14 sent: echo 'regs 0x200 256' > /sys/kernel/debug/zx279133-9132 ===
echo 'regs 0x200 256' > /sys/kernel/debug/zx279133-9132
   [  365.287266] soc reg 0x19000200 (nppt +0x000200) = 0x00000000
   root@ImmortalWrt:[  365.287320] soc reg 0x19000204 (nppt +0x000204) = 0x00000000
   ~# [  365.287330] soc reg 0x19000208 (nppt +0x000208) = 0x00000000
   [  365.287339] soc reg 0x1900020c (nppt +0x00020c) = 0x00000000
"""

# The same verb with an absolute address, which is the soc verb.
SOC_ABS_SAMPLE = """\
[  365.287266] soc reg 0x16000000 = 0xe0000004
[  365.287320] soc reg 0x16000004 = 0x50a840a1
"""

MDIO_SAMPLE = """\
[  177.312133] 9132 mdio: addr 0a reg 00 = 0x1040
[  179.252910] 9132 mdio: addr 0a reg 09 = 0x0700
[  185.207983] 9132 mdio: addr 0b reg 00 = 0x1140
"""

SPA_SAMPLE = """\
[  371.623283] 9132 spa: mem 15 addr 0 = 0x0000a767
[  384.186903] 9132 spa: mem 15 addr 1 = 0x0000088f
"""

M45_SAMPLE = """\
[  162.398689] outer phy 5 devad 0x0 reg 0x00 = 0x0000
[  173.442804] outer phy 5 devad 0x1e reg 0x7581 = 0x0000
"""

XPCS_SAMPLE = """\
[  212.607944] xpcs lane 1 devad 0x1e reg 0x1e = 0x0000
[  214.251654] xpcs lane 1 devad 0x1e reg 0x8001 = 0x0000
"""

# From the print format in np.c:2279 - the verb has not been run on the board.
ERAM_SAMPLE = """\
[  786.671628] eram table 0x18 entry 128 width 2 = 0x3
"""

ER_BLOCKS_SAMPLE = """\
[  786.671628] eram block 12288 = 00000000 00000000 00000000 00000000
[  786.671686] eram block 12289 = 00000000 00000000 00000000 00000000
"""

# From the print format in np.c:1735 - the verb has not been run on the board.
NPPT_REG_SAMPLE = """\
[  365.287266] nppt reg 0x8054 = 0x00000000
"""

# From the print format in np.c:1691 - the verb has not been run on the board.
VLAN_SAMPLE = """\
[  365.287266] 9132 vlan: vid 59 = ffc00000 0000ffff ffffffff 00003003
"""

# The board's ip -o link show, with the colour codes still in it.
IP_LINK_SAMPLE = (
    "root@ImmortalWrt:~# === >>> 06:11:28 sent: ip -o link show ===\n"
    "ip -o link show\n"
    "1: \x1b[1;36mlo: \x1b[0m<LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue "
    "state UNKNOWN mode DEFAULT group default qlen 1000\\    link/loopback "
    "\x1b[1;33m00:00:00:00:00:00\x1b[0m brd \x1b[1;33m00:00:00:00:00:00\x1b[0m\n"
    "2: \x1b[1;36meth0: \x1b[0m<BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 "
    "qdisc mq state \x1b[1;32mUP \x1b[0mmode DEFAULT group default qlen "
    "1000\\    link/ether \x1b[1;33md0:45:8d:84:09:00\x1b[0m brd "
    "\x1b[1;33mff:ff:ff:ff:ff:ff\x1b[0m\n"
    "4: \x1b[1;36mlan1@lan-cpu0: \x1b[0m<NO-CARRIER,BROADCAST,MULTICAST,UP> "
    "mtu 1500 qdisc noqueue master br-lan state LOWERLAYERDOWN mode DEFAULT "
    "group default qlen 1000\\    link/ether \x1b[1;33md0:45:8d:84:09:00"
    "\x1b[0m brd \x1b[1;33mff:ff:ff:ff:ff:ff\x1b[0m\n"
)

PROC_NET_DEV_SAMPLE = """\
root@ImmortalWrt:~# === >>> 02:43:15 sent: cat /proc/net/dev ===
cat /proc/net/dev
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:   22800     320    0    0    0     0          0         0    22800     320    0    0    0     0       0          0
  eth0:       0       0    0    0    0     0          0         0     1046       9    0    0    0     0       0          0
lan-cpu0:  801424    7578    0    0    0     0          0         0     4280      38    0    0    0     0       0          0
"""

ETHTOOL_SAMPLE = """\
	rmii_rx: 0
	rmii_tx: 0
	phy_error: 0
"""

LOADER_UNAME_SAMPLE = """\
=> === >>> 04:55:38 sent: uname -r; echo ZZPROBE ===
uname -r; echo ZZPROBE
Unknown command 'uname' - try 'help'
Unknown command 'echo' - try 'help'
"""

KERNEL_UNAME_SAMPLE = """\
root@ImmortalWrt:~# === >>> 20:07:00 sent: uname -a ===
uname -a
Linux ImmortalWrt 6.18.38+ #0 SMP Mon Oct 26 04:58:01 2026 aarch64 GNU/Linux
root@ImmortalWrt:~#
"""


# -------------------------------------------------------------- the parsers

def test_parse_uboot_md():
    rows = parsers.parse_uboot_md(MD_SAMPLE)
    assert len(rows) == 4
    assert rows[0]["addr"] == 0x16000000
    assert rows[0]["words"] == ["e0000004", "50a840a1", "013e860f", "0210c073"]
    assert rows[0]["values"][0] == 0xE0000004
    assert rows[3]["addr"] == 0x16000030
    assert rows[3]["values"] == [0x00000020, 0x00002000, 0x00000000, 0x00FF0000]


def test_parse_uboot_md_ignores_the_region_listing():
    assert parsers.parse_uboot_md(MD_REGION) == []


def test_parse_uboot_md_keeps_an_ascii_column_out_of_the_words():
    # The ASCII column is four spaces away from the last word; a column that
    # happens to look like a hex word must not become a register.
    sample = "16000000: e0000004 50a840a1 013e860f 0210c073    deadbeef....\n"
    rows = parsers.parse_uboot_md(sample)
    assert len(rows) == 1
    assert rows[0]["words"] == ["e0000004", "50a840a1", "013e860f", "0210c073"]


def test_parse_soc_reg_with_the_nppt_offset():
    rows = parsers.parse_soc_reg(SOC_NPPT_SAMPLE)
    assert len(rows) == 4
    assert rows[0] == {"addr": 0x19000200, "nppt_offset": 0x200, "value": 0}
    assert rows[3]["nppt_offset"] == 0x20C


def test_parse_soc_reg_by_absolute_address():
    rows = parsers.parse_soc_reg(SOC_ABS_SAMPLE)
    assert rows == [
        {"addr": 0x16000000, "nppt_offset": None, "value": 0xE0000004},
        {"addr": 0x16000004, "nppt_offset": None, "value": 0x50A840A1},
    ]


def test_parse_mdio():
    rows = parsers.parse_mdio(MDIO_SAMPLE)
    assert rows == [
        {"addr": 0x0A, "reg": 0x00, "value": 0x1040},
        {"addr": 0x0A, "reg": 0x09, "value": 0x0700},
        {"addr": 0x0B, "reg": 0x00, "value": 0x1140},
    ]


def test_parse_spa():
    rows = parsers.parse_spa(SPA_SAMPLE)
    assert rows == [
        {"mem": 15, "addr": 0, "value": 0x0000A767},
        {"mem": 15, "addr": 1, "value": 0x0000088F},
    ]


def test_parse_m45():
    rows = parsers.parse_m45(M45_SAMPLE)
    assert rows == [
        {"phy_addr": 5, "devad": 0x00, "reg": 0x0000, "value": 0},
        {"phy_addr": 5, "devad": 0x1E, "reg": 0x7581, "value": 0},
    ]


def test_parse_xpcs():
    rows = parsers.parse_xpcs(XPCS_SAMPLE)
    assert rows == [
        {"lane": 1, "devad": 0x1E, "reg": 0x001E, "value": 0},
        {"lane": 1, "devad": 0x1E, "reg": 0x8001, "value": 0},
    ]


def test_parse_eram():
    rows = parsers.parse_eram(ERAM_SAMPLE)
    assert rows == [{"sdt_id": 0x18, "index": 128, "width": 2, "value": 3}]


def test_parse_er_blocks():
    rows = parsers.parse_er_blocks(ER_BLOCKS_SAMPLE)
    assert len(rows) == 2
    assert rows[0]["block"] == 12288
    assert rows[0]["values"] == [0, 0, 0, 0]


def test_parse_nppt_reg():
    assert parsers.parse_nppt_reg(NPPT_REG_SAMPLE) == [
        {"offset": 0x8054, "value": 0}]


def test_parse_vlan():
    rows = parsers.parse_vlan(VLAN_SAMPLE)
    assert rows[0]["vid"] == 59
    assert rows[0]["values"] == [0xFFC00000, 0x0000FFFF, 0xFFFFFFFF, 0x00003003]


def test_parse_ip_link():
    links = parsers.parse_ip_link(IP_LINK_SAMPLE)
    assert len(links) == 3
    assert links[0]["name"] == "lo"
    assert links[0]["mac"] == "00:00:00:00:00:00"
    assert links[0]["mtu"] == 65536
    assert links[0]["state"] == "UNKNOWN"
    assert links[1]["name"] == "eth0"
    assert links[1]["mac"] == "d0:45:8d:84:09:00"
    assert links[1]["state"] == "UP"
    assert links[1]["flags"] == ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"]
    assert links[2]["name"] == "lan1"
    assert links[2]["peer"] == "lan-cpu0"
    assert links[2]["master"] == "br-lan"
    assert links[2]["state"] == "LOWERLAYERDOWN"


def test_parse_proc_net_dev():
    counters = parsers.parse_proc_net_dev(PROC_NET_DEV_SAMPLE)
    assert len(counters) == 3
    assert counters[0]["iface"] == "lo"
    assert counters[0]["rx_bytes"] == 22800
    assert counters[0]["tx_bytes"] == 22800
    assert counters[1]["iface"] == "eth0"
    assert counters[1]["tx_packets"] == 9
    assert counters[2]["iface"] == "lan-cpu0"
    assert counters[2]["rx_packets"] == 7578


def test_parse_ethtool():
    stats = parsers.parse_ethtool(ETHTOOL_SAMPLE)
    assert stats == {"rmii_rx": 0, "rmii_tx": 0, "phy_error": 0}


def test_detect_state_loader():
    assert parsers.detect_state(LOADER_UNAME_SAMPLE) == "loader"


def test_detect_state_kernel():
    assert parsers.detect_state(KERNEL_UNAME_SAMPLE) == "kernel"


def test_detect_state_when_the_board_has_just_booted():
    # Both signals are in the slice; the later one is the state now.
    assert parsers.detect_state(LOADER_UNAME_SAMPLE + KERNEL_UNAME_SAMPLE) == "kernel"
    assert parsers.detect_state(KERNEL_UNAME_SAMPLE + LOADER_UNAME_SAMPLE) == "loader"


def test_detect_state_unknown():
    assert parsers.detect_state("nothing to see here") == "unknown"


def test_strip_noise_drops_the_relay_markers_and_the_echo():
    clean = parsers.strip_noise(MD_SAMPLE, echo="md 0x16000000 0x10")
    assert "sent:" not in clean
    assert "md 0x16000000 0x10" not in clean
    assert "16000000: e0000004" in clean


def test_strip_prompt():
    assert parsers.strip_prompt("root@ImmortalWrt:~# ls") == "ls"
    assert parsers.strip_prompt("=> md 0x1 4") == "md 0x1 4"
    assert parsers.strip_prompt("~# [  365.287330] soc reg") == "soc reg"


def test_normalize_collapses_the_loaders_carriage_returns():
    # The loader ends its lines with CR CR LF; without the collapse every
    # other line comes out with a blank one after it.
    body = parsers.normalize("a\r\r\nb\r\nc\rd")
    assert body == "a\nb\nc\nd"


def test_the_mds_count_is_a_number_of_words():
    # U-Boot's md counts items, not bytes: 'md 0x16000000 0x40' dumps 64
    # words.  The parser does not care, but the tool does.
    rows = parsers.parse_uboot_md("16000000: e0000004 50a840a1 013e860f 0210c073\n")
    assert len(rows[0]["values"]) == 4
    assert len(rows[0]["values"]) * 4 == 16       # sixteen bytes on the wire


def test_md_last_row_address():
    # Four words to a line, so the lines are sixteen bytes apart.
    assert parsers.md_last_row_address(0x16000000, 64) == 0x160000F0
    assert parsers.md_last_row_address(0x16000000, 4) == 0x16000000
    assert parsers.md_last_row_address(0x16000000, 6) == 0x16000010
    assert parsers.md_last_row_address(0x19000200, 32) == 0x19000270


def test_the_prompt_line_marks_the_end_of_an_answer():
    import re
    pattern = re.compile(parsers.PROMPT_LINE_RE)
    # The prompt on its own line, and the prompt with the relay's next
    # marker glued to it: both end an answer.
    assert pattern.search("root@ImmortalWrt:~#")
    assert pattern.search("root@ImmortalWrt:~# ")
    assert pattern.search("root@ImmortalWrt:~# === >>> 06:11:32 sent: brctl show ===")
    assert pattern.search("~#")
    # The echo of the command is not the end of the answer, and neither is
    # a line of output.
    assert not pattern.search("root@ImmortalWrt:~# ip -o link show")
    assert not pattern.search("ip -o link show")
    assert not pattern.search(
        "1: \x1b[1;36mlo: \x1b[0m<LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue")


def test_find_shell_error():
    # The board refuses a command in the shell's own words; without this the
    # refusal looks like silence and the call waits out its whole timeout.
    assert parsers.find_shell_error(
        "ash: write error: Invalid argument\nroot@ImmortalWrt:~# ") == \
        "ash: write error: Invalid argument"
    assert parsers.find_shell_error("ethtool: not found\n") == "ethtool: not found"
    assert parsers.find_shell_error(
        "root@ImmortalWrt:~# ash: write error: Invalid argument\n") == \
        "ash: write error: Invalid argument"
    # An ordinary answer is not a refusal.
    assert parsers.find_shell_error(
        "[  365.287266] soc reg 0x19000200 (nppt +0x000200) = 0x00000000\n") is None
    assert parsers.find_shell_error(
        "9132 mdio: addr 0a reg 00 = 0x1040\n") is None


# ------------------------------------------------------------ the bookkeeping

def test_hex32_always_carries_the_prefix():
    # Without the 0x the driver reads the number as decimal
    # (zx279133-np.c:1267-1270).
    assert bench.hex32(0x140000) == "0x140000"
    assert bench.hex32(0) == "0x0"


def test_parse_int():
    assert bench.parse_int("0x140000", "offset") == 0x140000
    assert bench.parse_int(0x140000, "offset") == 0x140000
    assert bench.parse_int("200", "offset") == 200
    with pytest.raises(ValueError):
        bench.parse_int("nonsense", "offset")


def test_check_command_refuses_the_two_dangerous_ones():
    with pytest.raises(bench.CommandRejected):
        bench.check_command("zxboot")
    with pytest.raises(bench.CommandRejected):
        bench.check_command("up_mode get")
    assert bench.check_command("md 0x16000000 0x40") == "md 0x16000000 0x40"


def test_soc_block_ok():
    assert bench.soc_block_ok(0x16000000, 0x40)
    assert bench.soc_block_ok(0x1A000000, 0x100)
    assert not bench.soc_block_ok(0x19000000, 4)
    assert not bench.soc_block_ok(0x16100000, 0x200000)


def test_file_lock_takes_and_gives_the_lock(tmp_path):
    path = tmp_path / ".bench.lock"
    lock = bench.FileLock(path)
    lock.acquire()
    assert path.exists()
    lock.release()
    assert not path.exists()


def test_file_lock_refuses_a_second_holder(tmp_path):
    path = tmp_path / ".bench.lock"
    first = bench.FileLock(path)
    first.acquire()
    try:
        with pytest.raises(bench.BenchBusy):
            bench.FileLock(path).acquire()
    finally:
        first.release()


def test_file_lock_takes_over_a_stale_one(tmp_path):
    path = tmp_path / ".bench.lock"
    path.write_text("pid=1 ts=0\n")
    old = time.time() - 3600
    os.utime(path, (old, old))
    lock = bench.FileLock(path, stale_after=600.0)
    lock.acquire()
    assert lock.held
    lock.release()


# --------------------------------------------------------------- the offline mode

def test_the_offline_mode_sends_nothing(tmp_path):
    ctl = tmp_path / "staging" / "ctl"
    ctl.mkdir(parents=True)
    (ctl / "serial.log").write_text(MD_SAMPLE, encoding="utf-8")

    the = bench.Bench(root=tmp_path, offline=True)
    result = the.run("md 0x16000000 0x10", expect=r"(?<![\w.-])[0-9a-fA-F]{8,9}:")

    assert not (ctl / "sercmd.txt").exists()
    assert not (ctl / ".bench.lock").exists()
    assert result.confirmed is False
    assert result.notes and "offline" in result.notes[0]
    rows = parsers.parse_uboot_md(result.text)
    assert rows and rows[0]["addr"] == 0x16000000


def test_the_offline_mode_does_not_touch_the_board(tmp_path):
    ctl = tmp_path / "staging" / "ctl"
    ctl.mkdir(parents=True)
    (ctl / "serial.log").write_text(KERNEL_UNAME_SAMPLE, encoding="utf-8")

    the = bench.Bench(root=tmp_path, offline=True)
    assert the.board_state() == "kernel"
    assert the.board_state() == "kernel"      # the answer is cached
    assert not (ctl / "sercmd.txt").exists()


def test_the_offline_mode_reads_a_saved_slice(tmp_path):
    ctl = tmp_path / "staging" / "ctl"
    ctl.mkdir(parents=True)
    (ctl / "serial.log").write_text(KERNEL_UNAME_SAMPLE, encoding="utf-8")
    saved = tmp_path / "slice.txt"
    saved.write_text(MD_SAMPLE, encoding="utf-8")

    os.environ["SK_D840N_BENCH_OFFLINE_LOG"] = str(saved)
    try:
        the = bench.Bench(root=tmp_path, offline=True)
        result = the.run("md 0x16000000 0x10")
        assert "16000000: e0000004" in result.text
    finally:
        del os.environ["SK_D840N_BENCH_OFFLINE_LOG"]
