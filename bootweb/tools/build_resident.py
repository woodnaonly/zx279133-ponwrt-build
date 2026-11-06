#!/usr/bin/env python3
"""Build an independently copied resident dispatcher for RAM trials ONLY.

The output is deliberately labelled development/non-flashable. It does not
claim GPIO, RNG, cold boot, writable ECC or installation-time acceptance.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from audit_ram_elf import audit_elf, verify_payload
from build import NATIVE_FLAGS, RAM_PINS
from ram_probe import relocated_record
from host.resident import patch_resident_development,add_resident_descriptor
from host.core import BASE,Core
from host.reset import physical_reset_evidence,unverified_reset_mapping,PINCTRL_CODE_PINS,CONTROL_DTB_SHA256
from ci_profile import load_ci_profile


def byte_array(name, data):
    return "_Alignas(64) __attribute__((used)) static const unsigned char "+name+"[]={"+",".join(map(str,data))+"};\n"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-dir",type=Path,required=True)
    parser.add_argument("--build-dir",type=Path,required=True)
    parser.add_argument("--device-entropy-trial",action="store_true",help="independently build -O0 Jitterentropy and use the actual device-generated session token")
    parser.add_argument("--resident-format",action="store_true",help="add independently checkable resident tail/ABI metadata; does not enable NAND writes or certify this build")
    parser.add_argument("--native-service-trial",action="store_true",help="use the distinct native callback payload with its timing gate still locked; NOT installation acceptance")
    parser.add_argument("--native241-service",action="store_true",help="use the strict native241 continuous-power system Web payload; no hardware acceptance is implied")
    parser.add_argument("--reset-evidence",type=Path,help="physical press/release trace for the exact live control DTB; enables the cold Reset path only after recomputation")
    parser.add_argument("--user-directed-unverified-reset",type=Path,help="explicit skipped-acceptance build: use measured 32-bit mux mapping, never claim a passed physical test")
    parser.add_argument("--ci-offline-profile",type=Path,help="fixed static Reset target for public-baseline CI; no hardware acceptance or installation authority")
    parser.add_argument("--boot-dump",type=Path,default=ROOT.parents[1]/"sk-d840n-openwrt/binary/boot.bin")
    parser.add_argument("--vendor-reference",type=Path,default=ROOT.parents[1]/"sk-d840n-openwrt/binary/boot.bin",help="immutable original ABI template; CURRENT protection bytes still come from --boot-dump")
    parser.add_argument("--zig",type=Path,default=ROOT/".build/toolchain/zig-x86_64-windows-0.14.1/zig.exe")
    args=parser.parse_args()
    if args.native_service_trial and args.native241_service:
        parser.error("choose either the locked callback trial or native241 service")
    if sum(bool(v) for v in (args.reset_evidence,args.user_directed_unverified_reset,args.ci_offline_profile))>1:
        parser.error("verified, explicitly unverified and CI-only Reset policies are mutually exclusive")
    reset_evidence=None
    if args.reset_evidence:
        if not args.device_entropy_trial:parser.error("Reset Web entry requires the independent device entropy build")
        try:reset_evidence=physical_reset_evidence(json.loads(args.reset_evidence.read_text(encoding="utf-8")))
        except (OSError,ValueError) as error:parser.error(str(error))
    if args.user_directed_unverified_reset:
        if not args.device_entropy_trial:parser.error("Reset entry still requires the device entropy build")
        try:reset_evidence=unverified_reset_mapping(json.loads(args.user_directed_unverified_reset.read_text(encoding="utf-8")))
        except (OSError,ValueError) as error:parser.error(str(error))
    if args.ci_offline_profile and args.boot_dump.stat().st_size!=0x200000:
        parser.error("CI mode accepts only the 2 MiB public reference, not a device NAND backup")
    dump=args.boot_dump.read_bytes()
    ci_policy={}
    if args.ci_offline_profile:
        if not (args.native241_service and args.resident_format and args.device_entropy_trial):
            parser.error("CI target requires --native241-service --resident-format --device-entropy-trial")
        try:
            ci_policy=load_ci_profile(args.ci_offline_profile,dump)
            load_ci_profile(args.ci_offline_profile,args.vendor_reference.read_bytes())
        except (OSError,ValueError) as error:parser.error(str(error))
        reset_evidence=ci_policy["reset_static_configuration"]
    if len(dump)==0x10000000:dump=dump[:0x200000]
    if len(dump)!=0x200000:parser.error("requires a complete ECC boot or physical NAND dump")
    output=args.build_dir.resolve();output.mkdir(parents=True,exist_ok=False)
    stage2=dump[0x20000:0x180000]
    current_id=Core().resident_identity(stage2)
    native_service=args.native_service_trial or args.native241_service
    name="bootweb-resident-native" if native_service else "bootweb-ram-readonly"
    payload=(args.payload_dir/(name+".bin")).read_bytes()
    elf=(args.payload_dir/(name+".elf")).read_bytes()
    policy=json.loads((args.payload_dir/("resident-native-policy.json" if native_service else "ram-build-policy.json")).read_text(encoding="utf-8"))
    if (bool(policy.get("ci_offline_build"))!=bool(args.ci_offline_profile) or
            (ci_policy and policy.get("ci_profile_sha256")!=ci_policy["ci_profile_sha256"])):
        parser.error("CI payload and resident target profile must match; no hardware evidence substitution")
    if policy.get("payload_sha256")!=hashlib.sha256(payload).hexdigest() or policy.get("elf_sha256")!=hashlib.sha256(elf).hexdigest():
        parser.error("payload/ELF differs from the separate build policy")
    if args.native241_service:
        if not (policy.get("native_resident") is True and policy.get("native_callbacks_compiled") is True and
                policy.get("resident_service_write_capable") is True and policy.get("ram_readonly") is False and
                policy.get("bounded_io_enforced") is True and policy.get("layout")=="native241" and
                policy.get("power_mode")=="continuous_required" and policy.get("web_uboot_enabled") is False and
                policy.get("periodic_budget_verified") is False and policy.get("timing_ledger",{}).get("TIMING_PROVEN")==0):
            parser.error("native241 service requires the distinct continuous-power system-only policy")
        if policy.get("source_installed_sha256")!=current_id.installed_sha256.decode():
            parser.error("native payload policy does not bind this current installed image")
    elif args.native_service_trial:
        if not (policy.get("native_resident") is True and policy.get("native_callbacks_compiled") is True and
                policy.get("native_timing_verified") is False and policy.get("resident_service_write_capable") is False and
                policy.get("ram_readonly") is False and policy.get("timing_ledger",{}).get("TIMING_PROVEN")==0 and
                b"Complete native timing bound has not been verified" in payload):
            parser.error("native callback trial requires its independently locked timing gate")
    elif not all(policy.get(k) is True for k in ("ram_readonly","ram_hardware","resident_bootstrap_trial")) or policy.get("nand_write_enabled") is not False:
        parser.error("payload is not the distinct bootstrap-compatible RAM-only build")
    audit=verify_payload(elf,payload,limit=0x89100000)
    if int(audit["layout_symbols"]["__ram_stack_top"],16)>0x89100000:
        parser.error("payload stack collides with resident dispatcher")
    pins=sorted(set(RAM_PINS+[(0x9084,0xa0),(0xa270,0x84)]))
    if args.native241_service:
        # Measured reset cmd_tbl handler -> reset framework -> hang on failure;
        # timer/delay and command entry are pinned as well as the whole record.
        pins=sorted(set(pins+[(0x183a8,0xc4),(0x656dc,0x50),(0x63d7c,0x20)]))
    if reset_evidence:pins=sorted(set(pins+[(0x4dd24,0x38)]+(list(PINCTRL_CODE_PINS) if reset_evidence.get("pinctrl_configuration") else [])))
    header="struct bw_resident_pin {unsigned offset,length;const char *sha256;};\nstatic const struct bw_resident_pin bw_resident_pins[]={\n"
    for off,size in pins:
        header+="{"+hex(off)+","+str(size)+',"'+hashlib.sha256(stage2[off:off+size]).hexdigest()+'"},\n'
    header+="};\n"+byte_array("bw_resident_payload",payload)
    header+="#define BW_RESIDENT_RESET_VERIFIED "+("1" if args.reset_evidence else "0")+"\n"
    if reset_evidence and reset_evidence.get("pinctrl_configuration"):
        header+="#define BW_RESIDENT_RESET_PINCTRL 1\n"
        header+='#define BW_RESIDENT_CONTROL_DTB_SHA256 "'+CONTROL_DTB_SHA256+'"\n'
    header+='#define BW_RESIDENT_PAYLOAD_SHA256 "'+hashlib.sha256(payload).hexdigest()+'"\n'
    record=struct.unpack("<QIIQQQQQ",relocated_record(stage2[0x96c50:0x96c88],0x17f3c000))
    header+="static const struct command_record bw_original_alias={"+",".join(hex(n)+"ULL" for n in record)+"};\n"
    if args.native241_service:
        reset_record=struct.unpack("<QIIQQQQQ",relocated_record(stage2[0x96c88:0x96cc0],0x17f3c000))
        if reset_record[1]!=1 or reset_record[3]!=0x9ff3c000+0x12c8c or reset_record[4]!=0x9ff3c000+0x18444:
            parser.error("reset command no longer matches the measured four-argument ABI")
        header+="static const struct command_record bw_reset_record={"+",".join(hex(n)+"ULL" for n in reset_record)+"};\n"
    # The record type precedes this generated initializer in the C source.
    (output/"bw_resident_data.h").write_text(header,encoding="utf-8",newline="\n")
    common=[str(args.zig.resolve()),"cc",*NATIVE_FLAGS,"-std=c11","-Os","-Wall","-Wextra","-Werror","-fno-builtin",
            "-I"+str(output),"-I"+str(ROOT/"src"),"-I"+str(ROOT/"include")]
    if native_service:common+=["-DBW_RESIDENT_NATIVE_SERVICE=1"]
    if args.native241_service:common+=["-DBW_RESIDENT_NATIVE241_SERVICE=1"]
    bootstrap_elf=output/"resident-bootstrap.elf"
    entropy=[]
    if args.device_entropy_trial:
        upstream=ROOT/"vendor/jitterentropy"
        expected={"jitterentropy.c":"9c5bd55fb589248b5de5d0cf7d73c66d38eba025b9c4f54527398d5f42bc679c",
                  "jitterentropy.h":"9dcbc0f1b97637ca231f053f07cadbad2f7bf28fa9cd9aa8ec7fb94a1aeec43a"}
        for name,digest in expected.items():
            if hashlib.sha256((upstream/name).read_bytes()).hexdigest()!=digest:
                parser.error("upstream Jitterentropy fingerprint mismatch: "+name)
        obj=output/"jitterentropy-O0.o"
        subprocess.run([*common,"-O0","-Wno-unused-parameter","-Wno-pointer-sign","-I"+str(upstream),"-c",str(upstream/"jitterentropy.c"),"-o",str(obj)],check=True)
        entropy=["-DBW_RESIDENT_DEVICE_ENTROPY=1","-Wno-unused-parameter","-I"+str(upstream),str(ROOT/"src/sha3.c"),str(ROOT/"adapter/jitter_binding.c"),str(obj)]
    reset=["-DBW_RESIDENT_RESET_ENTRY=1",str(ROOT/"src/entry_gate.c")] if reset_evidence else []
    subprocess.run([*common,*entropy,*reset,str(ROOT/"adapter/resident-bootstrap.c"),str(ROOT/"src/hash.c"),
        "-Wl,-T,"+str(ROOT/"adapter/resident-bootstrap.ld"),"-Wl,-e,bw_resident_command","-o",str(bootstrap_elf)],check=True)
    loader=output/"resident-bootstrap.bin"
    subprocess.run([str(args.zig.resolve()),"objcopy","--output-target","binary",str(bootstrap_elf),str(loader)],check=True)
    bootstrap_audit=verify_payload(bootstrap_elf.read_bytes(),loader.read_bytes(),base=0x89100000,limit=0x891e0000)
    blob=loader.read_bytes();blob+=bytes((-len(blob))%64)
    (output/"bw_bootstrap_blob.h").write_text('#define BW_BOOTSTRAP_BYTES_ASM "'+str(len(blob))+'"\n'+byte_array("bw_bootstrap_blob",blob),encoding="utf-8",newline="\n")
    script=output/"resident-stage0.ld"
    script.write_text("ENTRY(bw_resident_stage0)\nSECTIONS { . = 0x88100000; .text : { KEEP(*(.text.entry)) *(.text .text.*) } .rodata : { *(.rodata .rodata.*) } .data : { *(.data .data.*) } /DISCARD/ : { *(.comment) *(.eh_frame*) } }\n",encoding="utf-8",newline="\n")
    stage0_elf=output/"resident-stage0.elf"
    subprocess.run([*common,str(ROOT/"adapter/resident-stage0.c"),"-Wl,-T,"+str(script),"-Wl,-e,bw_resident_stage0","-o",str(stage0_elf)],check=True)
    tail_path=output/"resident-tail-ram-trial.bin"
    stage0_binary=output/"resident-stage0.bin"
    subprocess.run([str(args.zig.resolve()),"objcopy","--output-target","binary",str(stage0_elf),str(stage0_binary)],check=True)
    tail=stage0_binary.read_bytes()
    stage0_audit=verify_payload(stage0_elf.read_bytes(),tail,base=0x88100000,limit=0x88160000)
    tail+=bytes((-len(tail))%64);tail_path.write_bytes(tail)
    patched,manifest=patch_resident_development(dump,tail,vendor_reference=args.vendor_reference.read_bytes())
    if args.resident_format:
        patched=add_resident_descriptor(patched,tail,profile="native241" if args.native241_service else "legacy")
        manifest.update({"resident_format":"BW-RESIDENT-V1", "descriptor_stage2_offset":"0x15ff00",
                         "installed_sha256":hashlib.sha256(patched[0x20000:0x180000]).hexdigest(),
                         "boot_sha256":hashlib.sha256(patched).hexdigest()})
        # Run the SAME C image checker as the native ECC identity path, using
        # the production-base host library built alongside this exact payload.
        checker=C.CDLL(str((args.payload_dir/("bootweb.dll" if sys.platform=="win32" else "bootweb.so")).resolve()))
        checker.bw_test_build.restype=C.c_int
        checker.bw_base_hash.restype=C.c_char_p
        checker.bw_resident_image_check.argtypes=[C.c_void_p,C.c_size_t,C.c_void_p]
        digest=C.create_string_buffer(65);image=patched[0x20000:0x180000]
        if checker.bw_test_build()!=0 or checker.bw_base_hash().decode()!=BASE or checker.bw_resident_image_check(image,len(image),digest)!=1 or digest.value.decode()!=manifest["installed_sha256"]:
            parser.error("production native resident image-format/ABI checker rejected this candidate")
        manifest["native_image_format_offline_pass"]=True
    (output/"resident-boot-development.bin").write_bytes(patched)
    (output/"resident-stage2-development.bin").write_bytes(patched[0x20000:0x180000])
    manifest.update({"payload_audit":audit,"bootstrap_audit":bootstrap_audit,"stage0_audit":stage0_audit,
                     "payload_build_policy":policy,
                     "tail_bytes":len(tail),"tail_sha256":hashlib.sha256(tail).hexdigest(),
                     "runtime_dispatcher":"0x89100000","runtime_stack":["0x891f0000","0x89200000"],
                     "initial_stage0":"0x88100000","payload_address":"0x89000000",
                     "device_entropy_trial":args.device_entropy_trial,
                     "native_service_trial":args.native_service_trial,"native241_service":args.native241_service,
                     "native_callbacks_compiled":native_service,
                     "resident_service_write_capable":args.native241_service,
                     "layout":"native241" if args.native241_service else "current240",
                     "power_mode":"continuous_required" if args.native241_service else "unchanged",
                     "reset_physical_evidence":reset_evidence if args.reset_evidence else None,
                     "user_directed_unverified_reset":reset_evidence if args.user_directed_unverified_reset else None,
                     "reset_cold_entry_enabled":reset_evidence is not None,
                     "entropy_source":"Jitterentropy-3.4.0/CNTPCT_EL0/SHA3-256" if args.device_entropy_trial else "host"})
    manifest.update(ci_policy)
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"development_build":True,"bytes":len(tail),"sha256":manifest["tail_sha256"],"nand_written":False,"flashable_release":False}))


if __name__=="__main__":main()
