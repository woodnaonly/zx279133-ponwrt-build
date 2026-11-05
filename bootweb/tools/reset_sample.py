#!/usr/bin/env python3
"""Physical Reset sampling with an optional audited volatile mux trial.

No GPIO request/direction/output writes; no reboot or environment changes.
The optional mux field is saved, read-back checked and restored in this cycle.
One exclusive serial owner. A trace without release/3s press/release fails.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from host.uart import Console,TrialError,open_serial,parse_md,parse_md_words
from ram_probe import field
from host.reset import PINCTRL_CODE_PINS,reset_mapping,reset_pinctrl_mapping

def evaluate(samples):
    samples=list(samples)
    while samples and samples[0][1]!=1:samples.pop(0)
    if not samples:return False
    pressed=None;held=False;previous=samples[0][0]
    for stamp,value in samples:
        if stamp<previous or stamp-previous>.25:return False
        previous=stamp
        if value==0 and pressed is None:pressed=stamp
        if value==0 and pressed is not None and stamp-pressed>=3:held=True
        if value==1 and held:return True
        if value==1:pressed=None
    return False

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inspection",type=Path,required=True);p.add_argument("--clean-trial",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--port",default="COM3")
    p.add_argument("--seconds",type=int,default=120)
    p.add_argument("--diagnose-only",action="store_true",help="only read the GPIO register bank; no sampling and no writes")
    p.add_argument("--configure-pinctrl",action="store_true",help="temporarily select the exact audited pin14 GPIO mux field; restore before returning")
    a=p.parse_args()
    if not 10<=a.seconds<=180:p.error("sampling is bounded to 10..180 seconds")
    if a.configure_pinctrl and a.diagnose_only:p.error("diagnose-only never modifies pinctrl")
    a.output_dir.mkdir(parents=True,exist_ok=False);port=console=None;pinmux_saved=None;start=time.monotonic();rc=1
    report={"schema":"sk-d840n-reset-physical-readonly-v1","utc":datetime.now(timezone.utc).isoformat(),
            "nand_written":False,"gpio_written":False,"mmio_access_width":32,"register":"0x10d10014","bit":0,"active_low":True,"samples":[]}
    if a.configure_pinctrl:report["schema"]="sk-d840n-reset-physical-pinctrl-v2"
    with (a.output_dir/"rx.bin").open("xb") as raw,(a.output_dir/"events.jsonl").open("x",encoding="utf-8",newline="\n") as log:
        def emit(event,**fields):
            item={"event":event,"elapsed":round(time.monotonic()-start,4),**fields}
            line=json.dumps(item,ensure_ascii=False);log.write(line+"\n");log.flush()
            if event!="command":print(line,flush=True)
        try:
            old=json.loads(a.inspection.read_text(encoding="utf-8"));clean=json.loads(a.clean_trial.read_text(encoding="utf-8"))
            clean_web=all(clean.get(k) is True for k in ("native_return_pass","bootstrap_return_pass","alias_restored","cache_restored"))
            clean_installer=all(clean.get(k) is True for k in ("installer_preflight_pass","alias_restored","cache_restored"))
            if not (old.get("inspection_pass") or old.get("installer_preflight_pass")) or not (clean_web or clean_installer):
                raise TrialError("original session did not return with complete native/cache/alias restoration")
            boot=next(e for e in old["events"] if e["event"]=="boot_detected")
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(old["utc"])).total_seconds()-boot["elapsed"]+5
            if not 0<=age<(16 if a.diagnose_only else 12)*60 or age+(30 if a.diagnose_only else a.seconds)>=17*60-30:raise TrialError("not enough time in this natural power cycle")
            source=(ROOT.parents[1]/"sk-d840n-openwrt/binary/boot.bin").read_bytes()
            port=open_serial(a.port);console=Console(port,emit=emit,raw=lambda data:(raw.write(data),raw.flush()))
            console.window.boot();console.window.started-=age+time.monotonic()-start;console.prompt=True
            _,_,board=console.identify()
            if field(board,"relocaddr")!=0x9ff3c000:raise TrialError("relocation differs")
            if "control_dtb" in old.get("memory_blocks",{}):dtb=bytes.fromhex(old["memory_blocks"]["control_dtb"]["hex"])
            else:
                at=field(board,"fdt_blob")
                if not 0x9f6ee080<=at<0x9fff0000:raise TrialError("control DTB is not in the measured monitor reservation")
                header=parse_md(console.inspect(f"md.b {at:x} 28"),at,40)
                magic,length=struct.unpack_from(">II",header)
                if magic!=0xd00dfeed or not 40<=length<=0x10000 or at+length>0xa0000000:raise TrialError("invalid live control DTB")
                dtb=parse_md(console.inspect(f"md.b {at:x} {length:x}",timeout=45),at,length)
            report["reset_mapping"]=reset_mapping(dtb)
            report["control_dtb_sha256"]=hashlib.sha256(dtb).hexdigest()
            pin=parse_md(console.inspect("md.b 9ff89d24 40"),0x9ff89d24,64)
            if pin!=source[0x20000+0x4dd24:0x20000+0x4dd64]:raise TrialError("native read-input GPIO instructions differ")
            bank=parse_md_words(console.inspect("md.l 10d10000 8"),0x10d10000,8)
            report["gpio_registers"]={hex(off):hex(struct.unpack_from("<I",bank,off)[0]) for off in range(0,32,4)}
            emit("gpio_registers",values=report["gpio_registers"],direction_bit0_is_output=bool(struct.unpack_from("<I",bank)[0]&1))
            pinmux=parse_md_words(console.inspect("md.l 10e20000 20"),0x10e20000,32)
            report["pinctrl_registers"]={hex(off):hex(struct.unpack_from("<I",pinmux,off)[0]) for off in range(0,128,4)}
            emit("pinctrl_registers",values=report["pinctrl_registers"],writes=0)
            if a.diagnose_only:return 0
            if struct.unpack_from("<I",bank)[0]&1:
                raise TrialError("Reset GPIO is an output; refusing mux changes or button sampling")
            if a.configure_pinctrl:
                report["pinctrl_configuration"]=reset_pinctrl_mapping(dtb)
                for off,n in PINCTRL_CODE_PINS:
                    code=parse_md(console.inspect(f"md.b {0x9ff3c000+off:x} {n:x}"),0x9ff3c000+off,n)
                    if code!=source[0x20000+off:0x20000+off+n]:raise TrialError("native pinctrl code differs: "+hex(off))
                report["pinctrl_runtime_code_verified"]=True
                pinmux_saved=struct.unpack_from("<I",pinmux)[0]
                configured=(pinmux_saved&~0x60000000)|0x20000000
                report["pinctrl_before"]=pinmux_saved
                console.window.require(a.seconds+30)
                console._exchange(f"mw.l 10e20000 {configured:x} 1",10)
                observed=struct.unpack("<I",parse_md_words(console.inspect("md.l 10e20000 1"),0x10e20000,1))[0]
                report["pinctrl_configured"]=observed
                if observed!=configured:raise TrialError("volatile Reset mux configuration did not read back exactly")
                emit("pinctrl_configured",register="0x10e20000",global_pin=14,mask="0x60000000",before=hex(pinmux_saved),after=hex(observed),nand_writes=0)
            emit("physical_sampling_ready",instruction="Release Reset, hold Reset >=3 seconds, then release",register="0x10d10014",gpio_writes=0)
            deadline=time.monotonic()+a.seconds;last=None
            while time.monotonic()<deadline:
                value=struct.unpack("<I",parse_md_words(console.inspect("md.l 10d10014 1"),0x10d10014,1))[0]
                sample=[round(time.monotonic()-start,4),value&1];report["samples"].append(sample)
                if sample[1]!=last:emit("reset_input_transition",raw_bit=sample[1],pressed=sample[1]==0,full_register=hex(value));last=sample[1]
                if evaluate(report["samples"]):report["physical_reset_pass"]=True;rc=0;break
                time.sleep(.05)
            report.setdefault("physical_reset_pass",False)
            emit("sampling_finished",physical_reset_pass=report["physical_reset_pass"],samples=len(report["samples"]))
        except (Exception,KeyboardInterrupt) as error:report["error"]=str(error);emit("sampling_stopped",reason=str(error))
        finally:
            if pinmux_saved is not None and console:
                try:
                    console.window.require(20)
                    console._exchange(f"mw.l 10e20000 {pinmux_saved:x} 1",10)
                    after=struct.unpack("<I",parse_md_words(console.inspect("md.l 10e20000 1"),0x10e20000,1))[0]
                    report["pinctrl_after"]=after;report["pinctrl_restored"]=after==pinmux_saved
                    if after!=pinmux_saved:raise TrialError("Reset mux restoration did not read back exactly")
                    emit("pinctrl_restored",register="0x10e20000",value=hex(after))
                except Exception as error:
                    report["physical_reset_pass"]=False;report["pinctrl_restored"]=False
                    report["cleanup_error"]=str(error);rc=1;emit("pinctrl_restore_failed",reason=str(error),no_replay=True)
            if port:port.close()
            (a.output_dir/"report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return rc

if __name__=="__main__":raise SystemExit(main())
