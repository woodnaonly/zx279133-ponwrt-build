#!/usr/bin/env python3
"""Run the compiled IPsec KAT against a synthetic MMIO device, not hardware."""

import argparse
import hashlib
import importlib
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2
from unicorn.arm64_const import UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6
from unicorn.arm64_const import UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP

import kcheck
from ponelf import Ko


trace = importlib.import_module("trace-ipsec-kat")
BTLL, QC, DDR, STACK = 0x12000000, 0x12500000, 0x90000000, 0x200000
SEC = 0x13000000  # Independent VA, deliberately not BTLL + physical offset.
STOP = STACK + 0xf800
QUEUES = ((0xe0, 0x80, 0x80000, 0x80020, 0xa0),
          (0xf0, 0x90, 0x80010, 0x80030, 0xb0))
CLEAR = bytes.fromhex("00112233445566778899aabbccddeeff")
CIPHER = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
DECRYPT_SEED = bytes.fromhex("13111d7fe3944a17f307a78b4d2b30c5")
STUBS = ("_dev_err", "_dev_info", "memset", "memset_io", "memcpy", "memcpy_toio", "memcpy_fromio",
         "memcmp", "ktime_get", "usleep_range_state")


def compile_kat(args, scratch):
    obj, stub, elf = (scratch / name for name in ("kat.o", "stub.o", "kat.elf"))
    clang = kcheck.newest_clang(args.clang)
    objcopy, lld = (shutil.which(name) for name in ("llvm-objcopy", "ld.lld"))
    if not all((clang, objcopy, lld)):
        raise SystemExit("clang, llvm-objcopy and ld.lld are required")
    source = args.source or args.tree / "drivers/crypto/zx279133-ipsec-kat.c"
    flags = ["-O0", "-ffunction-sections", "-fdata-sections", "-fno-stack-protector",
             "-fno-asynchronous-unwind-tables"]
    subprocess.run([clang, "-c", *flags, *kcheck.build_base_args(args.tree, args.gen),
                    str(source), "-o", str(obj)], check=True)
    stubs = "\n".join(f"void {name}(void) {{}}" for name in STUBS)
    subprocess.run([clang, "-target", "aarch64-linux-gnu", "-c", "-x", "c",
                    "-ffreestanding", "-o", str(stub), "-"], input=stubs,
                   text=True, check=True)
    subprocess.run([objcopy, "--globalize-symbol=zx_kat", "--globalize-symbol=zx_init",
                    str(obj)], check=True)
    subprocess.run([lld, "--image-base=0", "--gc-sections", "--entry=zx_kat", "--undefined=zx_init",
                    "-Ttext=0x10000", str(obj), str(stub), "-o", str(elf)], check=True)
    return Ko(elf)


class Model:
    def __init__(self, elf, decrypt, scenario, dma=DDR):
        self.u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.stub = {s["value"]: s["name"] for s in elf.syms() if s["name"] in STUBS}
        self.symbols = {s["name"]: s["value"] for s in elf.syms()}
        self.q = QUEUES[decrypt]
        self.key = DECRYPT_SEED if decrypt else bytes(range(16))
        self.output = CLEAR if decrypt else CIPHER
        self.scenario = scenario
        self.dma = dma
        self.writes = []
        self.btll_writes = []
        self.reads = []
        self.now = 0
        self.u.mem_map(0x10000, 0x40000)
        self.u.mem_map(STACK, 0x10000)
        self.u.mem_map(BTLL, 0x5000)
        self.u.mem_map(SEC, 0x1000)
        self.u.mem_map(QC, 0x100000)
        self.u.mem_map(dma, 0x200000)
        for section in elf.sh:
            if section["flags"] & 2 and section["size"]:
                self.u.mem_write(section["addr"], elf.bytes_of(section["sname"]))
        if scenario == "stale":
            self.write32(QC + QUEUES[not decrypt][0], 1)
        if scenario == "pending":
            self.write32(QC + (0xd4 if decrypt else 0xc4), 1)
        self.u.mem_write(dma, b"\xa5" * 128)
        self.u.hook_add(UC_HOOK_CODE, self.external)
        self.u.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.mmio,
                        begin=QC, end=QC + 0xfffff)
        self.u.hook_add(UC_HOOK_MEM_WRITE, self.record_btll,
                        begin=BTLL, end=BTLL + 0x4fff)
        self.u.hook_add(UC_HOOK_MEM_WRITE, self.record_btll,
                        begin=SEC, end=SEC + 0xfff)
        if scenario == "unlatched":
            self.u.hook_add(UC_HOOK_MEM_READ, self.unlatched,
                            begin=BTLL + 4, end=BTLL + 7)

    def unlatched(self, uc, kind, addr, size, value, data):
        self.write32(addr, 0)

    def record_btll(self, uc, kind, addr, size, value, data):
        assert size == 4
        self.btll_writes.append((addr, value))

    def write32(self, addr, value):
        self.u.mem_write(addr, struct.pack("<I", value))

    def mmio(self, uc, kind, addr, size, value, data):
        assert size == 4
        count, _, submit, complete, _ = self.q
        off = addr - QC
        if kind != UC_MEM_WRITE:
            self.reads.append(off)
            return
        self.writes.append((off, value))
        if off != submit or self.scenario == "timeout":
            return
        assert self.u.mem_read(self.dma + 0x80000, 16) == self.key
        self.write32(QC + count, 2 if self.scenario == "count" else 1)
        self.write32(QC + complete, self.dma + (0x40 if self.scenario == "address" else 0))
        if self.scenario != "data":
            uc.mem_write(self.dma + 0x1f0040, self.output)

    def external(self, uc, addr, size, data):
        name = self.stub.get(addr)
        if name is None:
            return
        x0, x1, x2 = (uc.reg_read(reg) for reg in (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2))
        result = x0
        if name in ("memset", "memset_io"):
            assert x2 <= 0x200000
            uc.mem_write(x0, bytes([x1 & 255]) * x2)
        elif name in ("memcpy", "memcpy_toio", "memcpy_fromio"):
            assert x2 <= 64
            uc.mem_write(x0, bytes(uc.mem_read(x1, x2)))
        elif name == "memcmp":
            assert x2 <= 64
            result = int(uc.mem_read(x0, x2) != uc.mem_read(x1, x2))
        elif name == "ktime_get":
            result = self.now
            self.now += 5_000_000
        uc.reg_write(UC_ARM64_REG_X0, result)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))

    def call(self, symbol, *args):
        regs = (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
                UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6)
        self.u.reg_write(UC_ARM64_REG_SP, STACK + 0xf000)
        self.u.reg_write(UC_ARM64_REG_LR, STOP)
        for reg, val in zip(regs, args):
            self.u.reg_write(reg, val)
        self.u.emu_start(self.symbols[symbol], STOP, count=10000)
        assert self.u.reg_read(UC_ARM64_REG_PC) == STOP, "instruction limit"

    def run(self, decrypt, expected, coherent=False):
        mem, submitted = STACK + 0x8000, STACK + 0x8100
        dma = self.dma
        self.u.mem_write(mem, struct.pack("<QQI4xQQ?7x", 0 if coherent else dma,
                                          dma if coherent else 0, dma, 0, 0,
                                          self.scenario == "cancel"))
        self.call("zx_kat", 0, QC, mem, decrypt, submitted)
        assert self.result() == expected, (self.scenario, self.result(), expected)
        assert bool(self.u.mem_read(submitted, 1)[0]) == (expected != -16)

    def result(self):
        return struct.unpack("<i", struct.pack("<I", self.u.reg_read(UC_ARM64_REG_X0) & 0xffffffff))[0]


def check_init(elf):
    preamble = [(SEC + 0x450, 0x05000000), (BTLL + 0x18, 0x7e9b), (BTLL + 4, 0x08010000)]
    good = Model(elf, False, "ok")
    good.call("zx_init", 0, BTLL, SEC, QC, DDR)
    assert good.result() == 0
    assert good.btll_writes[:3] == preamble
    assert (0, 2) in good.writes and (0x1c0, DDR >> 16) in good.writes
    assert all(off not in (QUEUES[0][2], QUEUES[1][2]) for off, _ in good.writes)

    failed = Model(elf, False, "unlatched")
    failed.call("zx_init", 0, BTLL, SEC, QC, DDR)
    assert failed.result() == -5
    assert failed.btll_writes == preamble and not failed.writes
    print("BTLL control readback and refusal to configure queues on mismatch: OK")


def check(elf, ko):
    for decrypt in (False, True):
        vendor = trace.Trace(ko).request(decrypt)
        _, request, submit, complete, credit = QUEUES[decrypt]
        submission = [(request, 1), (submit, DDR)]
        cases = (("ok", 0), ("stale", -16), ("pending", -16), ("timeout", -110), ("cancel", -125),
                 ("count", -5), ("address", -5), ("data", -74))
        for scenario, ret in cases:
            m = Model(elf, decrypt, scenario)
            m.run(decrypt, ret)
            no_submit = scenario in ("stale", "pending")
            writes = [] if no_submit else list(submission)
            if scenario in ("ok", "data"):
                writes.append((credit, 1))
            assert m.writes == writes, (scenario, m.writes)
            assert (complete in m.reads) == (scenario in ("ok", "address", "data"))
            if no_submit:
                assert m.u.mem_read(DDR, 128) == b"\xa5" * 128
            if scenario == "ok":
                for name, off in (("pd", 0), ("khb", 0x40), ("key_context", 0x80000),
                                  ("dynamic_context", 0x120000)):
                    blob = (DECRYPT_SEED.ljust(32, b"\0") if decrypt and name == "key_context"
                            else bytes.fromhex(vendor[name]))
                    assert m.u.mem_read(DDR + off, len(blob)) == blob, name
                assert m.u.mem_read(DDR + 0x1f0000, 16) == (CIPHER if decrypt else CLEAR)
            print(f"{vendor['direction']}: {scenario}: OK")

        dma = 0x9a300000
        m = Model(elf, decrypt, "ok", dma=dma)
        m.call("zx_init", 0, BTLL, SEC, QC, dma)
        assert m.result() == 0 and (0x1c0, dma >> 16) in m.writes
        m.writes.clear()
        m.run(decrypt, 0, coherent=True)
        assert m.writes == [(request, 1), (submit, dma), (credit, 1)]
        assert struct.unpack(">I", m.u.mem_read(dma + 0x24, 4))[0] == dma + 0x40
        for field, off in ((0x08, 0x1f0000), (0x30, 0x80000), (0x34, 0x120000), (0x38, 0x1f0040)):
            assert struct.unpack(">I", m.u.mem_read(dma + 0x40 + field, 4))[0] == dma + off
        print(f"{vendor['direction']}: relocated coherent arena: OK")


def check_encoder_matrix(elf, ko):
    model = Model(elf, False, "ok")
    pd, khb, desc = STACK, STACK + 0x40, STACK + 0x80
    for cases, vendor in enumerate(trace.request_matrix(ko), start=1):
        addr = vendor["addresses"]
        model.u.mem_write(pd, b"\xff" * 128)
        model.u.mem_write(desc, struct.pack("<5I", addr["khb"], addr["key_context"],
                                            addr["dynamic_context"], DDR + 0x1f0000, DDR + 0x1f0040))
        model.call("zx_pack", pd, khb, vendor["direction"] == "decrypt", vendor["opcode"],
                   vendor["key_bytes"], vendor["length"], desc)
        for name, at in (("pd", pd), ("khb", khb)):
            assert model.u.mem_read(at, 64).hex() == vendor[name], (name, vendor)
        iv = bytes(range(0xa0, 0xb0)) if vendor["mode"] != "ecb" else b""
        key = bytes(range(vendor["key_bytes"]))
        assert bytes.fromhex(vendor["dynamic_context"]) == iv.ljust(32, b"\0")
        assert bytes.fromhex(vendor["key_context"]) == key.ljust(32, b"\0")
    print(f"{cases} synchronous and asynchronous AES descriptor comparisons: OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, type=Path, help="prepared tree with board overlays")
    parser.add_argument("--gen", required=True, type=Path, help="headers generated by kcheck")
    parser.add_argument("--source", type=Path, help="test an overlay without modifying the kernel tree")
    parser.add_argument("--clang", help="override kcheck's clang discovery")
    parser.add_argument("module", type=Path, help="factory np_133.ko")
    args = parser.parse_args()
    ko = Ko(args.module)
    if ko.machine != 183 or hashlib.sha256(ko.d).hexdigest() != trace.FACTORY_SHA256:
        parser.error("unexpected vendor module")
    with tempfile.TemporaryDirectory(prefix="zx-ipsec-kat-") as scratch:
        elf = compile_kat(args, Path(scratch))
        check_init(elf)
        check(elf, ko)
        check_encoder_matrix(elf, ko)


if __name__ == "__main__":
    main()
