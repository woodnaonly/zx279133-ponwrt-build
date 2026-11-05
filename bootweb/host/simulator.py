"""Sparse, ECC-interface-shaped NAND model. It has NO device/file write backend."""
import ctypes as C
import threading
from .core import BAD, READ, ERASE, WRITE, VERIFY_BOOT, WATCH, PROGRESS, Port, PREPARE_UBOOT, NAND_NOW

ERASE_SIZE, PAGE_SIZE, FLASH_SIZE = 0x20000, 0x800, 0x10000000


class SparseNand:
    def __init__(self, boot, *, capabilities=0x7f, progress=None):
        if len(boot) != 0x200000:
            raise ValueError("simulated boot seed must be exactly 2 MiB")
        self.blocks = {off: bytearray(boot[off:off + ERASE_SIZE])
                       for off in range(0, len(boot), ERASE_SIZE)}
        self.bad, self.fail_erase, self.fail_write, self.fail_read = set(), set(), set(), set()
        self.corrupt_readback = None
        self.mutate_protected = False
        self.events, self.errors = [], []
        self.lock = threading.RLock()
        self.progress = progress or (lambda phase, done, total: None)
        self.clock = 1000
        self.prepare_error = False
        self.budget = None
        # Keep callback objects alive for the complete C call.
        self.callbacks = (BAD(self._bad), READ(self._read), ERASE(self._erase),
                          WRITE(self._write), VERIFY_BOOT(lambda ctx, data, size: 0),
                          WATCH(lambda ctx: None), PROGRESS(self._progress))
        self.port = Port(None, capabilities, *self.callbacks)
        # Synthetic time bounds for an in-memory model ONLY. No hardware
        # adapter imports this module or these numbers.
        self.prepare_callback = PREPARE_UBOOT(self._prepare_uboot)
        self.now_callback = NAND_NOW(lambda ctx: self.clock)
        self.port.prepare_uboot_write = self.prepare_callback
        self.port.milliseconds = self.now_callback

    def _prepare_uboot(self, ctx, identity, data, size, budget):
        if self.prepare_error or size != 0x160000:
            return -1
        values = dict(power_started_ms=0, power_uncertainty_ms=10, erase_max_ms=10,
                      page_max_ms=1, read_max_ms=2, hash_max_ms=2,
                      bad_query_max_ms=1, service_max_ms=1, fixed_max_ms=100)
        if self.budget is not None:
            values.update(self.budget)
        for name, value in values.items():
            setattr(budget.contents, name, value)
        return 0

    def read(self, offset, size):
        if offset < 0 or size < 0 or offset + size > FLASH_SIZE:
            raise ValueError("simulated read overflow")
        out = bytearray()
        with self.lock:
            while size:
                block = offset // ERASE_SIZE * ERASE_SIZE
                at = offset - block
                take = min(size, ERASE_SIZE - at)
                out += (self.blocks[block][at:at + take] if block in self.blocks else b"\xff" * take)
                offset, size = offset + take, size - take
        return bytes(out)

    def _bad(self, ctx, off):
        return int(off in self.bad)

    def _read(self, ctx, off, dest, size):
        try:
            if any(off <= item < off + size for item in self.fail_read):
                return -1
            data = self.read(off, size)
            if self.corrupt_readback is not None and self.events and off <= self.corrupt_readback < off + size:
                changed = bytearray(data)
                changed[self.corrupt_readback - off] ^= 1
                data = bytes(changed)
            C.memmove(dest, data, size)
            return 0
        except Exception as error:
            self.errors.append(str(error))
            return -1

    @staticmethod
    def writable(off, size):
        return (0x20000 <= off and off + size <= 0x180000 or
                0x200000 <= off and off + size <= 0xB00000 or
                0xF00000 <= off and off + size <= FLASH_SIZE)

    def _erase(self, ctx, off):
        try:
            if off % ERASE_SIZE or not self.writable(off, ERASE_SIZE) or off in self.bad:
                raise ValueError("protected/bad/misaligned erase")
            if off in self.fail_erase:
                return -1
            with self.lock:
                self.events.append(("erase", off))
                # An erased block is sparse. Existing zero-filled data disappears.
                self.blocks.pop(off, None)
                if self.mutate_protected:
                    self.blocks[0x180000][0] ^= 1
                    self.mutate_protected = False
            return 0
        except Exception as error:
            self.errors.append(str(error))
            return -1

    def _write(self, ctx, off, src):
        try:
            if off % PAGE_SIZE or not self.writable(off, PAGE_SIZE) or off // ERASE_SIZE * ERASE_SIZE in self.bad:
                raise ValueError("protected/bad/misaligned page write")
            if off in self.fail_write:
                return -1
            with self.lock:
                block = off // ERASE_SIZE * ERASE_SIZE
                page = off - block
                data = C.string_at(src, PAGE_SIZE)
                target = self.blocks.setdefault(block, bytearray(b"\xff" * ERASE_SIZE))
                if any(a & b != b for a, b in zip(target[page:page + PAGE_SIZE], data)):
                    raise ValueError("NAND 0-to-1 write")
                target[page:page + PAGE_SIZE] = data
                self.events.append(("write", off))
            return 0
        except Exception as error:
            self.errors.append(str(error))
            return -1

    def _progress(self, ctx, phase, done, total):
        try:
            self.progress(phase.decode(), done, total)
        except Exception as error:
            self.errors.append(str(error))
