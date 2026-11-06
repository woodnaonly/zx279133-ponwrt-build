#!/usr/bin/env python3
"""Compile the distinct native resident service; never contact COM3 or NAND.

Native callbacks are real but remain blocked by the independent runtime
backup, identity, confirmation and timing gates. No option fabricates timing
evidence or turns this development artifact into an accepted installation.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from audit_ram_elf import verify_payload
from build import NATIVE_FLAGS, SOURCES, RAM_PINS, SPI_PINS, NAND_WRITE_PINS, lwip_sources

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from host.core import Core,BASE
from ci_profile import load_ci_profile


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--build-dir",type=Path,required=True)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument("--current-backup",type=Path,help="fresh complete ECC backup; freshness must still be established by the installation owner")
    source.add_argument("--reference-boot",type=Path,help="OFFLINE-ONLY 2 MiB artifact reference; never substitutes for the fresh pre-install backup")
    entry=p.add_mutually_exclusive_group(required=True)
    entry.add_argument("--probe-report",type=Path)
    entry.add_argument("--ci-offline-profile",type=Path,help="fixed public-baseline compile-only target; never RAM evidence or install authority")
    p.add_argument("--native241-continuous",action="store_true",help="distinct strict native241 system Web target; requires explicit continuous-power confirmation at runtime")
    p.add_argument("--zig",type=Path,default=ROOT/".build/toolchain/zig-x86_64-windows-0.14.1/zig.exe")
    p.add_argument("--lwip-dir",type=Path,default=ROOT.parents[1]/"uboot-an758x/lib/lwip/lwip/src")
    a=p.parse_args()
    try:
        if a.ci_offline_profile and (a.current_backup or not a.native241_continuous):
            raise ValueError("CI mode requires --reference-boot --native241-continuous; no device backups")
        if not a.ci_offline_profile:
            probe=json.loads(a.probe_report.read_text(encoding="utf-8"))
            if not all(probe.get(k) is True for k in ("ram_probe_pass","alias_restored","cache_restored")):
                raise ValueError("verified RAM entry/ABI/cache restoration evidence is missing")
        data=(a.current_backup or a.reference_boot).read_bytes()
        if len(data)!=(0x10000000 if a.current_backup else 0x200000):
            raise ValueError("wrong length for the explicit backup/reference input mode")
        ci_policy={}
        if a.ci_offline_profile:
            ci_policy=load_ci_profile(a.ci_offline_profile,data)
        stage2=data[0x20000:0x180000]
        current_sha=hashlib.sha256(stage2).hexdigest()
        checker=Core().lib
        checker.bw_resident_image_check.argtypes=[C.c_void_p,C.c_size_t,C.c_void_p]
        digest=C.create_string_buffer(65)
        if checker.bw_resident_image_check(stage2,len(stage2),digest)<0 or digest.value.decode()!=current_sha:
            raise ValueError("unknown current vendor ABI/resident patch format")
        output=a.build_dir.resolve();output.mkdir(parents=True,exist_ok=False)
        for name,kind,items in (("bw_ram_pins.h","bw_pin",RAM_PINS),
                               ("bw_spi_pins.h","bw_spi_pin",SPI_PINS+NAND_WRITE_PINS)):
            header="static const struct "+kind+" "+("bw_ram_pins" if kind=="bw_pin" else "bw_spi_pins")+"[]={\n"
            for off,n in items:header+="{"+hex(off)+","+str(n)+',"'+hashlib.sha256(stage2[off:off+n]).hexdigest()+'"},\n'
            (output/name).write_text(header+"};\n",encoding="utf-8",newline="\n")
        # Conservative development ledger, explicitly NOT an upper-bound
        # certificate. Real Web writes are denied while PROVEN is zero.
        timing={"TIMING_PROVEN":0,"ERASE_MAX_MS":2100,"PAGE_MAX_MS":300,"READ_MAX_MS":3000,
                "HASH_MAX_MS":500,"BAD_MAX_MS":100,"SERVICE_MAX_MS":1000,"FIXED_MAX_MS":180000}
        (output/"bw_native_timing.h").write_text("".join("#define BW_NATIVE_"+key+" "+str(value)+"u\n" for key,value in timing.items()),encoding="utf-8",newline="\n")
        html=(ROOT/"web/index.html").read_bytes()
        if len(html)>32768:raise ValueError("single page exceeds its fixed bound")
        (output/"web.inc").write_text("static const unsigned char bw_page[]={"+",".join(map(str,html))+"};\n",encoding="utf-8",newline="\n")
        script=output/"resident-native.ld"
        script.write_text("BW_RAM_BASE = 0x89000000;\n"+(ROOT/"adapter/ram-readonly.ld").read_text(encoding="utf-8"),encoding="utf-8",newline="\n")
        lwip=a.lwip_dir.resolve();network=lwip_sources(lwip)
        if not all(path.is_file() for path in network):raise ValueError("complete lwIP source is required")
        flags=["-std=c11","-O2","-Wall","-Wextra","-Werror","-fno-builtin","-I"+str(ROOT/"include"),
               "-I"+str(ROOT/"src"),"-I"+str(output)]
        elf=output/"bootweb-resident-native.elf"
        subprocess.run([str(a.zig.resolve()),"cc",*flags,*NATIVE_FLAGS,"-DBW_RESIDENT_NATIVE_WRITE=1","-DBW_NATIVE_INSTALL=1",
            "-DBW_NATIVE_CONTINUOUS_POWER="+str(int(a.native241_continuous)),
            "-DBW_NATIVE_BOUNDED_SPI=1","-I"+str(ROOT/"adapter/lwip"),"-I"+str(lwip/"include"),
            *map(str,network),*map(str,SOURCES),str(ROOT/"src/service.c"),str(ROOT/"adapter/entry.c"),
            str(ROOT/"adapter/vendor_resident.c"),str(ROOT/"adapter/vendor_nand.c"),str(ROOT/"adapter/vendor_bounded_spi.c"),
            "-Wl,-T,"+str(script),"-Wl,-e,bootweb_command","-o",str(elf)],check=True)
        binary=output/"bootweb-resident-native.bin"
        subprocess.run([str(a.zig.resolve()),"objcopy","--output-target","binary",str(elf),str(binary)],check=True)
        audit=verify_payload(elf.read_bytes(),binary.read_bytes(),limit=0x89100000)
        # The identical production host image checker accompanies this target.
        suffix=".dll" if sys.platform=="win32" else ".so"
        subprocess.run([str(a.zig.resolve()),"cc",*flags,*map(str,SOURCES),"-shared",
                        *([] if sys.platform=="win32" else ["-fPIC"]),"-o",str(output/("bootweb"+suffix))],check=True)
        policy={"schema":"sk-d840n-native-resident-build-v1","native_resident":True,"native_callbacks_compiled":True,
                "native_timing_verified":False,"resident_service_write_capable":a.native241_continuous,"flashable_release":False,
                "nand_written":False,"ram_readonly":False,"resident_bootstrap_trial":True,
                "payload_sha256":hashlib.sha256(binary.read_bytes()).hexdigest(),"elf_sha256":hashlib.sha256(elf.read_bytes()).hexdigest(),
                "instruction_audit":audit,"snapshot_ram":["0x89400000","0x89a00000"],"installed_identity_ram":["0x89200000","0x89360000"],
                "native_wrapper_deadlines_ms":{"read":3000,"bad":100,"erase":2100,"page":300},
                "timing_ledger":timing,"future_boot_update_waiver_inherited":False,
                "layout":"native241","power_mode":"continuous_required" if a.native241_continuous else "periodic20m",
                "default_service_timeout_ms":1800000 if a.native241_continuous else 180000,
                "accepted_task_preparation_ignores_idle_deadline":a.native241_continuous,
                "bounded_io_enforced":True,"periodic_budget_verified":False,"web_uboot_enabled":False,
                "vendor_abi_sha256":BASE,"source_installed_sha256":current_sha,
                "build_input_kind":"current-ecc-backup" if a.current_backup else "offline-boot-reference",
                "fresh_preinstall_backup_still_required":True,
                "bounded_source_hashes":{str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in
                    (ROOT/"adapter/vendor_resident.c",ROOT/"adapter/vendor_nand.c",ROOT/"adapter/vendor_bounded_spi.c",
                     ROOT/"src/bounded_spi.c",ROOT/"src/budget.c",ROOT/"src/flash.c",ROOT/"src/resident_image.c",
                     ROOT/"src/install_image.c",ROOT/"src/nand_gate.c",ROOT/"src/service.c",ROOT/"adapter/lwip/bridge.c")}}
        policy.update(ci_policy)
        (output/"resident-native-policy.json").write_text(json.dumps(policy,indent=2)+"\n",encoding="utf-8")
        print(json.dumps({"native_payload_bytes":binary.stat().st_size,"sha256":policy["payload_sha256"],
                          "write_gate":"runtime-backup-identity-continuous-confirmation" if a.native241_continuous else "timing-unverified","nand_written":False}))
    except (OSError,ValueError,subprocess.SubprocessError) as error:p.exit(1,"native resident build: "+str(error)+"\n")


if __name__=="__main__":main()
