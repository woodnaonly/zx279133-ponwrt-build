#!/usr/bin/env python3
"""Observe a natural power-on and inspect the real U-Boot over an exclusive UART.

This initial stage cannot load, execute, erase, reboot or change environments.
It stops autoboot ONLY after a new boot marker. Binary logs and reports are
exclusive-created; a disconnect never causes old commands to be replayed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.uart import Console, TrialError, open_serial, parse_md
from host.btl import inspect_boot_selectors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--wait-seconds", type=int, default=1260)
    parser.add_argument("--output-dir", type=Path, required=True, help="NEW private report directory")
    parser.add_argument("--previous-inspection", type=Path,
                        help="read-only reinspection after our clean console close; NOT a resume permission for RAM changes")
    parser.add_argument("--next-natural-after", type=Path,
                        help="previous natural-boot inspection; ignore early reset banners until its next 1230s cycle")
    parser.add_argument("--dump-pinned-blocks", action="store_true", help="read code/command bytes from measured relocated U-Boot RAM")
    parser.add_argument("--dump-runtime-structures", action="store_true", help="also read the live legacy Ethernet descriptor and callback code")
    parser.add_argument("--dump-native-spi-chain", action="store_true", help="read live SPI controller callback/private structures and control DTB; requires runtime structures")
    parser.add_argument("--dump-boot-selectors", action="store_true", help="read only the BTL boot-mode/security status registers; never changes them")
    args = parser.parse_args()
    if args.previous_inspection and args.next_natural_after:
        parser.error("reinspection and a new natural-cycle wait are mutually exclusive")
    if args.dump_native_spi_chain and not args.dump_runtime_structures:
        parser.error("--dump-native-spi-chain requires --dump-runtime-structures")
    if not 1 <= args.wait_seconds <= 1260:
        parser.error("observation is limited to one 20m30s power cycle plus 30s")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    report = {"schema": "sk-d840n-ram-trial-v1", "port": args.port, "baud": 115200,
              "utc": datetime.now(timezone.utc).isoformat(), "flashable_release": False,
              "native_write_enabled": False, "ram_execution_verified": False,
              "webpage_verified": False, "nand_readonly_verified": False,
              "two_power_cycles_verified": False, "events": []}
    port = None
    status = 1
    started = time.monotonic()
    with (args.output_dir / "events.jsonl").open("x", encoding="utf-8", newline="\n") as events, \
            (args.output_dir / "rx.bin").open("xb") as raw:
        def emit(event, **fields):
            value = {"event": event, "elapsed": round(time.monotonic() - started, 3), **fields}
            report["events"].append(value)
            line = json.dumps(value, ensure_ascii=False)
            events.write(line + "\n")
            events.flush()
            print(line, flush=True)

        def log(data):
            raw.write(data)
            raw.flush()

        try:
            port = open_serial(args.port)
            emit("serial_open", dtr=False, rts=False, queued_commands=False)
            console = Console(port, emit=emit, raw=log)
            if args.previous_inspection:
                old = json.loads(args.previous_inspection.read_text(encoding="utf-8"))
                boot = next(e for e in old["events"] if e["event"] == "boot_detected")
                if not old.get("inspection_pass") or old["events"][-1]["event"] != "inspection_finished":
                    raise TrialError("previous session did not end at a clean inspected prompt")
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(old["utc"])).total_seconds() - boot["elapsed"] + 5
                if not 0 <= age < 15 * 60:
                    raise TrialError("prior boot evidence is expired; wait for another natural cycle")
                # Conservatively retain the EARLIER boot deadline. Fresh version,
                # help and bdinfo follow; this mode has NO mutation operations.
                console.window.boot()
                console.window.started -= age
                console.prompt = True
                emit("readonly_reinspection", prior_boot_age_seconds=round(age, 3), mutations_allowed=False)
            else:
                not_before = None
                if args.next_natural_after:
                    old = json.loads(args.next_natural_after.read_text(encoding="utf-8"))
                    boot = next(e for e in old["events"] if e["event"] == "boot_detected")
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(old["utc"])).total_seconds() - boot["elapsed"]
                    anchored = old.get("inspection_pass") or any(e["event"] == "natural_cycle_guard" for e in old["events"])
                    if not anchored or not 0 <= age < 1230:
                        raise TrialError("prior natural-cycle anchor is invalid or expired")
                    not_before = time.monotonic() + max(0, 1230 - age - 3)
                    emit("natural_cycle_guard", seconds_until_earliest_boot=round(max(0, not_before-time.monotonic()), 3),
                         early_reset_banners_ignored=True)
                console.wait_boot_prompt(args.wait_seconds, not_before=not_before)
            _, _, board = console.identify()
            if args.dump_boot_selectors:
                report["btl_status"] = inspect_boot_selectors(console)
                emit("btl_status_observed", **report["btl_status"])
            for command in ("help loadx", "help md", "help mw", "help crc32", "icache", "dcache",
                            "printenv bootcmd bootargs loadaddr ipaddr serverip"):
                result = console.inspect(command)
                emit("inspection_result", command=command, result=result)
            if args.dump_pinned_blocks or args.dump_runtime_structures:
                reloc = re.search(r"relocaddr\s*=\s*(0x[0-9a-fA-F]+)", board)
                delta = re.search(r"reloc off\s*=\s*(0x[0-9a-fA-F]+)", board)
                tlb = re.search(r"TLB addr\s*=\s*(0x[0-9a-fA-F]+)", board)
                if not reloc or not delta or not tlb or int(reloc[1],16) - int(delta[1],16) != 0x88000000:
                    raise TrialError("inconsistent runtime relocation evidence")
                base = int(reloc[1],16)
                blocks = {"alias_record": (base + 0x96c50, 56), "help_record": (base + 0x965f8, 56),
                          "command_wrapper": (base + 0x12c7c, 64), "printf": (base + 0x6677c, 128),
                          "icache_command": (base + 0x28d0, 160), "dcache_command": (base + 0x296c, 160),
                          "timer": (base + 0x65630, 128), "eth_current": (base + 0xb1938, 8),
                          "rx_callback_slot": (base + 0xad520, 8), "nand_device_slot": (base + 0xac580,8),
                          "mtd_read_ecc": (base + 0x18d34,256), "mtd_bad_block": (base + 0x192c0,64),
                          "translation_root": (int(tlb[1],16),4096)}
                report["memory_blocks"] = {}
                for name, (address, length) in blocks.items():
                    if not base <= address < address + length <= 0xa0000000:
                        raise TrialError("read would leave measured resident U-Boot reservation")
                    text = console.inspect(f"md.b {address:x} {length:x}", timeout=15)
                    data = parse_md(text, address, length)
                    report["memory_blocks"][name] = {"address":hex(address),"bytes":length,
                                                        "hex":data.hex(),"sha256":hashlib.sha256(data).hexdigest()}
                    emit("memory_read", name=name, address=hex(address), bytes=length,
                         sha256=hashlib.sha256(data).hexdigest())
                if args.dump_runtime_structures:
                    device=struct.unpack("<Q",bytes.fromhex(report["memory_blocks"]["eth_current"]["hex"]))[0]
                    if not 0x9f6ee080<=device<device+128<=0xa0000000:
                        raise TrialError("Ethernet device is outside measured U-Boot/heap reservation")
                    data=parse_md(console.inspect(f"md.b {device:x} 80"),device,128)
                    report["memory_blocks"]["eth_device"]={"address":hex(device),"bytes":128,"hex":data.hex(),
                                                               "sha256":hashlib.sha256(data).hexdigest()}
                    emit("ethernet_descriptor",address=hex(device),hex=data.hex())
                    for name,offset in (("eth_init_callback",48),("eth_send_callback",56),("eth_recv_callback",64),("eth_halt_callback",72)):
                        address=struct.unpack_from("<Q",data,offset)[0]
                        if not base<=address<address+512<=base+0x90000:raise TrialError("invalid live Ethernet callback pointer")
                        code=parse_md(console.inspect(f"md.b {address:x} 200"),address,512)
                        report["memory_blocks"][name]={"address":hex(address),"bytes":512,"hex":code.hex(),
                                                          "sha256":hashlib.sha256(code).hexdigest()}
                        emit("ethernet_callback",name=name,address=hex(address),sha256=hashlib.sha256(code).hexdigest())
                    mtd=struct.unpack("<Q",bytes.fromhex(report["memory_blocks"]["nand_device_slot"]["hex"]))[0]
                    if not 0x9f6ee080<=mtd<mtd+512<=0xa0000000:
                        raise TrialError("MTD device is outside measured monitor/heap reservation")
                    data=parse_md(console.inspect(f"md.b {mtd:x} 200"),mtd,512)
                    report["memory_blocks"]["mtd_device"]={"address":hex(mtd),"bytes":512,"hex":data.hex(),
                                                               "sha256":hashlib.sha256(data).hexdigest()}
                    emit("mtd_descriptor",address=hex(mtd),hex=data.hex())
                    if args.dump_native_spi_chain:
                        def read_structure(name,address,length):
                            if not 0x9f6ee080<=address<address+length<=0xa0000000:
                                raise TrialError("native structure leaves measured monitor reservation: "+name)
                            data=parse_md(console.inspect(f"md.b {address:x} {length:x}",timeout=45),address,length)
                            report["memory_blocks"][name]={"address":hex(address),"bytes":length,"hex":data.hex(),"sha256":hashlib.sha256(data).hexdigest()}
                            emit("native_structure",name=name,address=hex(address),bytes=length);return data
                        def ptr_at(data,off):return struct.unpack_from("<Q",data,off)[0]
                        nand=read_structure("spi_nand",ptr_at(data,312),256)
                        mem=read_structure("spi_mem",ptr_at(nand,80),64)
                        dev=read_structure("spi_device",ptr_at(mem,0),192)
                        parent=read_structure("spi_parent",ptr_at(dev,56),192)
                        read_structure("spi_private",ptr_at(parent,64),0xa00)
                        driver=read_structure("spi_driver",ptr_at(parent,0),128)
                        ops=read_structure("spi_ops",ptr_at(driver,104),64)
                        read_structure("spi_mem_ops",ptr_at(ops,32),24)
                        fdt=re.search(r"fdt_blob\s*=\s*(0x[0-9a-fA-F]+)",board)
                        if not fdt:raise TrialError("missing live control DTB pointer")
                        fdt_addr=int(fdt[1],16);head=read_structure("control_dtb_header",fdt_addr,40)
                        magic,length=struct.unpack_from(">II",head)
                        if magic!=0xd00dfeed or not 40<=length<=0x10000:raise TrialError("invalid live control DTB")
                        control=read_structure("control_dtb",fdt_addr,length)
                        (args.output_dir/"control.dtb").write_bytes(control)
            report["inspection_pass"] = True
            report["remaining_service_seconds"] = console.window.remaining()
            emit("inspection_finished", note="No RAM payload or NAND modification was attempted")
            status = 0
        except (OSError, TrialError, KeyboardInterrupt) as error:
            report["error"] = str(error) or type(error).__name__
            emit("trial_stopped", reason=report["error"], automatic_retry=False)
        finally:
            if port is not None:
                port.close()
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            with (args.output_dir / "report.json").open("x", encoding="utf-8", newline="\n") as target:
                json.dump(report, target, ensure_ascii=False, indent=2)
                target.write("\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
