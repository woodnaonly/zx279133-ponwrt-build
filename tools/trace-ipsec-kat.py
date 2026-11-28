#!/usr/bin/env python3
"""Trace factory synchronous and asynchronous IPsec requests in Unicorn.

This is a probe of CPU-side encoding and ordering, not an engine emulator.
Synchronous completion is synthesized; async runs stop at submission.
It never performs encryption.
"""

import argparse
import hashlib
import json
import struct
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE
from unicorn.arm64_const import UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2

from ponelf import Ko


FACTORY_SHA256 = "99ea61b5ac6658da319641ead113520b5835f576529b59ba7d61c0a608495498"
BTLL, ENGINE = 0x12000000, 0x12400000
DDR, VDDR, DDR_SIZE = 0x90000000, 0xA0000000, 0x200000
EXTERNAL, STACK, WORK = 0x27000000, 0x31000000, 0x32000000
MASK64 = (1 << 64) - 1
SECTIONS = (".text", ".rodata", ".rodata.str1.1", ".data", ".bss")
SUBMIT = {0x180000: (0x1000E0, 0x180020), 0x180010: (0x1000F0, 0x180030)}
MODES = ("ecb", "cbc", "ctr")


def cipher_opcode(ko, mode):
    # crypt_get_driver searches 0xa8-byte entries by their driver name.
    data = ko.bytes_of(".data")
    for start in range(0x2B800, 0x2C670, 0xA8):
        if data[start:start + 0x80].split(b"\x00", 1)[0] == f"{mode}-aes-zxic".encode():
            return struct.unpack_from("<I", data, start + 0x84)[0]
    raise ValueError(f"no {mode.upper()} AES driver entry")


class Trace:
    def __init__(self, ko):
        self.u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.base = {name: 0x20000000 + i * 0x1000000 for i, name in enumerate(SECTIONS)}
        self.syms = {s["name"]: s for s in ko.syms() if s["name"]}
        self.extern = {}
        self.unmapped = {}
        self.mmio = []
        self.opcodes = {mode: cipher_opcode(ko, mode) for mode in MODES}
        self.synchronous = True
        for name in SECTIONS:
            sec = ko.section(name)
            addr = self.base[name]
            self.u.mem_map(addr, (sec["size"] + 4095) & ~4095)
            if sec["typ"] != 8:
                self.u.mem_write(addr, ko.bytes_of(name))
        for addr, size in ((BTLL, 0x1000000), (VDDR, DDR_SIZE),
                           (EXTERNAL, 0x1000), (STACK, 0x10000), (WORK, 0x1000)):
            self.u.mem_map(addr, size)
        self.relocate(ko)
        self.u.hook_add(UC_HOOK_CODE, self.external, begin=EXTERNAL, end=EXTERNAL + 0xFFF)
        self.u.hook_add(UC_HOOK_CODE, self.unsupported, begin=self.base[".text"],
                        end=self.base[".text"] + ko.section(".text")["size"] - 1)
        self.u.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.access,
                        begin=BTLL, end=BTLL + 0xFFFFFF)

    def addr(self, sym):
        s = self.syms[sym]
        if s["sec"] in self.base:
            return self.base[s["sec"]] + s["value"]
        return self.external_address(sym)

    def external_address(self, sym):
        if sym not in self.extern:
            assert len(self.extern) < 128
            self.extern[sym] = EXTERNAL + 16 * len(self.extern)
        return self.extern[sym]

    def relocate(self, ko):
        names = ko.syms()
        rela = ko.bytes_of(".rela.text")
        for off, info, addend in struct.iter_unpack("<QQq", rela):
            s = names[info >> 32]
            op = info & 0xFFFFFFFF
            site = self.base[".text"] + off
            if 0x12D8D4 <= off < 0x13396C:
                if s["sec"] in self.base:
                    target = self.base[s["sec"]] + s["value"] + addend
                elif s["sec"] == "UND" and s["name"]:
                    target = self.external_address(s["name"]) + addend
                else:
                    self.unmapped[site] = (s["sec"], s["name"], op)
                    continue
                word = self.read32(site)
                if op in (282, 283):  # JUMP26 / CALL26
                    assert not (target - site) & 3 and abs(target - site) < 1 << 27
                    word = (word & 0xFC000000) | (((target - site) // 4) & 0x3FFFFFF)
                elif op == 275:  # ADR_PREL_PG_HI21
                    page = (target >> 12) - (site >> 12)
                    word = (word & 0x9F00001F) | ((page & 3) << 29) | ((page & 0x1FFFFC) << 3)
                elif op in (277, 278, 284, 285, 286, 299):
                    scale = {277: 1, 278: 1, 284: 2, 285: 4, 286: 8, 299: 16}[op]
                    assert target % scale == 0
                    word = (word & ~(0xFFF << 10)) | (((target & 0xFFF) // scale) << 10)
                else:
                    self.unmapped[site] = (s["sec"], s["name"], op)
                    continue
                self.write32(site, word)

    def read32(self, addr):
        return struct.unpack("<I", self.u.mem_read(addr, 4))[0]

    def write32(self, addr, value):
        self.u.mem_write(addr, struct.pack("<I", value))

    def write64(self, addr, value):
        self.u.mem_write(addr, struct.pack("<Q", value & MASK64))

    def unsupported(self, uc, addr, size, data):
        if addr in self.unmapped:
            raise RuntimeError(f"unhandled relocation at {addr:#x}: {self.unmapped[addr]}")

    def external(self, uc, addr, size, data):
        name = next((name for name, a in self.extern.items() if addr == a), None)
        x0, x1, x2 = (uc.reg_read(reg) for reg in (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2))
        if name == "memset":
            assert x2 <= DDR_SIZE
            uc.mem_write(x0, bytes([x1 & 255]) * x2)
        elif name == "memcpy":
            assert x2 <= DDR_SIZE
            uc.mem_write(x0, bytes(uc.mem_read(x1, x2)))
        elif name == "printk":
            uc.reg_write(UC_ARM64_REG_X0, 0)
        elif name == "__ll_sc_atomic64_add":
            current, = struct.unpack("<Q", uc.mem_read(x1, 8))
            self.write64(x1, current + x0)
        else:
            raise RuntimeError(f"unexpected external call: {name} at {addr:#x}")
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))

    def access(self, uc, kind, addr, size, value, data):
        assert size == 4
        write = kind == UC_MEM_WRITE
        self.mmio.append(["write" if write else "read", f"{addr:#010x}",
                          f"{(value if write else self.read32(addr)) & 0xffffffff:#010x}"])
        if self.synchronous and write and addr - ENGINE in SUBMIT:
            count, fifo = SUBMIT[addr - ENGINE]
            assert DDR <= value < DDR + DDR_SIZE
            self.write32(ENGINE + count, 1)
            self.write32(ENGINE + fifo, value)

    def run(self, sym, arg=0, arg1=0):
        end = STACK + 0xF000
        self.u.reg_write(UC_ARM64_REG_SP, end)
        self.u.reg_write(UC_ARM64_REG_LR, end)
        self.u.reg_write(UC_ARM64_REG_X0, arg)
        self.u.reg_write(UC_ARM64_REG_X1, arg1)
        self.u.emu_start(self.addr(sym), end, count=20000)
        if self.u.reg_read(UC_ARM64_REG_PC) != end:
            raise RuntimeError(f"instruction limit in {sym}")

    def request(self, decrypt, mode="ecb", key_bytes=16, length=16, queue=None):
        self.synchronous = queue is None
        assert key_bytes in (16, 24, 32) and 0 < length <= 64
        assert mode == "ctr" or length % 16 == 0
        assert queue in (None, 0, 1)
        self.write64(self.addr("ipsec_base"), ENGINE)
        self.write64(self.addr("ipsec_bttl_base"), BTLL)
        self.write64(self.addr("ipsec_ddr_phy_start"), DDR)
        self.write64(self.addr("memstart_addr"), -(1 << 38) + DDR - VDDR)
        self.run("zx_ipsec_init_for_rest")
        init_mmio, self.mmio = self.mmio, []
        slots = self.base[".bss"] + 0x3A300
        pd, khb, key, dyn = struct.unpack("<4Q", self.u.mem_read(slots, 32))
        assert (pd, khb, key, dyn) == (DDR, DDR + 0x40, DDR + 0x80000, DDR + 0x120000)
        key_data = bytes(range(key_bytes))
        iv = bytes(range(0xa0, 0xb0)) if mode != "ecb" else b""
        self.u.mem_write(WORK + 0x100, key_data)
        self.u.mem_write(WORK + 0x140, iv or b"\0")
        opcode = self.opcodes[mode]
        for off, value in ((0x0, decrypt), (0x10, length), (0x30, length), (0x68, len(iv)),
                           (0x98, key_bytes), (0xE0, opcode)):
            self.write32(WORK + off, value)
        for off, value in ((0x8, DDR + 0x1F0000), (0x28, DDR + 0x1F0040),
                           (0x60, WORK + 0x140 if iv else 0), (0x90, WORK + 0x100)):
            self.write64(WORK + off, value)
        dump = lambda at, size: self.u.mem_read(at - DDR + VDDR, size).hex()
        if self.synchronous:
            self.run("ipsec_pd_khb_set", WORK)
        else:
            self.run("ipsec_apd_khb_set", WORK, queue)
            delta = 0x10 if decrypt else 0
            count = ENGINE + 0x1000C0 + delta + 4 * queue
            credit = ENGINE + 0x100080 + delta + 4 * queue
            fifo = ENGINE + 0x180000 + delta + 4 * queue
            accesses = [(op, int(addr, 16), int(val, 16)) for op, addr, val in self.mmio]
            assert (len(accesses) == 3 and accesses[:2] == [("read", count, 0), ("write", credit, 1)]
                    and accesses[2][:2] == ("write", fifo)), accesses
            pd = accesses[2][2]
            assert DDR <= pd <= DDR + DDR_SIZE - 64
            khb = int(dump(pd + 0x24, 4), 16)
            assert DDR <= khb <= DDR + DDR_SIZE - 64
            key = int(dump(khb + 0x30, 4), 16)
            dyn = int(dump(khb + 0x34, 4), 16)
            assert all(DDR <= addr <= DDR + DDR_SIZE - 64 for addr in (key, dyn))
        return {"direction": "decrypt" if decrypt else "encrypt", "mmio": self.mmio,
                "init_mmio": init_mmio, "opcode": opcode, "mode": mode,
                "key_bytes": key_bytes, "length": length, "async_queue": queue,
                "addresses": {"pd": pd, "khb": khb, "key_context": key, "dynamic_context": dyn},
                "pd": dump(pd, 64), "khb": dump(khb, 64),
                "key_context": dump(key, 32), "dynamic_context": dump(dyn, 32)}


def request_matrix(ko):
    lengths = {"ecb": (16, 32), "cbc": (16, 32), "ctr": (16, 31)}
    for mode in MODES:
        for key_bytes in (16, 24, 32):
            for length in lengths[mode]:
                for queue in (None, 0, 1):
                    for decrypt in (False, True):
                        yield Trace(ko).request(decrypt, mode, key_bytes, length, queue)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", type=Path, help="original SK-D840N np_133.ko")
    parser.add_argument("--matrix", action="store_true", help="trace AES modes, key sizes and async queues")
    args = parser.parse_args()
    ko = Ko(args.module)
    if ko.machine != 183 or hashlib.sha256(ko.d).hexdigest() != FACTORY_SHA256:
        parser.error("unexpected vendor module; the trace is pinned to one build")
    if args.matrix:
        result = list(request_matrix(ko))
    else:
        result = [Trace(ko).request(direction) for direction in (False, True)]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
