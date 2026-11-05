#!/usr/bin/env python3
"""Run the resident copy/registration then the exact original main_loop in RAM.

NOT a full reset/BTL load test and NOT a flash authorization. This deliberately
continues into OpenWrt without a reboot command. No old UART commands replay
after the transition. Current configuration is exported only to tmpfs and PC.
"""
import argparse
import base64
from datetime import datetime,timezone
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import sys
import tarfile
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from host.uart import AliasHook,Console,Span,TrialError,open_serial,parse_md,validate_placement
from ram_probe import BASE_HASH,field,runtime_map,relocated_record,clean_natural_anchor
from build import RAM_PINS
from audit_ram_elf import verify_payload

def terminal_plain(data):
    return re.sub(rb"\x1b\[[0-?]*[ -/]*[@-~]",b"",bytes(data))

CONFIG_EXPORT_COMMAND="sysupgrade -b /tmp/bootweb-current-config.tar.gz && printf 'BW_CONFIG_META ' && wc -c < /tmp/bootweb-current-config.tar.gz && sha256sum /tmp/bootweb-current-config.tar.gz && printf 'BW_CONFIG_HEX_BEGIN\\n' && busybox hexdump -v -e '1/1 \"%02x\"' /tmp/bootweb-current-config.tar.gz; printf '\\nBW_CONFIG_END\\n'"

def decode_config_export(export):
    decoded=export.decode("ascii",errors="replace").replace("\r","")
    meta=re.search(r"(?m)^BW_CONFIG_META\s+(\d+)\n([0-9a-f]{64})\s+/tmp/bootweb-current-config.tar.gz\n",decoded)
    body=re.search(r"(?ms)^BW_CONFIG_HEX_BEGIN\n([0-9a-f\n]+)^BW_CONFIG_END\n",decoded)
    if not meta or not body or not 1<=int(meta[1])<=0x200000:raise TrialError("current configuration export framing is invalid")
    try:archive=bytes.fromhex("".join(body[1].splitlines()))
    except ValueError as error:raise TrialError("configuration hex encoding is invalid") from error
    if len(archive)!=int(meta[1]) or hashlib.sha256(archive).hexdigest()!=meta[2]:raise TrialError("configuration archive length/hash mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive),mode="r:gz") as tar:entries=tar.getnames()
    if not entries or not any(name.lstrip("./").startswith("etc/config/") for name in entries):raise TrialError("export contains no UCI configuration")
    return archive,entries


def accepted_nonwriting_trial(clean, manifest, digest, *, native_locked=False):
    """A locked native trial is diagnostic evidence, never final acceptance."""
    if clean.get("build_manifest_sha256") != digest:
        raise TrialError("RAM acceptance does not bind THIS EXACT resident build")
    if native_locked:
        if (clean.get("resident_native_locked_trial_pass") is not True or
                clean.get("resident_ram_trial_pass") is not False or
                manifest.get("native_service_trial") is not True or
                manifest.get("resident_service_write_capable") is not False):
            raise TrialError("native-locked diagnostic evidence/policy is missing")
    elif clean.get("resident_ram_trial_pass") is not True:
        raise TrialError("THIS EXACT resident build has not passed complete RAM HTTP/return verification")
    if manifest.get("nand_written") is not False or manifest.get("native_write_enabled") is not False:
        raise TrialError("cold-equivalent trial accepts only non-writing development artifacts")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ("inspection","probe-report","clean-trial","build-dir","output-dir"):
        parser.add_argument("--"+key,type=Path,required=True)
    parser.add_argument("--port",default="COM3")
    parser.add_argument("--skip-config-export",action="store_true",help="verify normal boot only; do not claim a new configuration export")
    parser.add_argument("--native-locked-trial",action="store_true",help="explicit timing-locked native diagnostic; not final installation acceptance")
    args=parser.parse_args();args.output_dir.mkdir(parents=True,exist_ok=False)
    report={"schema":"sk-d840n-resident-postrelocation-cold-equivalent-v1","utc":datetime.now(timezone.utc).isoformat(),
            "nand_written":False,"reboot_requested":False,"full_cold_reset_verified":False,"flashable_release":False,
            "events":[]};port=console=hook=None;changed=False;transitioned=False;start=time.monotonic();status=1
    with (args.output_dir/"rx.bin").open("xb") as raw,(args.output_dir/"events.jsonl").open("x",encoding="utf-8",newline="\n") as log:
        def emit(event,**fields):
            item={"event":event,"elapsed":round(time.monotonic()-start,3),**fields};report["events"].append(item)
            line=json.dumps(item,ensure_ascii=False);log.write(line+"\n");log.flush();print(line,flush=True)
        def record(data):raw.write(data);raw.flush()
        try:
            old=json.loads(args.inspection.read_text(encoding="utf-8"));probe=json.loads(args.probe_report.read_text(encoding="utf-8"))
            clean=json.loads(args.clean_trial.read_text(encoding="utf-8"));manifest_path=args.build_dir/"manifest.json"
            manifest=json.loads(manifest_path.read_text(encoding="utf-8"));digest=hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            if not clean_natural_anchor(old) or not all(probe.get(k) is True for k in ("ram_probe_pass","alias_restored","cache_restored")):
                raise TrialError("fresh inspection/minimal ABI probe missing")
            accepted_nonwriting_trial(clean,manifest,digest,native_locked=args.native_locked_trial)
            report["native_locked_diagnostic"]=args.native_locked_trial
            boot=next(e for e in old["events"] if e["event"]=="boot_detected")
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(old["utc"])).total_seconds()-boot["elapsed"]+5
            if not 0<=age<(12 if args.skip_config_export else 10)*60:raise TrialError("not enough fresh-cycle time for bounded normal boot/config work")
            tail=(args.build_dir/"resident-tail-ram-trial.bin").read_bytes()
            if hashlib.sha256(tail).hexdigest()!=manifest["tail_sha256"]:raise TrialError("resident tail changed")
            report["build_manifest_sha256"]=digest;report["candidate_sha256"]=manifest["installed_sha256"]
            report["tail_sha256"]=manifest["tail_sha256"]
            source=(ROOT.parents[1]/"sk-d840n-openwrt/binary/boot.bin").read_bytes()
            if hashlib.sha256(source[0x20000:0x180000]).hexdigest()!=BASE_HASH:raise TrialError("unknown source ABI")
            port=open_serial(args.port);console=Console(port,emit=emit,raw=record)
            console.window.boot();console.window.started-=age+time.monotonic()-start;console.prompt=True
            _,_,board=console.identify();reloc=field(board,"relocaddr")
            if reloc!=0x9ff3c000 or field(board,"reloc off")!=0x17f3c000:raise TrialError("runtime relocation changed")
            expected="bootcmd=ping 192.168.9.25; mtd read kernel 0x88000000; mtd read dtb 0x88800000; bootm 0x88000000 - 0x88800000"
            env=console.inspect("printenv bootcmd")
            if expected not in env.splitlines():raise TrialError("bootcmd is not the pinned non-writing normal boot sequence")
            banks,reserved=runtime_map(board)
            reserved+=[Span(0x90000000,0x1000000,"DMA-low-guard"),Span(0x93000000,0x1000000,"DMA-high-guard")]
            allocations=[Span(0x88100000,0x60000,"tail-staging"),Span(0x89000000,0x100000,"web-payload-stack"),
                         Span(0x89100000,0xe0000,"resident-dispatcher"),Span(0x891f0000,0x10000,"resident-stack"),
                         Span(0x89200000,0x160000,"installed-identity-scratch"),
                         *([Span(0x89400000,0x600000,"native-ecc-snapshot")] if args.native_locked_trial else []),
                         Span(0x8a000000,0x6000000,"upload")]
            validate_placement(banks,reserved,allocations);report["ram_map"]=[vars(s) for s in [*banks,*reserved,*allocations]]
            def read(at,n):return parse_md(console.inspect(f"md.b {at:x} {n:x}",timeout=15),at,n)
            for off,n in sorted(set(RAM_PINS+[(0x9084,0xa0),(0xa098,16),(0xa270,0x84)])):
                if read(reloc+off,n)!=source[0x20000+off:0x20000+off+n]:raise TrialError("runtime code pin changed: "+hex(off))
            address=reloc+0x96c50;alias=relocated_record(source[0xb6c50:0xb6c88],0x17f3c000)
            if read(address,56)!=alias or struct.unpack_from("<Q",read(field(board,"TLB addr"),32),16)[0]!=0x80000711:
                raise TrialError("alias or memory translation changed")
            for name in ("dcache","icache"):
                if "Cache is ON" not in console.inspect(name):raise TrialError("initial cache state is not ON")
            def cache(name,on):
                console._exchange(name+(" on" if on else " off"),10)
                if ("Cache is ON" if on else "Cache is OFF") not in console.inspect(name):raise TrialError("cache transition failed")
            changed=True;cache("dcache",False);cache("icache",False)
            console.loadx(tail,allocations[0],banks=banks,reserved=reserved,other_allocations=allocations[1:])
            def write(at,data):
                if at!=address+24 or len(data)!=8:raise TrialError("non-alias RAM write rejected")
                console._exchange(f"mw.q {at:x} {struct.unpack('<Q',data)[0]:x} 1",10)
            hook=AliasHook(console,address,alias,read_memory=read,write_pointer=write);hook.install(0x88100000)
            cache("icache",True);cache("dcache",True)
            console.window.require(240);emit("cold_equivalent_started",entry="0x88100000",original_main_loop="0x9ff45084",nand_writer=False)
            # From here the program owns the transition into Linux. NEVER send
            # old U-Boot cleanup commands to that new context.
            transitioned=True;console.prompt=False;console.write(b"? coldtrial\r")
            data=bytearray();deadline=min(time.monotonic()+180,console.window.started+15*60);activated=False
            while time.monotonic()<deadline:
                data.extend(console.read())
                if b"Starting kernel" in data and b"Please press Enter to activate this console." in data and not activated:
                    console.write(b"\r");activated=True;emit("linux_console_activated")
                if activated and re.search(rb"(?:^|[\r\n])root@[^\r\n]*:[^\r\n]*# ?$",terminal_plain(data)):break
                if len(data)>0x100000:raise TrialError("boot console exceeds bounded capture")
            else:raise TrialError("normal OpenWrt boot did not reach a recognized root prompt")
            text=data.decode("utf-8",errors="replace");report["boot_console"]=text
            required=("BW_RESIDENT_BOOTSTRAP_ENTRY v=1 cold=1 equivalent=1", "BW_RESIDENT_REGISTERED handler=89100000",
                      "BW_RESIDENT_BOOTSTRAP_EXIT rc=0 cold=1 cache_restored=1", "Starting kernel", "OpenWrt")
            if not all(mark in text for mark in required):raise TrialError("cold-equivalent boot evidence is incomplete")
            report["normal_openwrt_boot_pass"]=True;emit("normal_openwrt_boot_pass",persistent_installation=False)
            if args.skip_config_export:
                report["configuration_export_attempted"]=False
                report["cold_equivalent_trial_pass"]=True;status=0
                return status
            def linux_exchange(command,end_marker,seconds,limit):
                if not activated:raise TrialError("not in this recognized Linux console")
                console.write(command.encode("ascii")+b"\r");answer=bytearray();end=min(time.monotonic()+seconds,console.window.started+17*60)
                marker=re.compile(rb"(?:^|[\r\n])"+end_marker.encode("ascii")+rb"\r?\r?\n")
                while time.monotonic()<end:
                    answer.extend(console.read())
                    if len(answer)>limit:raise TrialError("Linux export exceeds capture bound")
                    if marker.search(answer):return bytes(answer)
                raise TrialError("current Linux command/export timed out; no replay")
            info=linux_exchange("printf 'BW_CURRENT_LINUX_BEGIN\\n'; uname -a; cat /proc/uptime; ubus call system board; cat /proc/mtd; sha256sum /etc/config/*; printf 'BW_CURRENT_LINUX_END\\n'",
                                "BW_CURRENT_LINUX_END",30,0x40000)
            (args.output_dir/"current-linux.txt").write_bytes(info);emit("current_linux_identity_exported",bytes=len(info))
            export=linux_exchange(CONFIG_EXPORT_COMMAND,"BW_CONFIG_END",120,0x500000)
            archive,entries=decode_config_export(export)
            (args.output_dir/"current-config.tar.gz").write_bytes(archive)
            report["configuration_export"]={"bytes":len(archive),"sha256":hashlib.sha256(archive).hexdigest(),"entries":entries,"verified":True}
            report["cold_equivalent_trial_pass"]=True;status=0;emit("current_configuration_exported",bytes=len(archive),sha256=hashlib.sha256(archive).hexdigest())
        except (Exception,KeyboardInterrupt) as error:
            report["error"]=str(error) or type(error).__name__;emit("cold_equivalent_stopped",reason=report["error"])
        finally:
            if not transitioned and console:
                try:
                    if hook and hook.armed:hook.restore();report["alias_restored"]=True
                    if changed:
                        for name in ("icache","dcache"):cache(name,True)
                        report["cache_restored"]=True
                except Exception as error:report["cleanup_error"]=str(error)
            report["linux_transition_started"]=transitioned
            if port:port.close()
            (args.output_dir/"report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return status

if __name__=="__main__":raise SystemExit(main())
