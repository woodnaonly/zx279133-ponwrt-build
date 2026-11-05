"""Exclusive, bounded UART primitives for RAM-only SK-D840N trials.

No shell passthrough, persistent environment commands, reset or NAND writes.
The same handle is used for console text and XMODEM. Importing is inert.
"""
from __future__ import annotations

from dataclasses import dataclass
import binascii
import hashlib
import re
import struct
import time
import zlib


class TrialError(RuntimeError):
    pass


class LinkLost(TrialError):
    pass


@dataclass(frozen=True)
class Span:
    start: int
    size: int
    name: str

    @property
    def end(self):
        return self.start + self.size

    def valid(self):
        return 0 <= self.start < self.end <= (1 << 64)

    def overlaps(self, other):
        return self.start < other.end and other.start < self.end


def validate_placement(banks, reserved, allocations):
    """All reservations must be supplied by runtime evidence, not a profile bit."""
    if not banks or not reserved or not allocations:
        raise TrialError("incomplete measured RAM map")
    all_spans = [*banks, *reserved, *allocations]
    if any(not s.valid() for s in all_spans):
        raise TrialError("invalid/overflowing RAM range")
    if len({s.name for s in all_spans}) != len(all_spans):
        raise TrialError("ambiguous RAM range names")
    for i, item in enumerate(allocations):
        if item.start % 64 or item.size % 64:
            raise TrialError("RAM allocations must be cache-line aligned")
        if not any(b.start <= item.start < item.end <= b.end for b in banks):
            raise TrialError("allocation is outside measured DRAM: " + item.name)
        if any(item.overlaps(s) for s in [*reserved, *allocations[:i]]):
            raise TrialError("RAM overlap: " + item.name)


class PowerWindow:
    SERVICE_STOP = 17 * 60
    RESTORE_END = 18 * 60

    def __init__(self, clock=time.monotonic, *, continuous_power_confirmed=False):
        self.clock = clock
        self.continuous_power_confirmed=continuous_power_confirmed is True
        self.started = None
        self.generation = 0

    def boot(self):
        self.started = self.clock()
        self.generation += 1

    def invalidate(self):
        self.started = None
        self.generation += 1

    def remaining(self, *, cleanup=False):
        if self.started is None:
            raise TrialError("no fresh boot timestamp")
        if self.continuous_power_confirmed:
            return float("inf")
        limit = self.RESTORE_END if cleanup else self.SERVICE_STOP
        return max(0.0, self.started + limit - self.clock())

    def require(self, seconds, *, cleanup=False):
        if seconds < 0 or self.remaining(cleanup=cleanup) <= seconds:
            raise TrialError("insufficient time before scheduled power loss")


def parse_md(text, address, length):
    """Parse ONLY explicitly addressed md.b bytes; refuse gaps/duplicates."""
    result = bytearray()
    for line in text.splitlines():
        match = re.match(r"^\s*([0-9a-fA-F]{8,16}):\s+((?:[0-9a-fA-F]{2}(?:\s+|$)){1,16})", line)
        if not match:
            continue
        at = int(match[1], 16)
        if at < address or at >= address + length:
            continue
        if at != address + len(result):
            raise TrialError("non-contiguous/duplicate memory readback")
        values = bytes.fromhex(match[2])
        result += values[:length - len(result)]
    if len(result) != length:
        raise TrialError("short memory readback")
    return bytes(result)


def parse_md_words(text,address,words):
    """Parse native-width md.l reads; MMIO byte lanes may not be readable."""
    if address%4 or not 1<=words<=4096:raise TrialError("invalid word memory read extent")
    result=bytearray();length=words*4
    for line in text.splitlines():
        match=re.match(r"^\s*([0-9a-fA-F]{8,16}):\s+((?:[0-9a-fA-F]{8}(?:\s+|$)){1,4})",line)
        if not match:continue
        at=int(match[1],16)
        if at<address or at>=address+length:continue
        if at!=address+len(result):raise TrialError("non-contiguous/duplicate word memory readback")
        for word in match[2].split():
            if len(result)<length:result.extend(struct.pack("<I",int(word,16)))
    if len(result)!=length:raise TrialError("short word memory readback")
    return bytes(result)


class Console:
    PROMPT = re.compile(rb"(?:^|[\r\n])=>[ \t]*(?:\x1b\[[0-9;]*[A-Za-z])*[ \t]*$")
    BOOT = re.compile(rb"(?:^|[\r\n])(?:U-Boot 20\d\d\.\d\d[^\r\n]*|bootloader[^\r\n]*|DDR3[^\r\n]*|pll init[^\r\n]*)", re.I)
    # These commands either inspect data or change only audited RAM locations.
    READ_COMMAND = re.compile(r"(?:version|help(?: (?:bdinfo|md|mw|crc32|loadx|icache|dcache|nand))?|bdinfo|printenv(?: [A-Za-z0-9_]+)*|(?:icache|dcache)|md\.[bl] [0-9a-f]+ [0-9a-f]+|crc32 [0-9a-f]+ [0-9a-f]+)\Z")

    def __init__(self, port, *, clock=time.monotonic, emit=lambda event, **fields: None, raw=lambda data: None,
                 continuous_power_confirmed=False):
        self.port, self.clock, self.emit, self.raw = port, clock, emit, raw
        self.window = PowerWindow(clock,continuous_power_confirmed=continuous_power_confirmed)
        self.prompt = self.identified = False
        self.tail = b""
        self.transferring = False

    def read(self, count=4096):
        try:
            # On Windows a large ReadFile request can defer delivery of a
            # short countdown fragment. Drain queued bytes immediately; when
            # idle wait for just one byte, not an entire 4096-byte buffer.
            waiting = getattr(self.port, "in_waiting", count)
            data = self.port.read(min(count, max(1, waiting)))
        except Exception as error:
            # SerialException is not consistently an OSError across backends.
            self.invalidate()
            raise LinkLost("UART read failed: " + str(error)) from error
        if data:
            self.raw(data)
        return data

    def write(self, data):
        try:
            at = 0
            while at < len(data):
                size = self.port.write(data[at:])
                if not size or size > len(data) - at:
                    raise OSError("short/invalid UART write")
                at += size
        except Exception as error:
            self.invalidate()
            raise LinkLost("UART write failed: " + str(error)) from error

    def invalidate(self):
        self.window.invalidate()
        self.prompt = self.identified = False
        self.tail = b""

    def wait_boot_prompt(self, seconds, *, not_before=None):
        """Never send a CR to an unknown console (it could submit stale input)."""
        end = self.clock() + seconds
        booting = False
        next_space = 0.0
        boot_deadline = 0.0
        interrupt_count = 0
        last_heartbeat = self.clock()
        while self.clock() < end:
            data = self.read()
            if not_before is not None and self.clock() < not_before:
                # Log but do not retain an early software-reset banner. It must
                # not extend a smart-switch power window or trigger a space.
                self.tail = b""
                continue
            self.tail = (self.tail + data)[-8192:]
            if not booting and self.BOOT.search(self.tail):
                self.window.boot()
                self.identified = False
                booting = True
                # This vendor board initializes PON/PHY before the countdown;
                # it may take well over 15 seconds. Do not type before it.
                boot_deadline = self.clock() + 75
                self.emit("boot_detected", generation=self.window.generation)
            if booting and any(marker in self.tail for marker in (b"Starting kernel", b"Linux version")):
                self.invalidate()
                raise TrialError("autoboot countdown was not interrupted; kernel stage observed, no more input")
            if booting and self.PROMPT.search(self.tail):
                self.prompt = True
                self.emit("uboot_prompt", generation=self.window.generation)
                return
            if booting and self.clock() >= boot_deadline:
                raise TrialError("fresh boot did not reach an interrupted U-Boot prompt")
            # Only during this freshly observed boot; NO perpetual spaces/CRs.
            if booting and re.search(rb"Hit any key|autoboot.*[0-9]|stop autoboot", self.tail, re.I) and self.clock() >= next_space:
                self.emit("autoboot_interrupt", attempt=interrupt_count+1,
                          received_tail=self.tail[-160:].decode("ascii",errors="replace"))
                self.write(b" ")
                interrupt_count += 1
                next_space = self.clock() + .12
            if self.clock() - last_heartbeat >= 30:
                self.emit("waiting_for_natural_boot", seconds_left=round(end - self.clock()),
                          received_tail_bytes=len(self.tail))
                last_heartbeat = self.clock()
        raise TrialError("no fresh U-Boot boot/prompt within observation window")

    def _exchange(self, command, timeout):
        if not self.prompt or self.transferring:
            raise TrialError("console command without synchronized U-Boot prompt")
        self.window.require(timeout, cleanup=True)
        self.prompt = False
        self.tail = b""
        self.emit("command", text=command)
        self.write(command.encode("ascii") + b"\r")
        return self._response(command, timeout)

    def _response(self, command, timeout):
        end = self.clock() + timeout
        result = bytearray()
        while self.clock() < end:
            result += self.read()
            if any(marker in result for marker in (b"Linux version", b"Resetting CPU", b'Synchronous Abort', b"SPI NAND\r")) or (command != "version" and self.BOOT.search(result)):
                self.invalidate()
                raise LinkLost("unexpected reboot/stage transition during command")
            if len(result) > 1024 * 1024:
                self.invalidate()
                raise TrialError("console response exceeds bound")
            if self.PROMPT.search(result):
                self.prompt = True
                return result.decode("ascii", errors="replace")
        self.invalidate()
        raise TrialError("console command timed out; no retry or queued replay: " + command)

    def inspect(self, command, timeout=10):
        if not self.READ_COMMAND.fullmatch(command):
            raise TrialError("not an allowed inspection command")
        return self._exchange(command, timeout)

    def identify(self):
        version = self.inspect("version")
        if "U-Boot 2021.01-svn308776" not in version:
            raise TrialError("unknown U-Boot revision; no RAM modifications allowed")
        help_text = self.inspect("help")
        for name in ("loadx", "md", "mw", "crc32", "bdinfo"):
            if not re.search(r"(?m)^\s*" + re.escape(name) + r"\s+-", help_text):
                raise TrialError("required runtime command is absent: " + name)
        if not re.search(r"(?m)^\s*(?:\?|bootweb)\s+-",help_text):
            raise TrialError("required audited command entry is absent")
        board = self.inspect("bdinfo")
        self.identified = True
        self.emit("identity_inspected", version=version, commands=help_text, bdinfo=board,
                  ram_map_verified=False, flashable_release=False)
        return version, help_text, board

    def loadx(self, payload, allocation, *, banks, reserved, other_allocations=()):
        if not self.identified or not self.prompt:
            raise TrialError("runtime identity/prompt not verified")
        validate_placement(banks, reserved, [allocation, *other_allocations])
        padded = (len(payload) + 127) // 128 * 128
        if not payload or padded > allocation.size:
            raise TrialError("XMODEM padded payload exceeds its reserved RAM allocation")
        self.window.require(215)
        command = f"loadx {allocation.start:x}"
        self.prompt = False
        self.transferring = True
        self.emit("command", text=command)
        self.write(command.encode("ascii") + b"\r")
        XmodemCRC(self).send(payload)
        text = self._response(command, 5)
        length = re.search(r"Total Size\s*=\s*(?:0x)?([0-9a-fA-F]+)", text)
        # xyzModem in this measured U-Boot removes trailing SUB padding;
        # other conforming receivers report the full 128-byte block count.
        # Both are bounded by the reserved allocation; CRC covers exact bytes.
        if length is None or int(length[1], 16) not in (len(payload), padded):
            raise TrialError("receiver's reported XMODEM length is missing or incorrect")
        checksum = self.inspect(f"crc32 {allocation.start:x} {len(payload):x}")
        crc = re.search(r"==>\s*([0-9a-fA-F]{8})\b", checksum)
        if crc is None or int(crc[1], 16) != zlib.crc32(payload):
            raise TrialError("RAM payload CRC does not match; execution forbidden")
        self.emit("ram_payload_verified", address=f"0x{allocation.start:x}", bytes=len(payload),
                  sha256=hashlib.sha256(payload).hexdigest(), executed=False)


class AliasHook:
    """An audited RAM cmd_tbl +0x18 change, restored only in its boot generation.

    The caller must first measure RAM reservations and cache/MMU state, and
    read back the relocated '?' record against the pinned firmware bytes.
    No CLI accepts a JSON boolean as a replacement for those measurements.
    """
    def __init__(self, console, address, expected, *, read_memory, write_pointer):
        if len(expected) != 56 or address % 8 or not console.identified:
            raise TrialError("unverified command record")
        self.console, self.address, self.expected = console, address, expected
        self.read_memory, self.write_pointer = read_memory, write_pointer
        self.generation = console.window.generation
        self.saved = expected[24:32]
        self.armed = False

    def check_session(self):
        if self.console.window.generation != self.generation or not self.console.identified or not self.console.prompt:
            raise LinkLost("RAM hook's original U-Boot session is no longer valid")
        self.console.window.require(10, cleanup=True)

    def install(self, entry):
        self.check_session()
        if self.armed or entry % 4 or not 0 < entry < (1 << 64):
            raise TrialError("invalid/already installed RAM hook")
        if self.read_memory(self.address, 56) != self.expected:
            raise TrialError("runtime command record differs from audited original")
        # Mark before writing: partial/error writes still require restoration.
        self.armed = True
        self.write_pointer(self.address + 24, struct.pack("<Q", entry))
        wanted = self.expected[:24] + struct.pack("<Q", entry) + self.expected[32:]
        if self.read_memory(self.address, 56) != wanted:
            self.restore()
            raise TrialError("RAM command hook readback mismatch")

    def restore(self):
        if not self.armed:
            return
        self.check_session()  # Never replay a pointer write after a power loss.
        self.write_pointer(self.address + 24, self.saved)
        if self.read_memory(self.address, 56) != self.expected:
            raise TrialError("RAM command hook restoration could not be verified")
        self.armed = False
        self.console.emit("ram_alias_restored", address=f"0x{self.address:x}")


class XmodemCRC:
    """128-byte XMODEM-CRC, bounded retries; no text injection while active."""
    def __init__(self, console, *, block_timeout=2.0, retries=5):
        self.console = console
        self.block_timeout, self.retries = block_timeout, retries
        self.tail = b""

    @staticmethod
    def packet(number, data):
        if not 0 <= number <= 255 or len(data) != 128:
            raise ValueError("invalid XMODEM block")
        return bytes([1, number, 255 - number]) + data + struct.pack(">H", binascii.crc_hqx(data, 0))

    def control(self, end, accepted):
        cancelled = 0
        while self.console.clock() < end:
            data = self.console.read(1)
            if not data:
                continue
            self.tail = (self.tail + data)[-512:]
            if any(marker in self.tail for marker in (b"Resetting ", b"U-Boot ", b"SPI NAND", b"enter bootloader", b"Linux version")):
                self.console.invalidate()
                raise LinkLost("boot/stage marker during XMODEM; no binary retry or cancel replay")
            value = data[0]
            if value == 0x18:
                cancelled += 1
                if cancelled == 2:
                    raise TrialError("receiver cancelled XMODEM")
            else:
                cancelled = 0
            if value in accepted:
                return value
        return None

    def send(self, payload, *, total_timeout=180):
        c = self.console
        if not payload or not c.transferring or c.prompt:
            raise TrialError("XMODEM requires an explicitly armed binary receive session")
        c.window.require(total_timeout + 30)
        absolute_end = c.clock() + total_timeout
        try:
            if self.control(min(absolute_end, c.clock() + 15), {ord("C")}) != ord("C"):
                raise TrialError("receiver did not request XMODEM CRC")
            for index, at in enumerate(range(0, len(payload), 128), 1):
                packet = self.packet(index % 256, payload[at:at + 128].ljust(128, b"\x1a"))
                for _ in range(self.retries):
                    if c.clock() >= absolute_end:
                        raise TrialError("XMODEM overall deadline exceeded")
                    c.write(packet)
                    reply = self.control(min(absolute_end, c.clock() + self.block_timeout), {6, 21})
                    if reply == 6:
                        break
                else:
                    raise TrialError("XMODEM block retry limit exceeded")
            for _ in range(self.retries):
                if c.clock() >= absolute_end:
                    break
                c.write(b"\x04")
                if self.control(min(absolute_end, c.clock() + self.block_timeout), {6, 21}) == 6:
                    c.emit("xmodem_sent", bytes=len(payload), padded_bytes=(len(payload)+127)//128*128,
                           sha256=hashlib.sha256(payload).hexdigest(), crc32=f"{zlib.crc32(payload):08x}")
                    return
            raise TrialError("XMODEM end-of-transfer was not acknowledged")
        except LinkLost:
            # Never send CAN or replay data on an unrecognized new boot.
            raise
        except TrialError:
            c.write(b"\x18\x18")
            c.invalidate()
            raise
        finally:
            c.transferring = False


def open_serial(name, baud=115200):
    import serial
    port = serial.Serial(port=None, baudrate=baud, bytesize=8, parity="N", stopbits=1,
                         timeout=.1, write_timeout=2, xonxoff=False, rtscts=False, dsrdtr=False)
    port.dtr = False
    port.rts = False
    port.port = name
    port.open()  # Windows CreateFile is exclusive; never kill an existing owner.
    return port
