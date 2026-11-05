#!/usr/bin/env python3
"""RAM-only execution of the real two-stage resident copy/command dispatcher.

NOT a full stage2 cold boot and NOT permission to flash. Uses one UART owner,
natural-cycle evidence, XMODEM length/CRC, code pins, real Ethernet HTTP and
complete alias/cache restoration. No NAND writer or reboot API is present.
"""
import argparse
from datetime import datetime,timedelta,timezone
import hashlib
import json
import re
from pathlib import Path
import secrets
import struct
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from host.uart import AliasHook,Console,Span,TrialError,open_serial,parse_md,validate_placement
from ram_probe import BASE_HASH,field,runtime_map,relocated_record,clean_natural_anchor
from build import RAM_PINS
from audit_ram_elf import verify_payload
import ram_web


def native_locked_state(token):
    status,data=ram_web.request("GET","/api/state");value=json.loads(data)
    if status!=200 or value.get("mode")!="native-development" or value.get("token")!=token or \
       value.get("resident") is not False or value.get("can_confirm") is not False or value.get("can_reboot") is not False or \
       "timing bound has not been verified" not in value.get("lock_reason",""):
        raise TrialError("native callback peer is not this UART-bound timing-locked RAM session")
    return value


def native_locked_evidence(text):
    releases=re.findall(r"BW_INSTALL_NAND_RELEASE rc=0 reads=(\d+) corrected_reads=(\d+) erases=0 pages=0 skipped_ff=0 .*failed=0",text)
    backup=re.findall(r"BW_RESIDENT_NATIVE_ENTRY .*boot_sha256=([0-9a-f]{64}) tags_sha256=([0-9a-f]{64}) timing_verified=0",text)
    audit=re.findall(r"BW_RESIDENT_BACKUP_AUDIT rc=0 boot_sha256=([0-9a-f]{64}) tags_sha256=([0-9a-f]{64}) no_update=1",text)
    returned="BW_RESIDENT_NATIVE_EXIT rc=0" in text
    restored=bool(re.search(r"BW_BOUNDED_SPI_DETACHED rc=0 .*denied=0 pointer_restored=1",text))
    no_write=len(releases)==1 and bool(backup) and backup==audit and "BW_NAND_REAL_" not in text
    return {"native_return_pass":returned and restored and no_write,"nand_unchanged_pass":no_write,
            "native_callbacks_executed_with_mutations_locked":True,"nand_readonly_verified":False,
            "backup_hashes":backup,"immutable_readonly_artifact":False,
            "native_wrapper_deadline_binding_observed":
                "BW_NATIVE_IO_BOUNDS read_ms=3000 bad_ms=100 erase_ms=2100 page_ms=300 complete_call_gates=1 proof_claimed=0" in text,
            "whole_install_timing_verified":False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspection",type=Path,required=True)
    parser.add_argument("--probe-report",type=Path,required=True)
    parser.add_argument("--build-dir",type=Path,required=True)
    parser.add_argument("--sample",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--port",default="COM3")
    parser.add_argument("--serial-entry",action="store_true",help="register the cold-path command in RAM, then invoke literal bare bootweb; restore the whole original record")
    parser.add_argument("--native-locked-trial",action="store_true",help="exercise the distinct native callback build with TIMING_PROVEN=0; never authorizes writes")
    args=parser.parse_args();args.output_dir.mkdir(parents=True,exist_ok=False)
    started=time.monotonic();port=console=hook=thread=None;token=secrets.token_hex(32)
    ready=threading.Event();done=threading.Event();execution={};cache_touched=False;status=1;registered_record=None
    report={"schema":"sk-d840n-resident-bootstrap-ram-trial-v1","utc":datetime.now(timezone.utc).isoformat(),
            "nand_written":False,"flashable_release":False,"cold_boot_verified":False,"events":[]}
    lock=threading.Lock()
    with (args.output_dir/"rx.bin").open("xb") as raw,(args.output_dir/"events.jsonl").open("x",encoding="utf-8",newline="\n") as log:
        lines=bytearray()
        def emit(event,**fields):
            if "text" in fields:fields["text"]=fields["text"].replace(token,"<ephemeral-token>")
            value={"event":event,"elapsed":round(time.monotonic()-started,3),**fields}
            with lock:
                report["events"].append(value);line=json.dumps(value,ensure_ascii=False)
                log.write(line+"\n");log.flush();print(line,flush=True)
        def record(data):
            raw.write(data);raw.flush();lines.extend(data)
            if b"BW_RAM_NETWORK_STARTED" in lines or (args.native_locked_trial and b"BW_RESIDENT_NETWORK_STARTED" in lines):ready.set()
            while b"\n" in lines:
                line,_,rest=lines.partition(b"\n");lines[:]=rest
                session=re.fullmatch(rb"BW_RESIDENT_SESSION token=([0-9a-f]{64})",line.strip(b"\r"))
                if session:
                    nonlocal token
                    token=session[1].decode("ascii")
                    report["device_token_sha256"]=hashlib.sha256(token.encode()).hexdigest()
                if line.startswith((b"BW_RAM_",b"BW_RESIDENT_",b"BW_BOUNDED_",b"BW_NAND_")):
                    emit("native_progress",text=line.strip(b"\r").decode("ascii",errors="replace"))
            if len(lines)>8192:del lines[:-8192]
        def cache(name,on):
            console._exchange(name+(" on" if on else " off"),10)
            if ("Cache is ON" if on else "Cache is OFF") not in console.inspect(name):
                raise TrialError("cache state did not change as requested")
        try:
            old=json.loads(args.inspection.read_text(encoding="utf-8"))
            probe=json.loads(args.probe_report.read_text(encoding="utf-8"))
            if not all(probe.get(k) is True for k in ("ram_probe_pass","alias_restored","cache_restored")):
                raise TrialError("minimal fresh RAM probe did not return cleanly")
            boot=next(e for e in old["events"] if e["event"]=="boot_detected")
            report["natural_boot_utc"]=(datetime.fromisoformat(old["utc"])+timedelta(seconds=boot["elapsed"])).isoformat()
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(old["utc"])).total_seconds()-boot["elapsed"]+5
            if not clean_natural_anchor(old) or not 0<=age<12*60:
                raise TrialError("natural boot evidence expired or original prompt was not clean")
            manifest=json.loads((args.build_dir/"manifest.json").read_text(encoding="utf-8"))
            report["build_manifest_sha256"]=hashlib.sha256((args.build_dir/"manifest.json").read_bytes()).hexdigest()
            report["device_entropy_trial"]=manifest.get("device_entropy_trial") is True
            report["native_locked_trial"]=args.native_locked_trial
            if args.native_locked_trial:
                policy=manifest.get("payload_build_policy",{})
                if not (manifest.get("native_service_trial") is True and policy.get("native_timing_verified") is False and
                        policy.get("resident_service_write_capable") is False and policy.get("timing_ledger",{}).get("TIMING_PROVEN")==0):
                    raise TrialError("native callback trial is not bound to a timing-locked build")
            elif manifest.get("native_service_trial"):
                raise TrialError("native callback payload requires its separate locked-trial opt-in")
            if args.serial_entry and not report["device_entropy_trial"]:raise TrialError("bare bootweb requires verified device entropy code")
            report["native_bounded_spi_trial"]=manifest.get("payload_build_policy",{}).get("native_bounded_spi_trial") is True
            report["native_nand_abi_dryrun"]=manifest.get("payload_build_policy",{}).get("native_nand_abi_dryrun") is True
            report["native_nand_preflight"]=manifest.get("payload_build_policy",{}).get("native_nand_preflight") is True
            payload=(args.build_dir/"resident-tail-ram-trial.bin").read_bytes()
            if manifest.get("nand_written") is not False or manifest.get("native_write_enabled") is not False or manifest.get("tail_sha256")!=hashlib.sha256(payload).hexdigest():
                raise TrialError("unexpected resident development artifact/policy")
            # The ELF excludes only the explicitly verified zero tail alignment.
            stage0=(args.build_dir/"resident-stage0.elf").read_bytes()
            file_bytes=max(int(s["address"],16)+s["file_bytes"] for s in ram_web.elf_info(stage0)["load_segments"] if s["file_bytes"])-0x88100000
            if any(payload[file_bytes:]):raise TrialError("nonzero ELF padding")
            report["stage0_audit"]=verify_payload(stage0,payload[:file_bytes],base=0x88100000,limit=0x88160000)
            source=(ROOT.parents[1]/"sk-d840n-openwrt/binary/boot.bin").read_bytes()
            if hashlib.sha256(source[0x20000:0x180000]).hexdigest()!=BASE_HASH:raise TrialError("unknown baseline")
            sample=args.sample.read_bytes()
            if not 4096<=len(sample)<=0x6000000:raise TrialError("sample outside reserved upload area")
            port=open_serial(args.port);console=Console(port,emit=emit,raw=record)
            console.window.boot();console.window.started-=age+time.monotonic()-started;console.prompt=True
            _,_,board=console.identify();reloc=field(board,"relocaddr")
            if reloc!=0x9ff3c000 or field(board,"reloc off")!=0x17f3c000:raise TrialError("runtime identity/relocation changed")
            banks,reserved=runtime_map(board)
            reserved+=[Span(0x90000000,0x1000000,"DMA-low-guard"),Span(0x93000000,0x1000000,"DMA-high-guard")]
            allocations=[Span(0x88100000,0x60000,"BTL-tail-staging"),Span(0x89000000,0x100000,"web-payload-and-stack"),
                         Span(0x89100000,0xe0000,"resident-dispatcher"),Span(0x891f0000,0x10000,"resident-stack"),
                         Span(0x89200000,0x160000,"installed-identity-scratch"),
                         Span(0x8a000000,0x6000000,"upload")]
            if args.native_locked_trial:allocations.append(Span(0x89400000,0x600000,"native-current-ECC-snapshot"))
            validate_placement(banks,reserved,allocations);report["ram_map"]=[vars(s) for s in [*banks,*reserved,*allocations]]
            def read_memory(at,n):return parse_md(console.inspect(f"md.b {at:x} {n:x}",timeout=15),at,n)
            for off,n in sorted(set(RAM_PINS+[(0x9084,0xa0),(0xa270,0x84)])):
                if read_memory(reloc+off,n)!=source[0x20000+off:0x20000+off+n]:raise TrialError("runtime pin differs: "+hex(off))
            address=reloc+0x96c50;alias=relocated_record(source[0xb6c50:0xb6c88],0x17f3c000)
            if read_memory(address,56)!=alias or struct.unpack_from("<Q",read_memory(field(board,"TLB addr"),32),16)[0]!=0x80000711:
                raise TrialError("command ABI or identity translation differs")
            for name in ("dcache","icache"):
                if "Cache is ON" not in console.inspect(name):raise TrialError("initial cache state is not measured ON")
            cache_touched=True;cache("dcache",False);cache("icache",False)
            console.loadx(payload,allocations[0],banks=banks,reserved=reserved,other_allocations=allocations[1:])
            def write_pointer(at,data):
                if at!=address+24 or len(data)!=8:raise TrialError("non-alias RAM pointer write rejected")
                console._exchange(f"mw.q {at:x} {struct.unpack('<Q',data)[0]:x} 1",10)
            hook=AliasHook(console,address,alias,read_memory=read_memory,write_pointer=write_pointer);hook.install(0x88100000)
            # Unlike earlier trials, entry REALLY starts with the measured cold
            # boot cache state ON. The native dispatcher owns off/on handling.
            cache("icache",True);cache("dcache",True)
            if args.serial_entry:
                registration=console._exchange("? registertrial",30)
                if not all(mark in registration for mark in ("BW_RESIDENT_BOOTSTRAP_ENTRY v=1 cold=1 equivalent=0",
                           "BW_RESIDENT_BOOTSTRAP_EXIT rc=0 cold=1 cache_restored=1 record_restored=0 stack_ok=1")):
                    raise TrialError("cold-path command registration failed")
                registered_record=read_memory(address,56)
                name,maxargs,pad,repeat,handler,usage,help_at,complete=struct.unpack("<QIIQQQQQ",registered_record)
                original=struct.unpack("<QIIQQQQQ",alias)
                if handler!=0x89100000 or maxargs!=5 or pad!=original[2] or repeat!=original[3] or complete!=original[7] or not all(0x89100000<=at<0x891e0000 for at in (name,usage,help_at)) or read_memory(name,8)!=b"bootweb\0":
                    raise TrialError("registered command record differs from the bounded cold-path format")
                report["cold_command_registration_pass"]=True
                emit("serial_bootweb_registered",handler=hex(handler),name="bootweb",persistent=False)
            duration=min(180000,int((console.window.remaining()-60)*1000))
            if duration<90000:raise TrialError("insufficient power window for bounded HTTP and restoration")
            ram_web.HTTP_DEADLINE=min(time.monotonic()+duration/1000-30,console.window.started+17*60-60)
            def execute():
                try:execution["text"]=console._exchange("bootweb" if args.serial_entry else "? trial "+token+" "+str(duration),duration/1000+45)
                except Exception as error:execution["error"]=str(error)
                finally:done.set()
            thread=threading.Thread(target=execute,daemon=False);thread.start()
            end=time.monotonic()+90
            while not ready.wait(.1):
                if done.is_set():raise TrialError("bootstrap returned before native network start: "+execution.get("text",execution.get("error","")))
                if time.monotonic()>=end:raise TrialError("bootstrap/native startup timed out")
            state_reader=native_locked_state if args.native_locked_trial else ram_web.native_state
            state_reader(token);report["http_ready"]=True;emit("http_ready",url="http://192.168.9.1")
            if report["device_entropy_trial"] and not report.get("device_token_sha256"):
                raise TrialError("native service did not supply a device-generated token")
            ram_web.http_checks(token,sample,emit,state_reader=state_reader);report["http_checks_pass"]=True
        except (Exception,KeyboardInterrupt) as error:
            report["error"]=str(error) or type(error).__name__;emit("resident_trial_stopped",reason=report["error"])
        finally:
            if thread:
                while thread.is_alive():thread.join(1)
                text=execution.get("text","");report["native_console"]=text.replace(token,"<ephemeral-token>")
                if "error" in execution:report["execution_error"]=execution["error"]
                report.update(native_locked_evidence(text) if args.native_locked_trial else ram_web.native_evidence(text))
                if report.get("native_bounded_spi_trial"):
                    evidence=re.findall(r"BW_BOUNDED_SPI_DETACHED rc=0 calls=([0-9]+) status_reads=([0-9]+) max_ms=([0-9]+) denied=0 pointer_restored=1",text)
                    report["bounded_spi_pass"]=len(evidence)==1 and all(int(x)>0 for x in evidence[0][:2]) and "BW_BOUNDED_SPI_ERROR" not in text and "BW_BOUNDED_SPI_DENIED" not in text
                    report["bounded_spi_evidence"]=evidence
                report["bootstrap_return_pass"]="BW_RESIDENT_BOOTSTRAP_EXIT rc=0 cold=0 cache_restored=1 record_restored=1 stack_ok=1" in text
                if report.get("device_entropy_trial"):
                    report["device_entropy_pass"]=bool(report.get("device_token_sha256")) and "BW_RESIDENT_ENTROPY rc=0 bytes=32 source=CNTPCT_JENT_SHA3 startup_rct_apt=1" in text
                if report.get("native_nand_abi_dryrun"):
                    report["nand_abi_dryrun_pass"]=bool(re.search(
                        r"BW_NATIVE_NAND_PREFLIGHT rc=0 erases=11 pages=704 max_bad_ms=\d+ max_erase_ms=\d+ max_page_ms=\d+ hardware_mutations=0",text)) if report.get("native_nand_preflight") else all(mark in text for mark in (
                        "BW_NAND_DRY_END rc=0 kind=erase offset=20000 wren=1 loads=0 executes=1 hardware_mutations=0",
                        "BW_NAND_DRY_END rc=0 kind=page offset=20000 wren=1 loads=1 executes=1 hardware_mutations=0",
                        "BW_NAND_DRY_PAGE_ABI rc=0 emulation_rc=0 returned=2048 requested=2048 hardware_mutations=0"))
                emit("native_return",bootstrap_pass=report["bootstrap_return_pass"],nand_readonly=report["nand_readonly_verified"])
            try:
                if hook and hook.armed:
                    if not console.prompt or not console.identified:raise TrialError("session lost; no RAM restoration replay")
                    for name in ("dcache","icache"):
                        if "Cache is ON" not in console.inspect(name):raise TrialError("native dispatcher did not restore caches")
                    report["native_cache_restored"]=True
                    cache("dcache",False);cache("icache",False)
                    if registered_record is not None:
                        if read_memory(address,56)!=registered_record:raise TrialError("resident command changed; no blind full-record restoration")
                        # Restore the six non-handler words ONLY within this
                        # original measured cmd_tbl record. AliasHook owns +24.
                        for at in (0,8,16,32,40,48):
                            value=struct.unpack_from("<Q",alias,at)[0]
                            console._exchange(f"mw.q {address+at:x} {value:x} 1",10)
                    hook.restore();report["alias_restored"]=True
                if cache_touched:
                    if not console.prompt or not console.identified:raise TrialError("cannot restore caches in an unknown session")
                    cache("icache",True);cache("dcache",True);report["cache_restored"]=True
            except (OSError,TrialError) as error:report["cleanup_error"]=str(error);emit("cleanup_failed",reason=str(error))
            report["resident_ram_trial_pass"]=all(report.get(k) is True for k in ("http_checks_pass","native_return_pass","bootstrap_return_pass","native_cache_restored","alias_restored","cache_restored"))
            if report.get("native_bounded_spi_trial") and not report.get("bounded_spi_pass"):report["resident_ram_trial_pass"]=False
            if report.get("device_entropy_trial") and not report.get("device_entropy_pass"):report["resident_ram_trial_pass"]=False
            if report.get("native_nand_abi_dryrun") and not report.get("nand_abi_dryrun_pass"):report["resident_ram_trial_pass"]=False
            if args.serial_entry:
                report["serial_bootweb_entry_pass"]=report["resident_ram_trial_pass"] and report.get("cold_command_registration_pass") is True
            if report["resident_ram_trial_pass"]:status=0
            if args.native_locked_trial:
                report["resident_native_locked_trial_pass"]=report["resident_ram_trial_pass"]
                report["resident_ram_trial_pass"]=False
                report["final_candidate_acceptance"]=False
            if port:port.close()
            ram_web.HTTP_DEADLINE=None
            (args.output_dir/"report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return status


if __name__=="__main__":raise SystemExit(main())
