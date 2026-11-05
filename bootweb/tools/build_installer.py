#!/usr/bin/env python3
"""Build a distinct pinned RAM NAND installer/preflight; never contact a device.

Preflight is the default and cannot transmit mutating SPI commands. Real mode
requires exact-candidate acceptance evidence; missing gates fail before output.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from build import NATIVE_FLAGS,RAM_PINS,SPI_PINS,NAND_WRITE_PINS,SOURCES
from build_resident import byte_array
from audit_ram_elf import verify_payload
from host.core import Core,BASE
from host.resident import DESCRIPTOR_OFFSET,SLOT_OFFSET
from host.reset import physical_reset_evidence
from host.resident_runtime import command_record
from package import make_uboot

def sha(data):return hashlib.sha256(data).hexdigest()
def read_json(path):return json.loads(Path(path).read_text(encoding="utf-8"))

def native241_request_evidence(manifest,digest,request):
    """A NEW exact-candidate operation under the native241/#337 request.

    Does not reuse the earlier consumed skip-tests authorization, nor claim
    rescue, physical Reset, RAM cycles or cold-boot acceptance already passed.
    Continuous power is separately confirmed at execution, not in this file.
    """
    policy=manifest.get("payload_build_policy",{})
    if (request.get("schema")!="sk-d840n-native241-install-request-v1" or
            request.get("operation")!="update-resident-native241-then-web-install-337" or
            request.get("resident_manifest_sha256")!=digest or
            request.get("candidate_sha256")!=manifest.get("installed_sha256") or
            request.get("source_installed_sha256")!=manifest.get("source_installed_sha256") or
            request.get("permitted_physical_write")!=["0x20000","0x180000"] or
            request.get("continuous_power_required") is not True or
            request.get("web_uboot_enabled") is not False or
            request.get("system_package_sha256")!="f92c1ff53d8cf7c64eabc97122eeda316228d787b0aa448638670df88603cca2"):
        raise ValueError("new native241 request does not bind this exact current/candidate/#337 operation")
    if (manifest.get("native241_service") is not True or manifest.get("native_image_format_offline_pass") is not True or
            manifest.get("static_space_pass") is not True or manifest.get("reset_cold_entry_enabled") is not True or
            manifest.get("device_entropy_trial") is not True or policy.get("bounded_io_enforced") is not True or
            policy.get("layout")!="native241" or policy.get("power_mode")!="continuous_required" or
            policy.get("web_uboot_enabled") is not False or policy.get("timing_ledger",{}).get("TIMING_PROVEN")!=0):
        raise ValueError("native241 candidate lacks the bounded, continuous-power system-only implementation")
    return {"native241_requested_installation":True,"hardware_acceptance_complete":False,
            "rescue_verified":False,"rescue_test_not_requested":True,"prior_authorization_reused":False,
            "power_confirmation_at_execution_required":True,"native_io_bounds_required":True,
            "request_sha256":sha(json.dumps(request,sort_keys=True,separators=(",",":")).encode())}

def user_directed_evidence(manifest,digest,policy):
    """Explicit unaccepted installation, never converted to acceptance facts.

    Current backup, exact ABI, container, RAM placement, fixed NAND targets,
    one-use authorization and native runtime time admission remain mandatory.
    The installed Web's independent native update lock remains unchanged.
    """
    if (policy.get("schema")!="sk-d840n-user-directed-install-v1" or
            policy.get("human_instruction")!="你先打包固件 刷入啊 不要测试了" or
            policy.get("skip_preinstallation_acceptance") is not True or
            policy.get("resident_manifest_sha256")!=digest or
            policy.get("candidate_sha256")!=manifest.get("installed_sha256") or
            policy.get("permitted_physical_write")!=["0x20000","0x180000"]):
        raise ValueError("explicit user-directed exact-image policy missing or changed")
    native=manifest.get("payload_build_policy",{})
    if (manifest.get("native_image_format_offline_pass") is not True or
            manifest.get("static_space_pass") is not True or
            manifest.get("native_callbacks_compiled") is not True or
            manifest.get("reset_cold_entry_enabled") is not True or
            native.get("native_callbacks_compiled") is not True or
            native.get("native_timing_verified") is not False or
            native.get("resident_service_write_capable") is not False or
            native.get("timing_ledger",{}).get("TIMING_PROVEN")!=0):
        raise ValueError("requires the cold-integrated native image with its Web write lock retained")
    binding=policy.get("existing_native_binding",{})
    path=Path(binding.get("path",""));raw=path.read_bytes()
    if sha(raw)!=binding.get("sha256"):raise ValueError("existing native binding evidence changed")
    report=json.loads(raw)
    if (report.get("resident_native_locked_trial_pass") is not True or
            report.get("native_wrapper_deadline_binding_observed") is not True or
            report.get("nand_unchanged_pass") is not True or
            ("payload_sha256="+native.get("payload_sha256","missing")) not in report.get("native_console","")):
        raise ValueError("this exact native payload has no existing ABI/IO-deadline/restoration record")
    return {"user_directed_unaccepted_installation":True,"acceptance_verified":False,
            "rescue_waived":True,"rescue_verified":False,"web_native_update_gate_locked":True,
            "whole_install_timing_certified":False,"runtime_budget_admission_retained":True,
            "waived_acceptance":["exact-final RAM cycles","physical Reset hold/release",
                                 "exact-final cold boot","final installer preflight"],
            "policy_sha256":sha(json.dumps(policy,sort_keys=True,separators=(",",":")).encode())}

def real_evidence(manifest,digest,ram_reports,cold,reset,preflight,timing):
    """No rescue evidence is requested or fabricated by this one-install waiver."""
    if manifest.get("resident_service_write_capable") is not True:
        raise ValueError("resident Web native update adapter is not accepted; this is not the final installable build")
    if len(ram_reports)<2:raise ValueError("two exact-build natural-cycle RAM acceptance reports are required")
    cycles=set()
    for report in ram_reports:
        if report.get("resident_ram_trial_pass") is not True or report.get("build_manifest_sha256")!=digest:
            raise ValueError("RAM acceptance does not bind this exact final resident build")
        cycles.add(report.get("natural_boot_utc"))
    if None in cycles or len(cycles)<2:raise ValueError("RAM acceptance is not bound to distinct observed natural power cycles")
    if not cold or cold.get("normal_openwrt_boot_pass") is not True or cold.get("build_manifest_sha256")!=digest:
        raise ValueError("exact final resident normal-boot acceptance is missing")
    if not reset:raise ValueError("physical Reset press/release/polarity acceptance is missing")
    physical_reset_evidence(reset)
    if not preflight or preflight.get("installer_preflight_pass") is not True or preflight.get("candidate_sha256")!=manifest["installed_sha256"]:
        raise ValueError("exact candidate native installer preflight is missing")
    if not timing or timing.get("bounded_native_nand_pass") is not True or timing.get("candidate_sha256")!=manifest["installed_sha256"]:
        raise ValueError("complete bounded native NAND timing proof is missing")
    if cold.get("reset_web_entry_pass") is not True or cold.get("serial_bootweb_entry_pass") is not True:
        raise ValueError("exact final Reset Web / serial bootweb acceptance is missing")
    return {"ram_cycles":sorted(cycles),"rescue_waived":True,"rescue_verified":False}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("resident-dir","current-backup","backup-audit","config-report","build-dir"):
        p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--mode",choices=("preflight","install"),default="preflight")
    p.add_argument("--user-directed-install-policy",type=Path,help="explicit exact-candidate user request to install without acceptance; does not unlock the resident Web write gate")
    p.add_argument("--native241-install-request",type=Path,help="NEW exact-current/candidate native241/#337 operation; prior one-use policies are not accepted")
    p.add_argument("--native241-continuous",action="store_true",help="separate continuous-power installer; still requires bounded IO and explicit execution acknowledgement")
    p.add_argument("--current-resident-dir",type=Path,help="exact previously installed ELF/manifest, required for a patched current command record")
    p.add_argument("--ram-acceptance",type=Path,action="append",default=[])
    for name in ("cold-acceptance","reset-evidence","installer-preflight","timing-evidence"):
        p.add_argument("--"+name,type=Path)
    p.add_argument("--zig",type=Path,default=ROOT/".build/toolchain/zig-x86_64-windows-0.14.1/zig.exe")
    a=p.parse_args()
    try:
        manifest_file=a.resident_dir/"manifest.json";manifest=read_json(manifest_file);manifest_digest=sha(manifest_file.read_bytes())
        if manifest.get("native_image_format_offline_pass") is not True:
            raise ValueError("the production C resident image-format checker has not accepted this build")
        backup=a.current_backup.read_bytes();audit=read_json(a.backup_audit)
        if len(backup)!=0x10000000 or audit.get("snapshot_audit_pass") is not True or audit.get("oob_included") is not False or audit.get("sha256")!=sha(backup) or audit.get("bad_blocks")!=[] or audit.get("btl_matches_known_baseline") is not True or audit.get("vendor_abi_matches_known_baseline") is not True:
            raise ValueError("current full physical ECC backup audit/length/hash/bad-block evidence failed")
        current=backup[:0x200000];original=current[0x20000:0x180000]
        core=Core();identity=core.resident_identity(original);current_sha=identity.installed_sha256.decode()
        if audit.get("installed_sha256",current_sha)!=current_sha or audit["regions"]["stage2"]["sha256"]!=current_sha:
            raise ValueError("current backup audit does not bind the verified installed identity")
        if manifest.get("source_installed_sha256",BASE)!=current_sha:
            raise ValueError("candidate was built against another installed image")
        command_name,expected_record=command_record(current,0x9ff3c000,resident_dir=a.current_resident_dir)
        if a.native241_continuous and (manifest.get("native241_service") is not True or manifest.get("power_mode")!="continuous_required"):
            raise ValueError("continuous installer requires the distinct native241 candidate")
        if a.native241_install_request and a.user_directed_install_policy:
            raise ValueError("new native241 authorization cannot reuse the earlier skip-tests policy")
        config=read_json(a.config_report);archive=(a.config_report.parent/"current-config.tar.gz").read_bytes()
        if config.get("configuration_export_pass") is not True or config.get("configuration_export",{}).get("verified") is not True or config["configuration_export"].get("bytes")!=len(archive) or config["configuration_export"].get("sha256")!=sha(archive):
            raise ValueError("current configuration archive is missing or does not match its verified export")
        candidate=(a.resident_dir/"resident-boot-development.bin").read_bytes()
        stage2=candidate[0x20000:0x180000];tail=(a.resident_dir/"resident-tail-ram-trial.bin").read_bytes()
        if len(candidate)!=0x200000 or sha(stage2)!=manifest["installed_sha256"] or sha(tail)!=manifest["tail_sha256"] or stage2[SLOT_OFFSET:SLOT_OFFSET+len(tail)]!=tail:
            raise ValueError("resident candidate/manifest/tail hash mismatch")
        acceptance={}
        if a.mode=="install":
            load=lambda path:read_json(path) if path else None
            if a.native241_install_request:
                if not a.native241_continuous:raise ValueError("native241 real installation requires the distinct continuous-power installer")
                acceptance=native241_request_evidence(manifest,manifest_digest,read_json(a.native241_install_request))
            elif a.native241_continuous:
                raise ValueError("continuous native241 installation requires its NEW exact-candidate request")
            elif a.user_directed_install_policy:
                acceptance=user_directed_evidence(manifest,manifest_digest,read_json(a.user_directed_install_policy))
            else:
                acceptance=real_evidence(manifest,manifest_digest,[read_json(path) for path in a.ram_acceptance],
                    load(a.cold_acceptance),load(a.reset_evidence),load(a.installer_preflight),load(a.timing_evidence))
        elif a.user_directed_install_policy or a.native241_install_request:
            raise ValueError("a user-directed install policy must not be used to label a preflight build")
        package=make_uboot(current,candidate);result=core.validate_identity(package,identity);prefix_bytes=result.writes[0].data_offset
        prefix=package[:prefix_bytes];suffix=package[prefix_bytes+len(stage2):];descriptor=stage2[DESCRIPTOR_OFFSET:DESCRIPTOR_OFFSET+256]
        if not prefix or not suffix or len(prefix)>16384 or len(suffix)>16384:raise ValueError("unexpected package wrapper extents")
        protected=[]
        for name,start,end in (("btl",0,0x20000),("env",0x180000,0x1a0000),("boot_tail",0x1a0000,0x200000),("tags",0xb00000,0xf00000)):
            value=sha(backup[start:end])
            if audit["regions"][name]["sha256"]!=value:raise ValueError("protected backup mismatch: "+name)
            protected.append(value)
        output=a.build_dir.resolve();output.mkdir(parents=True,exist_ok=False)
        token=secrets.token_hex(32) if a.mode=="install" else "preflight-no-write-authorization"
        header="struct bw_install_pin_dummy;\n"
        pins=sorted(set(RAM_PINS+SPI_PINS+NAND_WRITE_PINS+[(0x9084,0xa0),(0xa098,16),(0xa270,0x84)]))
        header+="static const struct bw_install_pin bw_install_pins[]={\n"
        for off,n in pins:header+="{"+hex(off)+","+str(n)+',"'+sha(original[off:off+n])+'"},\n'
        header+="};\n"
        for name,data in (("prefix",prefix),("tail",tail),("descriptor",descriptor),("suffix",suffix)):
            header+=byte_array("install_"+name,data)
        header+='static const char *const bw_install_protected[]={'+",".join('"'+value+'"' for value in protected)+"};\n"
        header+='static const struct bw_install_parts bw_install_parts={install_prefix,install_tail,install_descriptor,install_suffix,'+",".join(str(len(value)) for value in (prefix,tail,descriptor,suffix))+',"'+current_sha+'","'+sha(stage2)+'","'+sha(package)+'"};\n'
        header+='#define BW_INSTALL_AUTHORIZATION "'+token+'"\n'
        (output/"bw_install_data.h").write_text(header,encoding="utf-8",newline="\n")
        spi_header="static const struct bw_spi_pin bw_spi_pins[]={\n"
        for off,n in SPI_PINS+NAND_WRITE_PINS:spi_header+="{"+hex(off)+","+str(n)+',"'+sha(original[off:off+n])+'"},\n'
        (output/"bw_spi_pins.h").write_text(spi_header+"};\n",encoding="utf-8",newline="\n")
        script=output/"installer.ld"
        script.write_text("ENTRY(bw_installer_command)\nSECTIONS { . = 0x89400000; .text : { KEEP(*(.text.entry)) *(.text .text.*) } .rodata : { *(.rodata .rodata.*) } .data : { *(.data .data.*) } . = ALIGN(64); __bss_start = .; .bss (NOLOAD) : { *(.bss .bss.*) *(COMMON) } __bss_end = .; . = ALIGN(64); .ram_stack (NOLOAD) : { __ram_stack_bottom = .; . += 0x10000; __ram_stack_top = .; } __image_end = ALIGN(64); ASSERT(__image_end <= 0x89500000, \"installer exceeds RAM reservation\") /DISCARD/ : { *(.comment) *(.eh_frame*) } }\n",encoding="utf-8",newline="\n")
        flags=["-DBW_NATIVE_BOUNDED_SPI=1","-DBW_INSTALL_CONTINUOUS_POWER="+str(int(a.native241_continuous))]+(["-DBW_NATIVE_INSTALL=1"] if a.mode=="install" else ["-DBW_RAM_READONLY=1","-DBW_INSTALL_PREFLIGHT=1","-DBW_NATIVE_NAND_DRYRUN=1"])
        elf=output/"native-installer.elf"
        subprocess.run([str(a.zig.resolve()),"cc",*NATIVE_FLAGS,"-std=c11","-O2","-Wall","-Wextra","-Werror","-fno-builtin",
            "-I"+str(ROOT/"include"),"-I"+str(ROOT/"src"),"-I"+str(output),*flags,*map(str,SOURCES),
            str(ROOT/"adapter/native-installer.c"),str(ROOT/"adapter/vendor_nand.c"),str(ROOT/"adapter/vendor_bounded_spi.c"),
            "-Wl,-T,"+str(script),"-Wl,-e,bw_installer_command","-o",str(elf)],check=True)
        binary=output/"native-installer.bin"
        subprocess.run([str(a.zig.resolve()),"objcopy","--output-target","binary",str(elf),str(binary)],check=True)
        instruction_audit=verify_payload(elf.read_bytes(),binary.read_bytes(),base=0x89400000,limit=0x89500000)
        (output/"candidate-package.tar").write_bytes(package)
        report={"schema":"sk-d840n-native-installer-build-v1","utc":datetime.now(timezone.utc).isoformat(),"mode":a.mode,
            "native_write_enabled":a.mode=="install","nand_written":False,"flashable_release":False,"acceptance":acceptance,
            "authorization":token if a.mode=="install" else None,"candidate_sha256":sha(stage2),"original_sha256":current_sha,
            "vendor_abi_sha256":BASE,"power_mode":"continuous_required" if a.native241_continuous else "periodic20m",
            "runtime_command_name":command_name,"runtime_command_record_sha256":sha(expected_record),
            "package_sha256":sha(package),"package_bytes":len(package),"package_prefix_bytes":prefix_bytes,
            "resident_manifest_sha256":manifest_digest,"protected_sha256":protected,"backup_sha256":sha(backup),
            "configuration_sha256":sha(archive),"binary_sha256":sha(binary.read_bytes()),"elf_sha256":sha(elf.read_bytes()),
            "instruction_audit":instruction_audit,"ram":{"entry":"0x89400000","image_limit":"0x89500000",
            "package":["0x8a000000","0x8a200000"],"recovery":["0x8a200000","0x8a360000"]}}
        # The execution owner reopens and rehashes every final acceptance file
        # before opening COM3. A copied acceptance boolean is not sufficient.
        report["source_paths"]={"resident_dir":str(a.resident_dir.resolve()),"current_backup":str(a.current_backup.resolve()),
            "backup_audit":str(a.backup_audit.resolve()),"config_report":str(a.config_report.resolve())}
        report["source_paths"]["current_resident_dir"]=str(a.current_resident_dir.resolve()) if a.current_resident_dir else None
        evidence={"ram_acceptance":a.ram_acceptance,"cold_acceptance":a.cold_acceptance,"reset_evidence":a.reset_evidence,
                  "installer_preflight":a.installer_preflight,"timing_evidence":a.timing_evidence,
                  "user_directed_install_policy":a.user_directed_install_policy,"native241_install_request":a.native241_install_request}
        report["evidence_files"]={key:([{"path":str(path.resolve()),"sha256":sha(path.read_bytes())} for path in value]
            if isinstance(value,list) else {"path":str(value.resolve()),"sha256":sha(value.read_bytes())} if value else None)
            for key,value in evidence.items()}
        (output/"manifest.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        print(json.dumps({"mode":a.mode,"bytes":len(binary.read_bytes()),"sha256":report["binary_sha256"],"nand_written":False}))
    except (OSError,ValueError,KeyError,RuntimeError,subprocess.SubprocessError) as error:p.exit(1,"installer build: "+str(error)+"\n")

if __name__=="__main__":main()
