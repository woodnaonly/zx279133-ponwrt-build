#!/usr/bin/env python3
"""Load ONLY the pinned RAM-readonly build and test its native HTTP service.

Same exclusive UART for XMODEM, temporary alias and final cleanup. HTTP is
tested directly on the board, never SSH/Linux or a localhost demo. A fresh
random challenge binds the HTTP peer to the program entered through UART.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import re
import secrets
import socket
import struct
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from host.uart import AliasHook,Console,Span,TrialError,open_serial,parse_md,validate_placement
from ram_probe import BASE_HASH,field,runtime_map,relocated_record
from build import RAM_PINS
from verify import elf_info
from audit_ram_elf import verify_payload
from host.backup import download_backup
from host.core import Core
from host.resident_runtime import command_record

# A single live UART generation owns this process. This deadline is never
# extended on HTTP reconnect; leave 45 seconds for socket failure and cleanup.
HTTP_DEADLINE=None


def http_timeout(requested):
    if HTTP_DEADLINE is None:return requested
    left=HTTP_DEADLINE-time.monotonic()
    if left<=0:raise TrialError("native HTTP deadline reached; no more network requests")
    return min(requested,left)


def request(method,path,body=None,*,token=None,declared=None,origin=None,timeout=8):
    connection=http.client.HTTPConnection("192.168.9.1",80,timeout=http_timeout(timeout))
    guard=reply=None
    headers={"Host":"192.168.9.1","Connection":"close"}
    if token is not None:headers["X-Bootweb-Token"]=token
    if origin:headers["Origin"]=origin
    if body is not None:headers["Content-Length"]=str(len(body) if declared is None else declared)
    try:
        if HTTP_DEADLINE is not None:
            # An idle socket timeout alone can be kept alive by a slow stream.
            # Shutdown the EXACT connected socket at the absolute deadline,
            # even while HTTPResponse is parsing headers or waiting for data.
            connection.connect();wire=connection.sock
            def abort_wait():
                try:wire.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            guard=threading.Timer(http_timeout(max(0,HTTP_DEADLINE-time.monotonic())),abort_wait)
            guard.daemon=True;guard.start()
        connection.request(method,path,body=body,headers=headers)
        reply=connection.getresponse();chunks=[];received=0
        while True:
            http_timeout(timeout)
            data=reply.read1(65536)
            if not data:break
            received+=len(data)
            if received>0x100080:raise TrialError("native HTTP response exceeds fixed backup/page bound")
            chunks.append(data)
        http_timeout(timeout)
        return reply.status,b"".join(chunks)
    finally:
        if guard:guard.cancel()
        if reply:reply.close()
        connection.close()


def native_state(token):
    status,data=request("GET","/api/state")
    state=json.loads(data)
    if status!=200 or state.get("mode") not in ("native-ram-readonly","native-resident-readonly") or state.get("token")!=token or \
            state.get("can_confirm") is not False or state.get("can_reboot") is not False:
        raise TrialError("HTTP peer is not this UART-bound immutable read-only session")
    return state


def http_checks(token,payload,emit,*,state_reader=None):
    if state_reader is None:state_reader=native_state
    state=state_reader(token);emit("http_check",case="identity-and-write-lock",passed=True)
    status,page=request("GET","/")
    if status!=200 or page!=(ROOT/"web/index.html").read_bytes():raise TrialError("native page did not match embedded HTML")
    emit("http_check",case="embedded-page",passed=True,bytes=len(page))
    for origin in ("http://evil.invalid",):
        status,_=request("POST","/api/upload",b"invalid",token=token,origin=origin)
        if status!=409:raise TrialError("cross-origin upload accepted")
    status,_=request("POST","/api/upload",b"not-an-upgrade-package",token=token)
    if status!=409:raise TrialError("invalid firmware accepted")
    state_reader(token)
    request("POST","/api/cancel",b"",token=token)
    emit("http_check",case="invalid-origin-and-package",passed=True)
    for index in range(10):
        connection=http.client.HTTPConnection("192.168.9.1",80,timeout=http_timeout(8))
        try:
            connection.putrequest("POST","/api/upload",skip_host=True)
            connection.putheader("Host","192.168.9.1")
            connection.putheader("X-Bootweb-Token",token)
            connection.putheader("Content-Length",str(len(payload)))
            connection.endheaders()
            connection.send(payload[:1024+index*137])
            connection.sock.shutdown(socket.SHUT_WR)
            # Wait for the receiver's EOF: proves the body reached this server,
            # instead of racing a TCP-open/close and claiming a disconnect pass.
            if connection.sock.recv(1):raise TrialError("truncated upload unexpectedly returned HTTP data")
        finally:connection.close()
        state=state_reader(token)
        if state["state"]!="failed" or "truncated" not in state["error"]:
            raise TrialError("interrupted upload was not recorded as truncated")
        status,_=request("POST","/api/cancel",b"",token=token)
        if status!=200:raise TrialError("cannot reset the read-only upload task")
        emit("http_check",case="interrupted-upload",iteration=index+1,passed=True)
    status,data=request("POST","/api/upload",payload,token=token,timeout=100)
    result=json.loads(data)
    if status!=200 or result.get("state")!="ready" or result.get("can_confirm") is not False:
        raise TrialError("complete real firmware did not validate in native RAM")
    state=state_reader(token)
    digest=hashlib.sha256(payload).hexdigest()
    if state["result"]["sha256"]!=digest:raise TrialError("native validator reported the wrong package digest")
    emit("http_check",case="complete-real-firmware",passed=True,bytes=len(payload),sha256=digest)
    # Use the CURRENT three-field protocol: an obsolete/invalid request alone
    # cannot prove that read-only mode rejects an otherwise valid confirmation.
    for path,body in (("/api/confirm",(state["id"]+"\n"+digest+"\n"+state["installed_sha256"]+"\n").encode()),("/api/reboot",b"")):
        status,_=request("POST",path,body,token=token)
        if status!=409:raise TrialError("read-only privileged route was not denied: "+path)
        emit("http_check",case=path,passed=True,http_status=status)
    state_reader(token)


def native_evidence(text):
    hashes={}
    for phase,region,digest in re.findall(r"BW_RAM_HASH (before|repeat|after) (boot|tags) ([0-9a-f]{64})\b",text):
        key=phase+"/"+region
        if key in hashes:raise TrialError("duplicate native NAND hash evidence")
        hashes[key]=digest
    hash_pass=len(hashes)==6 and all(hashes["before/"+region]==hashes["repeat/"+region]==hashes["after/"+region] for region in ("boot","tags"))
    watch=re.findall(r"BW_RAM_WATCHDOG phase=(before|after) controls=([0-9a-f,]+) disabled=([01])",text)
    watch_pass=len(watch)==2 and {w[0] for w in watch}=={"before","after"} and all(
        flag=="1" and len(values.split(","))==4 and not any(int(v,16)&0xffff for v in values.split(","))
        for phase,values,flag in watch) and watch[0][1]==watch[1][1]
    returned=bool(re.search(r"BW_RAM_READONLY_EXIT rc=0 .*rx_restored=1 driver_restored=1 phy_modes_restored=1",text))
    return {"nand_hashes":hashes,"nand_readonly_verified":bool(hash_pass and returned),
            "watchdog_verified":bool(watch_pass and returned),
            "watchdog_mode":"vendor-disabled-readback-only",
            "native_return_pass":bool(returned and hash_pass and watch_pass)}


def main():
    global HTTP_DEADLINE
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspection",type=Path,required=True,help="initial fresh natural-boot report")
    parser.add_argument("--probe-report",type=Path,required=True)
    parser.add_argument("--sample",type=Path)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--port",default="COM3")
    parser.add_argument("--build-dir",type=Path,default=ROOT/".build",help="isolated artifact directory; never replaces a tested build")
    parser.add_argument("--full-backup",action="store_true",help="use the separate RAM-only full physical ECC backup artifact")
    parser.add_argument("--backup-only",action="store_true",help="capture the complete ECC backup and protected repeats; do not add upload/interruption tests")
    parser.add_argument("--confirm-continuous-power",action="store_true",help="operator has paused smart-switch cutoff; requires the separately compiled read-only continuous-backup artifact")
    parser.add_argument("--current-boot",type=Path,help="known installed 2 MiB image reference, checked AGAINST native ECC identity; not a replacement for the new backup")
    parser.add_argument("--current-resident-dir",type=Path,help="exact current resident ELF/manifest for full runtime command-record verification")
    args=parser.parse_args()
    if args.backup_only and not args.full_backup:parser.error("--backup-only requires --full-backup")
    if args.confirm_continuous_power and not args.backup_only:parser.error("continuous-power mode here is only for immutable --backup-only --full-backup")
    if not args.backup_only and not args.sample:parser.error("--sample is required unless performing only the new backup")
    args.output_dir.mkdir(parents=True,exist_ok=False)
    report={"schema":"sk-d840n-native-ram-web-v1","utc":datetime.now(timezone.utc).isoformat(),
            "web_pass":False,"flashable_release":False,"native_write_enabled":False,
            "linux_userspace_used":False,"watchdog_verified":False,"events":[]}
    port=console=hook=None;changed=[];thread=None;execution={};ready=threading.Event();done=threading.Event()
    status=1;started=time.monotonic();lock=threading.Lock();token=secrets.token_hex(32)
    with (args.output_dir/"rx.bin").open("xb") as raw,(args.output_dir/"events.jsonl").open("x",encoding="utf-8",newline="\n") as log:
        tail=bytearray()
        native_lines=bytearray()
        def emit(event,**fields):
            if event=="command":
                fields["text"]=fields["text"].replace(token,"<ephemeral-token>")
            value={"event":event,"elapsed":round(time.monotonic()-started,3),**fields}
            with lock:
                report["events"].append(value);line=json.dumps(value,ensure_ascii=False);log.write(line+"\n");log.flush();print(line,flush=True)
        def record(data):
            raw.write(data);raw.flush();tail.extend(data)
            if b"BW_RAM_NETWORK_STARTED" in tail:ready.set()
            if len(tail)>4096:del tail[:-4096]
            native_lines.extend(data)
            while b"\n" in native_lines:
                line,_,rest=native_lines.partition(b"\n");native_lines[:]=rest
                line=line.strip(b"\r")
                if line.startswith(b"BW_RAM_"):
                    emit("native_progress",text=line.decode("ascii",errors="replace"))
            if len(native_lines)>8192:del native_lines[:-8192]
        try:
            probe=json.loads(args.probe_report.read_text(encoding="utf-8"))
            if not all(probe.get(k) is True for k in ("ram_probe_pass","alias_restored","cache_restored")):
                raise TrialError("minimal RAM probe has not passed with clean return")
            inspection=json.loads(args.inspection.read_text(encoding="utf-8"))
            boot=next(e for e in inspection["events"] if e["event"]=="boot_detected")
            if not inspection.get("inspection_pass") or inspection["events"][-1]["event"]!="inspection_finished":
                raise TrialError("initial inspection did not end at a clean prompt")
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(inspection["utc"])).total_seconds()-boot["elapsed"]+5
            if not 0<=age<(3600 if args.confirm_continuous_power else 12*60):raise TrialError("fresh power window is too short; wait for next observed boot")
            binary=(args.current_boot or ROOT.parents[1]/"sk-d840n-openwrt/binary/boot.bin").read_bytes()
            identity=Core().resident_identity(binary[0x20000:0x180000])
            command_name,alias=command_record(binary,0x9ff3c000,resident_dir=args.current_resident_dir)
            patched=identity.installed_sha256.decode()!=BASE_HASH
            policy=json.loads((args.build_dir/"ram-build-policy.json").read_text(encoding="utf-8"))
            if (policy.get("power_mode")=="continuous_required")!=args.confirm_continuous_power:
                raise TrialError("read-only continuous-backup artifact and operator power confirmation must both be present")
            if args.confirm_continuous_power and (policy.get("max_service_ms")!=1800000 or policy.get("native_bounded_spi_trial") is not True):
                raise TrialError("continuous backup requires the bounded native I/O artifact and exact 30-minute maximum")
            report["power_mode"]="continuous_required" if args.confirm_continuous_power else "periodic20m"
            if patched:
                if policy.get("resident_bootstrap_trial") is not True or policy.get("nand_write_enabled") is not False:
                    raise TrialError("installed resident backup requires the immutable installed-identity-checking payload")
            artifact="bootweb-ram-backup" if args.full_backup else "bootweb-ram-readonly"
            payload=(args.build_dir/(artifact+".bin")).read_bytes()
            elf_bytes=(args.build_dir/(artifact+".elf")).read_bytes()
            elf=elf_info(elf_bytes)
            report["instruction_audit"]=verify_payload(elf_bytes,payload)
            symbols=report["instruction_audit"]["layout_symbols"]
            stack_bottom=int(symbols.get("__ram_stack_bottom","0"),16)
            stack_top=int(symbols.get("__ram_stack_top","0"),16)
            if not 0x89000000<int(symbols["__bss_end"],16)<=stack_bottom<stack_top<=0x89200000 or stack_top-stack_bottom!=65536:
                raise TrialError("private RAM stack is missing or overlaps image/BSS bounds")
            report["private_stack"]={"bottom":hex(stack_bottom),"top":hex(stack_top),"bytes":stack_top-stack_bottom}
            if elf["entry"]!="0x89000000":raise TrialError("wrong RAM image entry")
            if any(not 0x89000000<=int(s["address"],16)<int(s["address"],16)+s["memory_bytes"]<=0x89200000 for s in elf["load_segments"]):
                raise TrialError("ELF memory footprint exceeds reserved RAM image")
            # Independently reject an ordinary unadapted/write-capable artifact.
            if b"RAM ONLY: NAND writes and reboot are compiled out" not in payload or b"BW_RAM_READONLY_ENTRY" not in payload:
                raise TrialError("artifact is not the separately compiled pinned RAM-only service")
            if args.full_backup and b"BWROBK01" not in payload:
                raise TrialError("artifact has no separate physical ECC backup framing")
            sample=args.sample.read_bytes() if args.sample else None
            if sample is not None and not 4096<=len(sample)<=0x6000000:raise TrialError("firmware sample does not fit reserved upload RAM")
            report["artifact"]={"bytes":len(payload),"sha256":hashlib.sha256(payload).hexdigest(),"elf":elf}
            (args.output_dir/"payload.bin").write_bytes(payload)
            (args.output_dir/"payload.elf").write_bytes(elf_bytes)
            port=open_serial(args.port);console=Console(port,emit=emit,raw=record,continuous_power_confirmed=args.confirm_continuous_power)
            console.window.boot();console.window.started-=age+(time.monotonic()-started);console.prompt=True
            _,_,board=console.identify();reloc=field(board,"relocaddr")
            if reloc!=0x9ff3c000 or field(board,"reloc off")!=reloc-0x88000000:raise TrialError("unknown running relocation")
            banks,reserved=runtime_map(board)
            reserved += [Span(0x90000000,0x1000000,"vendor-dma-alias-guard-low"),Span(0x93000000,0x1000000,"vendor-dma-alias-guard-high")]
            image=Span(0x89000000,0x100000 if patched else 0x200000,"bootweb-image");upload=Span(0x8a000000,0x6000000,"http-upload")
            if patched and (stack_top>image.end or any(int(s["address"],16)+s["memory_bytes"]>image.end for s in elf["load_segments"])):
                raise TrialError("backup payload would overwrite the installed dispatcher")
            extra=[Span(0x89200000,0x160000,"installed-identity-workspace")] if patched else []
            if patched:reserved+=[Span(0x89100000,0x100000,"installed-dispatcher-and-stack")]
            validate_placement(banks,reserved,[image,upload,*extra]);report["ram_map"]=[vars(s) for s in [*banks,*reserved,image,upload,*extra]]
            def read_memory(at,n):return parse_md(console.inspect(f"md.b {at:x} {n:x}",timeout=15),at,n)
            for off,n in RAM_PINS:
                if read_memory(reloc+off,n)!=binary[0x20000+off:0x20000+off+n]:raise TrialError("live code pin mismatch: "+hex(off))
            alias_address=reloc+0x96c50
            if read_memory(alias_address,56)!=alias:raise TrialError("unexpected current RAM command record")
            if struct.unpack_from("<Q",read_memory(field(board,"TLB addr"),32),16)[0]!=0x80000711:
                raise TrialError("unrecognized identity memory attributes")
            for cache in ("dcache","icache"):
                if "Cache is ON" not in console.inspect(cache):raise TrialError("unrecognized original cache state")
                changed.append(cache);console._exchange(cache+" off",10)
                if "Cache is OFF" not in console.inspect(cache):raise TrialError("cache disable failed")
            console.loadx(payload,image,banks=banks,reserved=reserved,other_allocations=(upload,*extra))
            def write_pointer(at,data):
                if at!=alias_address+24 or len(data)!=8:raise TrialError("non-alias RAM write rejected")
                console._exchange(f"mw.q {at:x} {struct.unpack('<Q',data)[0]:x} 1",10)
            hook=AliasHook(console,alias_address,alias,read_memory=read_memory,write_pointer=write_pointer)
            hook.install(0x89000000)
            duration=1800000 if args.confirm_continuous_power else min(900000 if args.full_backup else 180000,int((console.window.remaining()-45)*1000))
            if duration<90000:raise TrialError("service cannot fit in remaining power window")
            entered=time.monotonic()
            HTTP_DEADLINE=entered+duration/1000-45
            if not args.confirm_continuous_power:HTTP_DEADLINE=min(HTTP_DEADLINE,console.window.started+17*60-60)
            def execute():
                try:execution["text"]=console._exchange(command_name+" "+token+" "+str(duration),duration/1000+45)
                except Exception as error:execution["error"]=str(error)
                finally:done.set()
            tail.clear();thread=threading.Thread(target=execute,daemon=False);thread.start()
            limit=time.monotonic()+min(75,duration/1000)
            while not ready.wait(.1):
                if done.is_set():raise TrialError("native service returned before network startup: "+execution.get("text",execution.get("error","")))
                if time.monotonic()>=limit:raise TrialError("native NAND audit/network startup exceeded deadline")
            http_limit=time.monotonic()+15
            while True:
                try:
                    state=native_state(token)
                    if state.get("installed_sha256")!=identity.installed_sha256.decode():
                        raise TrialError("live ECC installed identity differs from the supplied reference")
                    if args.confirm_continuous_power and (state.get("power_mode")!="continuous_required" or state.get("can_finish_backup") is not True):
                        raise TrialError("live read-only service does not implement the confirmed continuous-backup policy")
                    report["installed_sha256"]=state["installed_sha256"]
                    break
                except (OSError,ValueError,http.client.HTTPException):
                    if done.is_set() or time.monotonic()>=http_limit:raise
                    time.sleep(.1)
            report["http_ready"]=True;emit("http_ready",url="http://192.168.9.1",mode="native-ram-readonly")
            if args.full_backup:
                backup_deadline=entered+duration/1000-45
                if not args.confirm_continuous_power:backup_deadline=min(backup_deadline,console.window.started+17*60-30)
                def reader(offset,length,timeout):
                    state=native_state(token)
                    if state.get("can_backup") is not True or state.get("backup_format")!="physical-ecc-v1":
                        raise TrialError("live UART-bound peer has no immutable physical ECC backup capability")
                    status,data=request("POST","/api/backup",f"{offset}\n{length}\n".encode(),token=token,timeout=timeout)
                    if status!=200:raise TrialError("native ECC backup request failed; no retry")
                    return data
                backup=download_backup(reader,args.output_dir/"nand-backup",backup_deadline,
                                       emit=lambda **fields:emit(fields.pop("event"),**fields))
                report["full_flash_backup"]=backup["full_flash_backup"]
                report["backup_sha256"]=backup["sha256"]
                report["configuration_exported"]=False
                emit("backup_finished",bytes=backup["bytes"],sha256=backup["sha256"],
                     full_flash_backup=backup["full_flash_backup"],configuration_exported=False)
            if args.backup_only:report["http_checks_skipped"]=True
            else:http_checks(token,sample,emit);report["http_checks_pass"]=True
            if args.backup_only and state.get("can_finish_backup") is True:
                status_code,finished=request("POST","/api/backup-finish",b"",token=token)
                if status_code!=200 or json.loads(finished)!={"finished":True,"reboot":False}:
                    raise TrialError("authenticated backup completion failed; wait for bounded native return")
                report["authenticated_backup_finish"]=True
            emit("backup_capture_finished" if args.backup_only else "http_checks_finished",waiting_for_bounded_native_return=True)
        except (OSError,ValueError,StopIteration,TrialError,http.client.HTTPException,KeyboardInterrupt) as error:
            report["error"]=str(error) or type(error).__name__;emit("ram_web_stopped",reason=report["error"])
        finally:
            if thread:
                # Bounded by the command timeout and the measured power window;
                # never send an interrupt to an unknown stage or cancel writes.
                while thread.is_alive():
                    thread.join(1)
                if "error" in execution:report["execution_error"]=execution["error"]
                text=execution.get("text","");report["native_console"]=text.replace(token,"<ephemeral-token>")
                try:report.update(native_evidence(text))
                except TrialError as error:report["evidence_error"]=str(error);report["native_return_pass"]=False
                emit("native_return",passed=report["native_return_pass"],text=report["native_console"])
            try:
                if hook and hook.armed:hook.restore();report["alias_restored"]=True
                if changed:
                    if not console or not console.identified or not console.prompt:raise TrialError("original synchronized RAM session was lost; no cleanup replay")
                    for cache in reversed(changed):
                        console._exchange(cache+" on",10)
                        if "Cache is ON" not in console.inspect(cache):raise TrialError("cache restoration failed")
                    report["cache_restored"]=True
            except (OSError,TrialError) as error:
                report["cleanup_error"]=str(error);emit("cleanup_failed",reason=str(error))
            report["web_pass"]=all(report.get(k) is True for k in ("http_checks_pass","native_return_pass","alias_restored","cache_restored"))
            report["backup_pass"]=args.full_backup and all(report.get(k) is True for k in ("full_flash_backup","native_return_pass","alias_restored","cache_restored"))
            if report["web_pass"] or (args.backup_only and report["backup_pass"]):status=0
            if port:port.close()
            HTTP_DEADLINE=None
            with (args.output_dir/"report.json").open("x",encoding="utf-8",newline="\n") as out:
                json.dump(report,out,indent=2,ensure_ascii=False);out.write("\n")
    return status


if __name__=="__main__":raise SystemExit(main())
