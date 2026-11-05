#!/usr/bin/env python3
"""Read-only live control-DTB and SPI callback-chain export, one UART owner."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from host.uart import Console,TrialError,open_serial,parse_md
from ram_probe import field


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspection",type=Path,required=True)
    parser.add_argument("--clean-trial",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--port",default="COM3")
    args=parser.parse_args();args.output_dir.mkdir(parents=True,exist_ok=False)
    report={"schema":"sk-d840n-native-chain-inspection-v1","utc":datetime.now(timezone.utc).isoformat(),
            "nand_written":False,"events":[],"memory_blocks":{}}
    port=None;start=time.monotonic();status=1
    with (args.output_dir/"rx.bin").open("xb") as raw,(args.output_dir/"events.jsonl").open("x",encoding="utf-8",newline="\n") as log:
        def emit(event,**fields):
            item={"event":event,"elapsed":round(time.monotonic()-start,3),**fields};report["events"].append(item)
            line=json.dumps(item);log.write(line+"\n");log.flush();print(line,flush=True)
        try:
            old=json.loads(args.inspection.read_text(encoding="utf-8"));clean=json.loads(args.clean_trial.read_text(encoding="utf-8"))
            if not old.get("inspection_pass") or not clean.get("resident_ram_trial_pass"):raise TrialError("previous prompt is not a clean completed trial")
            boot=next(e for e in old["events"] if e["event"]=="boot_detected")
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(old["utc"])).total_seconds()-boot["elapsed"]+5
            if not 0<=age<15*60:raise TrialError("natural cycle too old for read-only chain inspection")
            port=open_serial(args.port);console=Console(port,emit=emit,raw=lambda data:(raw.write(data),raw.flush()))
            console.window.boot();console.window.started-=age+time.monotonic()-start;console.prompt=True
            _,_,board=console.identify();reloc=field(board,"relocaddr")
            if reloc!=0x9ff3c000 or field(board,"reloc off")!=0x17f3c000:raise TrialError("unknown relocation")
            def read(name,at,n,timeout=15):
                if not 0x9f6ee080<=at<at+n<=0xa0000000:raise TrialError("structure pointer leaves measured resident reservation: "+name)
                data=parse_md(console.inspect(f"md.b {at:x} {n:x}",timeout=timeout),at,n)
                report["memory_blocks"][name]={"address":hex(at),"bytes":n,"hex":data.hex(),"sha256":hashlib.sha256(data).hexdigest()}
                emit("structure_read",name=name,address=hex(at),bytes=n);return data
            def pointer(data,off):return struct.unpack_from("<Q",data,off)[0]
            slot=read("mtd_slot",reloc+0xac580,8);mtd=read("mtd",pointer(slot,0),512)
            nand=read("spinand",pointer(mtd,312),256)
            spi_mem=read("spi_mem",pointer(nand,80),64)
            device=read("spi_device",pointer(spi_mem,0),192)
            parent=read("spi_parent",pointer(device,56),192)
            read("spi_private",pointer(parent,64),0xa00)
            driver=read("spi_driver",pointer(parent,0),128)
            ops=read("spi_ops",pointer(driver,104),64)
            if pointer(ops,32):read("spi_mem_ops",pointer(ops,32),64)
            fdt=field(board,"fdt_blob");head=read("control_dtb_header",fdt,40)
            magic,n=struct.unpack_from(">II",head)
            if magic!=0xd00dfeed or not 40<=n<=0x10000:raise TrialError("invalid runtime control DTB")
            data=read("control_dtb",fdt,n,timeout=45)
            (args.output_dir/"control.dtb").write_bytes(data)
            report["inspection_pass"]=True;status=0;emit("inspection_finished",no_mutations=True)
        except (OSError,TrialError,ValueError,KeyboardInterrupt) as error:
            report["error"]=str(error);emit("inspection_stopped",reason=str(error))
        finally:
            if port:port.close()
            (args.output_dir/"report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    return status


if __name__=="__main__":raise SystemExit(main())
