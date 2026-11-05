"""No UART is opened. Fault-injected console, XMODEM and RAM transactions."""
import binascii
from pathlib import Path
import struct
import sys
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.uart import AliasHook, Console, LinkLost, PowerWindow, Span, TrialError, XmodemCRC, parse_md, parse_md_words, validate_placement


class WordReadbackTests(unittest.TestCase):
    def test_mmio_word_reads_preserve_high_bits_and_little_endian_bytes(self):
        text="10e20000: 20000003 01259549 00fdcfe0 55000000    ....I.%........U\n"
        self.assertEqual(parse_md_words(text,0x10e20000,4),struct.pack("<4I",0x20000003,0x01259549,0x00fdcfe0,0x55000000))
        self.assertEqual(parse_md_words(text,0x10e20000,1),struct.pack("<I",0x20000003))
        self.assertTrue(Console.READ_COMMAND.fullmatch("md.l 10e20000 20"))

    def test_short_duplicate_gapped_and_misaligned_words_fail(self):
        line="10e20000: 20000003\n"
        for text,at,n in ((line,0x10e20000,2),(line+line,0x10e20000,2),
                          (line+"10e20008: 00000000\n",0x10e20000,2),(line,0x10e20001,1),
                          (line,0x10e20000,0),("10e20000: 03 00 00 20\n",0x10e20000,1)):
            with self.subTest(text=text,at=at,n=n),self.assertRaises(TrialError):parse_md_words(text,at,n)


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class FakePort:
    def __init__(self, clock, incoming=b"", on_write=lambda port, data: None):
        self.clock, self.incoming, self.sent, self.on_write = clock, bytearray(incoming), [], on_write
        self.lost = False

    def read(self, size):
        self.clock.value += .05
        if self.lost:
            raise OSError("unplugged")
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        return result

    def write(self, data):
        if self.lost:
            raise OSError("unplugged")
        self.sent.append(bytes(data))
        self.on_write(self, bytes(data))
        return len(data)


class UartTests(unittest.TestCase):
    def test_continuous_power_requires_explicit_selection_and_a_fresh_generation(self):
        clock=Clock();periodic=PowerWindow(clock);continuous=PowerWindow(clock,continuous_power_confirmed=True)
        with self.assertRaises(TrialError):continuous.require(1)
        periodic.boot();continuous.boot();clock.value=1200
        with self.assertRaises(TrialError):periodic.require(1)
        continuous.require(900);continuous.invalidate()
        with self.assertRaises(TrialError):continuous.require(1)

    def console(self, incoming=b"", on_write=lambda port, data: None):
        clock = Clock()
        port = FakePort(clock, incoming, on_write)
        console = Console(port, clock=clock)
        return clock, port, console

    def test_unknown_console_never_receives_cr_or_spaces(self):
        for text in (b"", b"root@router:~# ", b"old log mentions U-Boot but is not a banner\r\n"):
            with self.subTest(text=text):
                _, port, console = self.console(text)
                with self.assertRaisesRegex(TrialError, "no fresh"):
                    console.wait_boot_prompt(.5)
                self.assertEqual(port.sent, [])
                self.assertIsNone(console.window.started)

    def test_new_boot_can_be_interrupted_but_not_replayed(self):
        def send(port, data):
            self.assertEqual(data, b" ")
            port.incoming += b"\r\n=> "
        _, port, console = self.console(b"\r\nU-Boot 2021.01-svn308776\r\nHit any key", send)
        console.wait_boot_prompt(2)
        self.assertTrue(console.prompt)
        self.assertEqual(port.sent, [b" "])
        console.invalidate()
        with self.assertRaises(TrialError):
            console.inspect("version")
        self.assertEqual(port.sent, [b" "])

    def test_slow_vendor_init_does_not_type_until_countdown(self):
        clock, port, console = self.console(b"\r\npll init, cpu freq=1000 MHz\r\n")
        original_read = port.read
        injected = False
        def read(size):
            nonlocal injected
            if clock.value >= 22 and not injected:
                injected = True
                port.incoming += b"Hit any key to stop autoboot:  1 "
            return original_read(size)
        port.read = read
        def respond(p, data):
            self.assertGreaterEqual(clock.value, 22)
            self.assertEqual(data, b" ")
            p.incoming += b"\r\n=> "
        port.on_write = respond
        console.wait_boot_prompt(30)
        self.assertTrue(console.prompt)
        self.assertEqual(port.sent, [b" "])

    def test_early_reset_banner_cannot_extend_natural_power_window(self):
        clock, port, console = self.console(b"\r\nU-Boot 2021.01-svn308776\r\nHit any key\r\n=> ")
        with self.assertRaisesRegex(TrialError, "no fresh"):
            console.wait_boot_prompt(2, not_before=1)
        self.assertIsNone(console.window.started)
        self.assertEqual(port.sent, [])

    def test_boot_banner_without_countdown_does_not_type(self):
        _, port, console = self.console(b"\r\nU-Boot 2021.01-svn308776\r\n")
        with self.assertRaises(TrialError):
            console.wait_boot_prompt(1)
        self.assertEqual(port.sent, [])

    def test_windows_fast_drain_wins_the_real_100ms_countdown(self):
        clock = Clock()
        class TimedPort(FakePort):
            @property
            def in_waiting(self): return len(self.incoming)
            def read(self, size):
                clock.value += .11 if size > len(self.incoming) else .001
                result = bytes(self.incoming[:size])
                del self.incoming[:size]
                return result
        def respond(port, data):
            self.assertLess(clock.value,.1)
            port.incoming += b"\r\n=> "
        port = TimedPort(clock,b"\r\nU-Boot 2021.01-svn308776\r\nHit any key to stop autoboot:  1 ",respond)
        console = Console(port,clock=clock)
        console.wait_boot_prompt(1)
        self.assertTrue(console.prompt)
        self.assertEqual(port.sent,[b" "])

    def test_batched_stale_boot_plus_kernel_never_receives_input(self):
        _, port, console = self.console(b"\r\nU-Boot 2021.01-svn308776\r\nHit any key\r\nStarting kernel ...\r\n")
        with self.assertRaisesRegex(TrialError,"kernel stage"):
            console.wait_boot_prompt(1)
        self.assertFalse(console.identified)
        self.assertEqual(port.sent,[])

    def test_read_allowlist_and_version_banner_are_not_reboot(self):
        def respond(port, data):
            command = data.strip().decode()
            replies = {"version": "U-Boot 2021.01-svn308776 (Apr 17 2025)",
                       "help": "\n".join(x + " - test" for x in ("loadx", "md", "mw", "crc32", "bdinfo", "?")),
                       "bdinfo": "DRAM bank = 0\nstart = 0x80000000\nsize = 0x20000000"}
            port.incoming += (command + "\r\n" + replies[command] + "\r\n=> ").encode()
        _, port, console = self.console(on_write=respond)
        console.window.boot()
        console.prompt = True
        console.identify()
        self.assertTrue(console.identified)
        for command in ("saveenv", "reset", "nand erase 0", "setenv x y", "version; reset", "md.b 1 2\rreset", "mw.q 1234 0"):
            with self.assertRaisesRegex(TrialError, "allowed inspection"):
                console.inspect(command)
        self.assertEqual(len(port.sent), 3)

    def test_unknown_revision_cannot_gain_ram_authority(self):
        _, _, console = self.console(on_write=lambda p, d: p.incoming.extend(b"U-Boot 2021.01-svn358069\r\n=> "))
        console.window.boot()
        console.prompt = True
        with self.assertRaisesRegex(TrialError, "unknown U-Boot"):
            console.identify()
        self.assertFalse(console.identified)

    def test_timeout_and_unplug_invalidate_identity_and_generation(self):
        for unplug in (False, True):
            _, port, console = self.console()
            console.window.boot()
            generation = console.window.generation
            console.prompt = console.identified = True
            port.lost = unplug
            with self.assertRaises(TrialError):
                console.inspect("bdinfo", timeout=.2)
            self.assertFalse(console.identified)
            self.assertFalse(console.prompt)
            self.assertGreater(console.window.generation, generation)

    def test_power_window_bounds_and_reboot_generation(self):
        clock = Clock()
        window = PowerWindow(clock)
        with self.assertRaisesRegex(TrialError, "fresh boot"):
            window.remaining()
        window.boot()
        clock.value = 17 * 60
        self.assertEqual(window.remaining(), 0)
        self.assertEqual(window.remaining(cleanup=True), 60)
        with self.assertRaises(TrialError):
            window.require(1)
        window.require(59, cleanup=True)
        window.invalidate()
        with self.assertRaises(TrialError):
            window.require(1, cleanup=True)

    def test_memory_map_rejects_all_overlaps_and_unmeasured_ranges(self):
        banks = [Span(0x80000000, 0x20000000, "dram")]
        reserved = [Span(0x9f000000, 0x1000000, "uboot-stack-dma")]
        image = Span(0x89000000, 0x200000, "image")
        upload = Span(0x8b000000, 0x4000000, "upload")
        validate_placement(banks, reserved, [image, upload])
        for allocations in ([image, image], [Span(0x9f000000, 64, "oops")],
                            [Span(0x70000000, 64, "outside")], [Span(0x89000001, 64, "unaligned")],
                            [Span((1 << 64)-64, 128, "overflow")]):
            with self.assertRaises(TrialError):
                validate_placement(banks, reserved, allocations)
        with self.assertRaises(TrialError):
            validate_placement(banks, [], [image])

    def test_md_readback_rejects_short_and_duplicate_lines(self):
        text = "md.b 89000000 14\r\n89000000: " + "00 " * 16 + " ...\r\n89000010: 01 02 03 04 ....\r\n=> "
        self.assertEqual(parse_md(text, 0x89000000, 20), bytes(16) + bytes([1, 2, 3, 4]))
        for bad in (text.replace("89000010:", "89000000:"), text.replace("01 02 03 04", "01 02")):
            with self.assertRaises(TrialError):
                parse_md(bad, 0x89000000, 20)

    def test_xmodem_crc_packets_padding_wrap_and_retry(self):
        attempts = {}
        def receiver(port, data):
            if data == b"\x04":
                port.incoming += b"\x06"
                return
            self.assertEqual(len(data), 133)
            self.assertEqual(data[1] ^ data[2], 255)
            self.assertEqual(struct.unpack(">H", data[-2:])[0], binascii.crc_hqx(data[3:-2], 0))
            number = data[1]
            attempts[number] = attempts.get(number, 0) + 1
            port.incoming += b"\x15" if number == 1 and attempts[number] == 1 else b"\x06"
        _, port, console = self.console(b"C", receiver)
        console.window.boot()
        console.transferring = True
        payload = bytes(range(256)) * 128 + b"abc"
        XmodemCRC(console).send(payload)
        self.assertEqual(port.sent[0], port.sent[1])
        self.assertTrue(any(len(p) == 133 and p[1] == 0 for p in port.sent))
        self.assertEqual(port.sent[-2][3:-2], b"abc" + b"\x1a" * 125)
        self.assertFalse(console.transferring)

    def test_xmodem_cancel_deadline_and_no_crc_handshake_fail_closed(self):
        for initial in (b"\x18\x18", b"\x15", b""):
            _, port, console = self.console(initial)
            console.window.boot()
            console.identified = console.transferring = True
            with self.assertRaises(TrialError):
                XmodemCRC(console, block_timeout=.1, retries=2).send(b"test", total_timeout=.2)
            self.assertEqual(port.sent, [b"\x18\x18"])
            self.assertFalse(console.transferring)
            self.assertFalse(console.identified)

    def test_xmodem_block_error_retries_are_bounded(self):
        def bad(port, data):
            if len(data) == 133:
                port.incoming += b"\x15"
        _, port, console = self.console(b"C", bad)
        console.window.boot()
        console.transferring = True
        with self.assertRaisesRegex(TrialError, "retry limit"):
            XmodemCRC(console, retries=2).send(b"data")
        self.assertEqual(sum(len(x) == 133 for x in port.sent), 2)

    def test_unplug_during_binary_never_sends_cancel_to_a_new_boot(self):
        def lose(port, data):
            port.lost = True
        _, port, console = self.console(b"C", lose)
        console.window.boot()
        console.identified = console.transferring = True
        with self.assertRaises(LinkLost):
            XmodemCRC(console).send(b"data")
        self.assertEqual(len(port.sent), 1)
        self.assertNotIn(b"\x18\x18", port.sent)
        self.assertFalse(console.identified)

    def test_reset_during_binary_never_sends_cancel_or_old_block(self):
        def reset(port, data):
            port.incoming += b"\r\nSPI NAND\r\nU-Boot 2021.01-svn308776\r\n"
        _, port, console = self.console(b"C", reset)
        console.window.boot()
        console.identified = console.transferring = True
        with self.assertRaisesRegex(LinkLost, "boot/stage"):
            XmodemCRC(console).send(b"data")
        self.assertEqual(len(port.sent), 1)
        self.assertFalse(console.identified)

    def test_abort_during_version_invalidates_session(self):
        _, _, console = self.console(on_write=lambda p, d: p.incoming.extend(b'Synchronous Abort\r\n=> '))
        console.window.boot()
        console.identified = console.prompt = True
        with self.assertRaises(LinkLost):
            console.inspect("version")
        self.assertFalse(console.identified)

    def test_loadx_checks_exact_or_padded_length_and_crc(self):
        payload = b"safe ram payload"
        banks = [Span(0x80000000, 0x20000000, "dram")]
        reserved = [Span(0x9f000000, 0x1000000, "monitor")]
        for length, crc, allowed in ((len(payload), zlib.crc32(payload), True),
                                      (128, zlib.crc32(payload), True),
                                      (127, zlib.crc32(payload), False),
                                      (128, zlib.crc32(payload) ^ 1, False)):
            def receiver(port, data):
                if data.startswith(b"loadx "):
                    port.incoming += b"C"
                elif len(data) == 133:
                    port.incoming += b"\x06"
                elif data == b"\x04":
                    port.incoming += f"\x06\r\nTotal Size = 0x{length:x}\r\n=> ".encode()
                elif data.startswith(b"crc32 "):
                    port.incoming += f"CRC32 for 89000000 ==> {crc:08x}\r\n=> ".encode()
            _, port, console = self.console(on_write=receiver)
            console.window.boot()
            console.identified = console.prompt = True
            with self.subTest(length=length, crc=crc):
                if allowed:
                    console.loadx(payload, Span(0x89000000, 4096, "image"), banks=banks, reserved=reserved)
                else:
                    with self.assertRaises(TrialError):
                        console.loadx(payload, Span(0x89000000, 4096, "image"), banks=banks, reserved=reserved)
                self.assertTrue(console.prompt)
                self.assertFalse(any(x.startswith(b"?") for x in port.sent))

    def test_alias_restore_and_no_write_after_generation_change(self):
        _, _, console = self.console()
        console.window.boot()
        console.identified = console.prompt = True
        original = bytes(range(56))
        memory, writes = bytearray(original), []
        def write(at, data):
            writes.append((at, data))
            memory[at - 0x9f001000:at - 0x9f001000 + len(data)] = data
        hook = AliasHook(console, 0x9f001000, original, read_memory=lambda a, n: bytes(memory), write_pointer=write)
        hook.install(0x89000000)
        self.assertEqual(bytes(memory[24:32]), struct.pack("<Q", 0x89000000))
        hook.restore()
        self.assertEqual(bytes(memory), original)
        self.assertFalse(hook.armed)
        hook.install(0x89000000)
        count = len(writes)
        console.invalidate()
        with self.assertRaises(LinkLost):
            hook.restore()
        self.assertEqual(len(writes), count)

    def test_alias_write_failure_is_still_marked_for_restoration(self):
        _, _, console = self.console()
        console.window.boot()
        console.identified = console.prompt = True
        original = bytes(range(56))
        def fail(at, data):
            raise OSError("write failed")
        hook = AliasHook(console, 0x9f001000, original, read_memory=lambda a, n: original, write_pointer=fail)
        with self.assertRaises(OSError):
            hook.install(0x89000000)
        self.assertTrue(hook.armed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
