#!/usr/bin/env python3
"""Save OFFLINE native241/#337 validation. No serial, browser or flash backend.

Successful output is never installation acceptance or a fresh device backup.
"""
import argparse
import ctypes as C
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from host.core import Core,UbootBudget
from host.image import regions
from host.resident_runtime import command_record

EXPECTED_337="f92c1ff53d8cf7c64eabc97122eeda316228d787b0aa448638670df88603cca2"


def sha(data):return hashlib.sha256(data).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("resident-dir","current-reference-dir","system-image","output-dir"):
        p.add_argument("--"+name,type=Path,required=True)
    a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=False)
    report={"schema":"sk-d840n-native241-offline-validation-v1","utc":datetime.now(timezone.utc).isoformat(),
            "nand_written":False,"serial_opened":False,"device_contacted":False,"power_confirmed":False,
            "fresh_current_backup_verified":False,"hardware_acceptance_complete":False,"offline_pass":False,
            "stopped_before_first_erase":True,
            "remaining":["user continuous-power confirmation","fresh complete ECC NAND and configuration backup",
                         "TTL resident installation + readback + protected audit","resident cold boot and physical Reset entry",
                         "real browser #337 upload/confirmation/write/readback","#337 cold boot, default network and bootweb re-entry"]}
    rc=1
    try:
        core=Core();current=(a.current_reference_dir/"resident-boot-development.bin").read_bytes()
        identity=core.resident_identity(regions(current)["stage2"])
        name,record=command_record(current,0x9ff3c000,resident_dir=a.current_reference_dir)
        manifest_bytes=(a.resident_dir/"manifest.json").read_bytes();manifest=json.loads(manifest_bytes)
        candidate=(a.resident_dir/"resident-boot-development.bin").read_bytes()
        package=(a.resident_dir/"candidate-package.tar").read_bytes()
        candidate_id=core.resident_identity(regions(candidate)["stage2"])
        if (manifest["installed_sha256"]!=candidate_id.installed_sha256.decode() or
                manifest["source_installed_sha256"]!=identity.installed_sha256.decode() or
                manifest.get("native241_service") is not True or manifest.get("power_mode")!="continuous_required"):
            raise ValueError("candidate identity or strict continuous-power build policy mismatch")
        stage2=regions(candidate)["stage2"]
        if core.lib.bw_resident_image_version(stage2,len(stage2))!=b"bootweb-resident-native241-v1":
            raise ValueError("candidate descriptor is not the installed native241 version")
        for region in ("stage1","environment","tail"):
            if regions(current)[region]!=regions(candidate)[region]:
                raise ValueError("candidate changed a protected BOOT reference region: "+region)
        boot_result=core.validate_identity(package,identity)
        if (boot_result.kind!=2 or boot_result.count!=1 or
                (boot_result.writes[0].offset,boot_result.writes[0].capacity)!=(0x20000,0x160000) or
                boot_result.writes[0].sha256!=candidate_id.installed_sha256):
            raise ValueError("U-Boot package leaves the exact fixed physical window")
        image=a.system_image.read_bytes()
        if sha(image)!=EXPECTED_337:raise ValueError("complete #337 SHA256 mismatch")
        system_result=core.validate_identity(image,identity,layout="native241")
        ledger=manifest["payload_build_policy"]["timing_ledger"]
        b=UbootBudget(erase_max_ms=ledger["ERASE_MAX_MS"],page_max_ms=ledger["PAGE_MAX_MS"],
            read_max_ms=ledger["READ_MAX_MS"],hash_max_ms=ledger["HASH_MAX_MS"],
            bad_query_max_ms=ledger["BAD_MAX_MS"],service_max_ms=ledger["SERVICE_MAX_MS"],
            fixed_max_ms=ledger["FIXED_MAX_MS"]+2000,power_policy=1)
        bound=C.c_uint64();error=C.create_string_buffer(192)
        if core.lib.bw_system_budget_bound(C.byref(system_result),C.byref(b),C.byref(bound),error):
            raise ValueError(error.value.decode())
        sources={}
        for folder,pattern in (("src","*.c"),("include","*.h"),("adapter","*.c"),("adapter/lwip","*.c"),
                               ("host","*.py"),("tools","*.py"),("tests","*.py"),("tests","*.cjs"),("web","*.html")):
            for path in sorted((ROOT/folder).glob(pattern)):
                sources[str(path.relative_to(ROOT))]=sha(path.read_bytes())
        report.update({"vendor_abi_sha256":identity.vendor_abi_sha256.decode(),
            "installed_reference_sha256":identity.installed_sha256.decode(),"runtime_command":name,
            "runtime_command_record_sha256":sha(record),"candidate_sha256":candidate_id.installed_sha256.decode(),
            "resident_manifest_sha256":sha(manifest_bytes),"uboot_package":boot_result.report(),
            "system_package":system_result.report(),"conservative_system_bound_ms":bound.value,
            "periodic_budget_verified":False,"web_uboot_enabled":False,"source_sha256":sources,
            "resident_dir":str(a.resident_dir.resolve()),"current_reference_dir":str(a.current_reference_dir.resolve()),
            "system_image":str(a.system_image.resolve())})
        tests=subprocess.run([sys.executable,"-X","utf8","-m","unittest","discover","-s",str(ROOT/"tests"),"-q"],
                             cwd=ROOT.parent,capture_output=True,text=True,encoding="utf-8",timeout=120)
        with (a.output_dir/"unittest.log").open("x",encoding="utf-8",newline="\n") as log:
            log.write(tests.stdout);log.write(tests.stderr)
        report["test_returncode"]=tests.returncode
        if tests.returncode:raise ValueError("offline regressions failed; see unittest.log")
        report["offline_pass"]=True;rc=0
    except (OSError,ValueError,KeyError,RuntimeError,subprocess.SubprocessError) as error:
        report["error"]=str(error)
    finally:
        with (a.output_dir/"report.json").open("x",encoding="utf-8",newline="\n") as output:
            json.dump(report,output,ensure_ascii=False,indent=2);output.write("\n")
    print(json.dumps({"offline_pass":report["offline_pass"],"nand_written":False,
                     "report":str((a.output_dir/"report.json").resolve()),"error":report.get("error")},ensure_ascii=False))
    return rc


if __name__=="__main__":raise SystemExit(main())
