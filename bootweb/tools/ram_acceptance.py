#!/usr/bin/env python3
"""Summarize existing RAM-only evidence. Never opens UART/network or unlocks writes."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

from audit_ram_elf import verify_payload
from ram_web import native_evidence
from verify import inputs
from host.uart import Span,validate_placement


def require(condition, message):
    if not condition:raise ValueError(message)


def timestamp(report):
    value=datetime.fromisoformat(report["utc"])
    require(value.utcoffset() is not None,"evidence timestamp needs a timezone")
    return value


def check_cycle(inspection,probe,web):
    events=inspection["events"]
    require(inspection.get("inspection_pass") is True and events[-1]["event"]=="inspection_finished",
            "natural inspection did not finish cleanly")
    require(not any(e["event"]=="readonly_reinspection" for e in events),"reinspection is not a new power cycle")
    boots=[e for e in events if e["event"]=="boot_detected"]
    guards=[e for e in events if e["event"]=="natural_cycle_guard"]
    require(len(boots)==len(guards)==1,"a single guarded natural boot is required")
    require(boots[0]["elapsed"]>=guards[0]["elapsed"]+guards[0]["seconds_until_earliest_boot"],
            "early software reset cannot establish a natural cycle")
    boot=timestamp(inspection)+timedelta(seconds=boots[0]["elapsed"])
    require(all(probe.get(k) is True for k in ("ram_probe_pass","alias_restored","cache_restored")),
            "minimal probe/cleanup is incomplete")
    require(all(web.get(k) is True for k in ("web_pass","http_ready","http_checks_pass","native_return_pass",
            "alias_restored","cache_restored","nand_readonly_verified","watchdog_verified")),"web cycle is incomplete")
    require(not any(web.get(k) for k in ("error","cleanup_error","execution_error","evidence_error")),
            "a failed cycle cannot be accepted")
    require(web.get("flashable_release") is False and web.get("native_write_enabled") is False and
            web.get("linux_userspace_used") is False,"evidence must be native RAM-only, never flashable")
    require(boot<=timestamp(probe)<=timestamp(web),"probe belongs to a different or stale boot")
    evidence=native_evidence(web["native_console"])
    require(evidence["native_return_pass"],"native NAND/watchdog/return evidence is incomplete")
    spans=[Span(**s) for s in web["ram_map"]]
    allocations=[s for s in spans if s.name in ("bootweb-image","http-upload")]
    banks=[s for s in spans if s.name.startswith("memory-")]
    reserved=[s for s in spans if s not in allocations and s not in banks]
    require(sorted((s.start,s.size) for s in allocations)==[(0x89000000,0x200000),(0x8a000000,0x6000000)],
            "missing/incorrect image and upload reservations")
    validate_placement(banks,reserved,allocations)
    stack=web["private_stack"];bottom=int(stack["bottom"],16);top=int(stack["top"],16)
    require(0x89000000<bottom<top<=0x89200000 and top-bottom==stack["bytes"]==65536,"invalid private RAM stack")
    checks=[e for e in web["events"] if e["event"]=="http_check"]
    require(all(e.get("passed") is True for e in checks),"an HTTP check failed")
    require([e["iteration"] for e in checks if e["case"]=="interrupted-upload"]==list(range(1,11)),
            "exactly ten interrupted uploads and reconnects are required")
    for case in ("identity-and-write-lock","embedded-page","invalid-origin-and-package","complete-real-firmware"):
        require(sum(e["case"]==case for e in checks)==1,"missing/duplicate HTTP case: "+case)
    for route in ("/api/confirm","/api/reboot"):
        denials=[e for e in checks if e["case"]==route]
        require(len(denials)==1 and denials[0].get("http_status")==409,"missing explicit HTTP 409: "+route)
    entries=[e for e in web["events"] if e["event"]=="native_progress" and
             e.get("text","").startswith("BW_RAM_READONLY_ENTRY ")]
    require(len(entries)==1,"missing/duplicate native service entry")
    duration=re.fullmatch(r"BW_RAM_READONLY_ENTRY duration_ms=(\d+) caps=0 writes_compiled_out=1",entries[0]["text"])
    require(duration is not None and 1000<=int(duration[1])<=180000,"unbounded/non-readonly service duration")
    age=(timestamp(web)-boot).total_seconds()
    stop_age=age+entries[0]["elapsed"]+int(duration[1])/1000
    restore_age=age+max(e["elapsed"] for e in web["events"])
    require(stop_age<17*60 and restore_age<18*60,"power-window service/restore deadline exceeded")
    complete=next(e for e in checks if e["case"]=="complete-real-firmware")
    return {"boot_utc":boot.isoformat(),"payload_sha256":web["artifact"]["sha256"],
            "elf_sha256":web["instruction_audit"]["elf_sha256"],"firmware_sha256":complete["sha256"],
            "firmware_bytes":complete["bytes"],"service_stop_boot_age_seconds":round(stop_age,3),
            "restore_boot_age_seconds":round(restore_age,3),"ram_map":web["ram_map"],
            "private_stack":web["private_stack"],**evidence}


def check_cycles(cycles):
    require(len(cycles)>=2,"at least two complete natural power cycles are required")
    ordered=sorted(cycles,key=lambda c:datetime.fromisoformat(c["boot_utc"]))
    for before,after in zip(ordered,ordered[1:]):
        gap=(datetime.fromisoformat(after["boot_utc"])-datetime.fromisoformat(before["boot_utc"])).total_seconds()
        periods=round(gap/1230)
        require(periods>=1 and abs(gap-periods*1230)<=5,"duplicate boot or inconsistent natural power-cycle timing")
    for key in ("payload_sha256","elf_sha256","firmware_sha256","firmware_bytes","nand_hashes"):
        require(all(c[key]==ordered[0][key] for c in ordered),"cross-cycle evidence differs: "+key)
    return ordered


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle",nargs=3,action="append",required=True,type=Path,metavar=("INSPECTION","PROBE","WEB"))
    parser.add_argument("--offline",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():parser.error("report exists; choose a new output")
    report={"schema":"sk-d840n-ram-readonly-acceptance-v1","utc":datetime.now(timezone.utc).isoformat(),
            "ram_readonly_acceptance_pass":False,"two_power_cycles_verified":False,"flashable_release":False,
            "native_write_enabled":False,"cycles":[],"unverified":["BTL rescue","NAND writes/erase/reboot",
                "2.5G performance","Reset entry","active watchdog servicing","same-power-cycle network reentry"]}
    status=1
    try:
        offline=json.loads(args.offline.read_text(encoding="utf-8"))
        require(offline.get("offline_pass") is True and offline["tests"]["run"]>0 and
                not any(offline["tests"][k] for k in ("failed","errors","skipped")),"offline regression did not pass")
        require(offline["source_inputs"]["sha256"]==inputs()["sha256"],"offline evidence does not match current sources")
        report["offline"]={"path":str(args.offline.resolve()),"sha256":digest(args.offline),"tests":offline["tests"]["run"]}
        for paths in args.cycle:
            inspection,probe,web=[json.loads(p.read_text(encoding="utf-8")) for p in paths]
            cycle=check_cycle(inspection,probe,web)
            folder=paths[2].parent
            audit=verify_payload((folder/"payload.elf").read_bytes(),(folder/"payload.bin").read_bytes())
            require(audit["payload_sha256"]==cycle["payload_sha256"] and audit["elf_sha256"]==cycle["elf_sha256"],
                    "saved payload differs from its audited/tested artifact")
            require(all(audit["layout_symbols"]["__ram_stack_"+key]==cycle["private_stack"][key] for key in ("bottom","top")),
                    "recorded stack differs from the tested ELF")
            dom=json.loads((folder/"browser-dom.json").read_text(encoding="utf-8"))
            require(all(dom.get(k) is True for k in ("confirmationHidden","confirmDisabled","rebootDisabled")) and
                    "RAM 只读（原生 U-Boot）" in dom.get("device","") and "校验通过" in dom.get("status",""),
                    "browser did not verify this native read-only page")
            require((folder/"browser.jpg").read_bytes().startswith(b"\xff\xd8"),"missing browser screenshot")
            evidence_paths=[*paths,folder/"payload.bin",folder/"payload.elf",folder/"browser.jpg",
                            folder/"browser-dom.json",folder/"browser-ax.txt"]
            for path in paths:evidence_paths.extend([path.parent/"rx.bin",path.parent/"events.jsonl"])
            cycle["files"]={str(p.resolve()):digest(p) for p in evidence_paths}
            report["cycles"].append(cycle)
        report["cycles"]=check_cycles(report["cycles"])
        report["ram_readonly_acceptance_pass"]=report["two_power_cycles_verified"]=True;status=0
    except (OSError,ValueError,KeyError,RuntimeError,StopIteration) as error:
        report["error"]=str(error) or type(error).__name__
    with args.output.open("x",encoding="utf-8",newline="\n") as out:
        json.dump(report,out,ensure_ascii=False,indent=2);out.write("\n")
    print(str(args.output.resolve()))
    print("RAM-only evidence PASS; NOT FLASHABLE." if status==0 else "NOT PASSED: "+report["error"])
    return status


if __name__=="__main__":raise SystemExit(main())
