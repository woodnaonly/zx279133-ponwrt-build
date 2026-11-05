#!/usr/bin/env python3
"""Audited minimal RAM probe; never binds a NAND/network/reset write API.

Requires a fresh natural-boot inspection made by ram_trial.py. Re-inspects
runtime identity, reservations, cmd_tbl, printf/cache code and the identity
translation before uploading. No replay/retry after loss of a console session.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from host.uart import AliasHook, Console, Span, TrialError, open_serial, parse_md, validate_placement
from host.core import Core
from host.resident_runtime import command_record
from build import NATIVE_FLAGS
from audit_ram_elf import audit_elf

BASE_HASH = "5b084490813b540c1a82ddf2e2d4de46080e6cfe47b15b2e6575bdf3fe198fe9"


def clean_natural_anchor(report):
    """Accept only a clean inspection or our fully restored nonwriting trial."""
    events=report.get("events",[])
    if report.get("inspection_pass") is True and events and events[-1].get("event")=="inspection_finished":return True
    return (report.get("installer_preflight_pass") is True and report.get("nand_written") is False and
            report.get("hardware_mutations")==0 and all(report.get(k) is True for k in
            ("ram_probe_pass","native_preflight_pass","alias_restored","cache_restored")))


def field(board,name):
    found=re.search(re.escape(name)+r"\s*=\s*(0x[0-9a-fA-F]+)",board)
    if not found:raise TrialError("missing measured board field: "+name)
    return int(found[1],16)


def runtime_map(board):
    banks=[];reserved=[]
    for kind,out in (("memory",banks),("reserved",reserved)):
        for m in re.finditer(kind+r"\.reg\[(0x[0-9a-fA-F]+)\]\.base\s*=\s*(0x[0-9a-fA-F]+)\s+\.size\s*=\s*(0x[0-9a-fA-F]+)",board):
            out.append(Span(int(m[2],16),int(m[3],16),kind+"-"+m[1]))
    if [(s.start,s.size) for s in banks]!=[(0x80000000,0x20000000)]:
        raise TrialError("unrecognized measured DRAM map")
    if not any(s.start==0x91000000 and s.size==0x2000000 for s in reserved):
        raise TrialError("unrecognized DMA reservation")
    if not any(s.start<=field(board,"sp start")<s.end and s.end==0xa0000000 for s in reserved):
        raise TrialError("stack/monitor reservation missing")
    reserved += [Span(0x82000000,0x2000000,"multi-dtb-conservative"),
                 Span(0x9e000000,0x16ee080,"stack-heap-conservative")]
    return banks,reserved


def relocated_record(data,delta):
    result=bytearray(data)
    for at in (0,16,24,32,40,48):
        value=struct.unpack_from("<Q",result,at)[0]
        if value:struct.pack_into("<Q",result,at,value+delta)
    return bytes(result)


def build_probe(output,binary,reloc,address,*,expected_record=None,command_name="?"):
    zig=ROOT/".build/toolchain/zig-x86_64-windows-0.14.1/zig.exe"
    if command_name not in ("?","bootweb"):
        raise TrialError("probe command is not a verified audited entry")
    alias=expected_record or relocated_record(binary[0xb6c50:0xb6c88],reloc-0x88000000)
    if len(alias)!=56:
        raise TrialError("probe requires the complete current command record")
    definitions={"BW_PROBE_PRINTF":reloc+0x6677c,"BW_PROBE_COMMAND":reloc+0x96c50,
                 "BW_PROBE_ORIGINAL":struct.unpack_from("<Q",alias,24)[0],"BW_PROBE_RELOC":reloc}
    script=output/"probe.ld"
    script.write_text("ENTRY(bw_ram_probe)\nSECTIONS { . = "+hex(address)+
                      "; .text : { KEEP(*(.text.entry)) *(.text .text.*) } .rodata : { *(.rodata .rodata.*) } "
                      ".data : { *(.data .data.*) } .bss (NOLOAD) : { *(.bss .bss.*) } "
                      "/DISCARD/ : { *(.comment) *(.eh_frame*) } }\n",encoding="utf-8",newline="\n")
    elf=output/"ram-probe.elf"
    command=[str(zig),"cc",*NATIVE_FLAGS,"-std=c11","-Os","-Wall","-Wextra","-Werror","-fno-builtin",
             '-DBW_PROBE_COMMAND_NAME="'+command_name+'"',
             *["-D"+k+"="+hex(v)+"ULL" for k,v in definitions.items()],str(ROOT/"adapter/ram_probe.c"),
             "-Wl,-T,"+str(script),"-Wl,-e,bw_ram_probe","-o",str(elf)]
    subprocess.run(command,check=True)
    (output/"instructions.json").write_text(json.dumps(audit_elf(elf.read_bytes()),indent=2)+"\n",encoding="utf-8")
    path=output/"ram-probe.bin"
    subprocess.run([str(zig),"objcopy","--output-target","binary",str(elf),str(path)],check=True)
    return path.read_bytes()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspection",type=Path,required=True,help="initial NATURAL boot report, not a reinspection")
    parser.add_argument("--boot-dump",type=Path,default=ROOT.parents[1]/"sk-d840n-openwrt/binary/boot.bin")
    parser.add_argument("--current-resident-dir",type=Path,help="exact current resident ELF/manifest when probing a patched installed command")
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--port",default="COM3")
    args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=False)
    report={"schema":"sk-d840n-ram-probe-v1","utc":datetime.now(timezone.utc).isoformat(),
            "ram_probe_pass":False,"flashable_release":False,"nand_written":False,
            "alias_restored":False,"cache_restored":False,"events":[]}
    port=console=hook=None;cache_changed=[];status=1;started=time.monotonic()
    with (args.output_dir/"rx.bin").open("xb") as raw, (args.output_dir/"events.jsonl").open("x",encoding="utf-8",newline="\n") as log:
        def emit(event,**fields):
            item={"event":event,"elapsed":round(time.monotonic()-started,3),**fields};report["events"].append(item)
            line=json.dumps(item,ensure_ascii=False);log.write(line+"\n");log.flush();print(line,flush=True)
        def record_raw(data):raw.write(data);raw.flush()
        try:
            old=json.loads(args.inspection.read_text(encoding="utf-8"))
            boot=next(e for e in old["events"] if e["event"]=="boot_detected")
            if not old.get("inspection_pass") or old["events"][-1]["event"]!="inspection_finished":
                raise TrialError("inspection did not close at a known clean prompt")
            boot_age=(datetime.now(timezone.utc)-datetime.fromisoformat(old["utc"])).total_seconds()-boot["elapsed"]+5
            if not 0<=boot_age<13*60:raise TrialError("insufficient fresh-boot time for a probe; await next cycle")
            binary=args.boot_dump.read_bytes()
            if len(binary)!=0x200000:raise TrialError("wrong current BOOT reference length")
            identity=Core().resident_identity(binary[0x20000:0x180000])
            command_name,alias=command_record(binary,0x9ff3c000,resident_dir=args.current_resident_dir)
            report["installed_reference_sha256"]=identity.installed_sha256.decode()
            old_board=next(e["bdinfo"] for e in old["events"] if e["event"]=="identity_inspected")
            reloc=field(old_board,"relocaddr");address=0x89000000
            payload=build_probe(args.output_dir,binary,reloc,address,expected_record=alias,command_name=command_name)
            report["payload"]={"address":hex(address),"bytes":len(payload),"sha256":hashlib.sha256(payload).hexdigest()}
            port=open_serial(args.port);console=Console(port,emit=emit,raw=record_raw)
            console.window.boot();console.window.started-=boot_age+(time.monotonic()-started)
            console.prompt=True
            _,_,board=console.identify()
            if field(board,"relocaddr")!=reloc or field(board,"reloc off")!=reloc-0x88000000:
                raise TrialError("runtime relocation changed; discard compiled payload")
            banks,reserved=runtime_map(board);allocation=Span(address,0x10000,"probe")
            validate_placement(banks,reserved,[allocation]);report["ram_map"]=[vars(s) for s in [*banks,*reserved,allocation]]
            def read_memory(at,n):return parse_md(console.inspect(f"md.b {at:x} {n:x}",timeout=15),at,n)
            alias_address=reloc+0x96c50
            for off,n in ((0x12c7c,64),(0x6677c,128),(0x1100,0x800),(0x286c,0x1a0)):
                if read_memory(reloc+off,n)!=binary[0x20000+off:0x20000+off+n]:
                    raise TrialError("runtime instruction fingerprint mismatch: "+hex(off))
            if read_memory(alias_address,56)!=alias:raise TrialError("RAM alias differs from pinned cmd_rep table")
            root=read_memory(field(board,"TLB addr"),32)
            # Measured L1 identity normal-memory block: AF, RW, PXN/UXN clear.
            if struct.unpack_from("<Q",root,16)[0]!=0x80000711:
                raise TrialError("unrecognized DRAM identity/execution mapping")
            cache_original={}
            for cache in ("dcache","icache"):
                text=console.inspect(cache)
                if "Cache is ON" not in text:raise TrialError("unrecognized initial cache state")
                cache_original[cache]=True
            emit("runtime_fingerprints_verified",relocaddr=hex(reloc),alias=hex(alias_address),
                 identity_mapping=True,ram_placement_verified=True)
            for cache in ("dcache","icache"):
                cache_changed.append(cache) # restoration required even if reply is lost
                console._exchange(cache+" off",10)
                if "Cache is OFF" not in console.inspect(cache):raise TrialError("cache did not disable")
            console.loadx(payload,allocation,banks=banks,reserved=reserved)
            def write_pointer(at,data):
                if at!=alias_address+24 or len(data)!=8:raise TrialError("non-alias RAM write rejected")
                console._exchange(f"mw.q {at:x} {struct.unpack('<Q',data)[0]:x} 1",10)
            hook=AliasHook(console,alias_address,alias,read_memory=read_memory,write_pointer=write_pointer)
            hook.install(address)
            result=console._exchange(command_name,15)
            emit("probe_return",result=result)
            if "BW_RAM_PROBE v1 " not in result or "argc=1 RETURNING" not in result:
                raise TrialError("probe did not report a successful command ABI return")
            hook.restore();report["alias_restored"]=True
            report["ram_probe_pass"]=True;status=0
        except (OSError,ValueError,StopIteration,TrialError,subprocess.SubprocessError,KeyboardInterrupt) as error:
            report["error"]=str(error) or type(error).__name__;emit("probe_stopped",reason=report["error"])
        finally:
            try:
                if hook and hook.armed:hook.restore();report["alias_restored"]=True
                if cache_changed:
                    if not console or not console.identified or not console.prompt:
                        raise TrialError("cannot restore caches without the original synchronized session")
                    for cache in reversed(cache_changed):
                        console._exchange(cache+" on",10)
                        if "Cache is ON" not in console.inspect(cache):raise TrialError("cache restoration failed")
                    report["cache_restored"]=True
                if report["ram_probe_pass"] and not (report["alias_restored"] and report["cache_restored"]):
                    raise TrialError("RAM cleanup incomplete")
            except (TrialError,OSError) as error:
                status=1;report["ram_probe_pass"]=False;report["cleanup_error"]=str(error);emit("cleanup_failed",reason=str(error))
            if port:port.close()
            with (args.output_dir/"report.json").open("x",encoding="utf-8",newline="\n") as out:
                json.dump(report,out,indent=2,ensure_ascii=False);out.write("\n")
    return status


if __name__=="__main__":raise SystemExit(main())
