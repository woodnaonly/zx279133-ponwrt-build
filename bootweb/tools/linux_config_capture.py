#!/usr/bin/env python3
"""Recover a failed local export from THIS known, already-booted Linux console.

No old U-Boot commands, reboot, network reconfiguration or NAND writes.
Creates a config archive in /tmp and transfers it through the same UART.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from host.uart import Console,TrialError,open_serial
from resident_cold_trial import CONFIG_EXPORT_COMMAND,decode_config_export,terminal_plain

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("inspection","cold-report","output-dir"):p.add_argument("--"+k,type=Path,required=True)
    p.add_argument("--port",default="COM3");a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=False)
    report={"schema":"sk-d840n-current-linux-config-export-v1","utc":datetime.now(timezone.utc).isoformat(),"nand_written":False,
            "reboot_requested":False,"events":[]};port=None;start=time.monotonic();status=1
    with (a.output_dir/"rx.bin").open("xb") as raw,(a.output_dir/"events.jsonl").open("x",encoding="utf-8",newline="\n") as log:
        def emit(event,**fields):
            item={"event":event,"elapsed":round(time.monotonic()-start,3),**fields};report["events"].append(item)
            line=json.dumps(item,ensure_ascii=False);log.write(line+"\n");log.flush();print(line,flush=True)
        try:
            old=json.loads(a.inspection.read_text(encoding="utf-8"));cold=json.loads(a.cold_report.read_text(encoding="utf-8"))
            if not old.get("inspection_pass") or not cold.get("normal_openwrt_boot_pass") or not cold.get("linux_transition_started"):
                raise TrialError("this exact natural cycle has no recognized Linux transition")
            boot=next(e for e in old["events"] if e["event"]=="boot_detected")
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(old["utc"])).total_seconds()-boot["elapsed"]+5
            if not 0<=age<14*60:raise TrialError("insufficient known-cycle time for a config export")
            port=open_serial(a.port);console=Console(port,raw=lambda data:(raw.write(data),raw.flush()))
            # The previous tool reached a real root prompt in this generation;
            # send only an empty line, then demand a FRESH root prompt. No old
            # commands or guessed login are sent if Linux rebooted meanwhile.
            console.write(b"\r");tail=bytearray();end=time.monotonic()+5
            while time.monotonic()<end:
                tail.extend(console.read())
                if Console.BOOT.search(tail):raise TrialError("new U-Boot banner; Linux generation ended")
                if re.search(rb"(?:^|[\r\n])root@[^\r\n]*:[^\r\n]*# ?$",terminal_plain(tail)):break
            else:raise TrialError("current console did not prove a fresh Linux root prompt")
            emit("current_linux_prompt_verified",prior_natural_boot_age_seconds=round(age,3))
            console.write(CONFIG_EXPORT_COMMAND.encode("ascii")+b"\r");data=bytearray();end=min(time.monotonic()+120,start+17*60-age)
            while time.monotonic()<end:
                data.extend(console.read())
                if len(data)>0x500000:raise TrialError("configuration export exceeds serial bound")
                if re.search(rb"(?:^|[\r\n])BW_CONFIG_END\r?\r?\n",data):break
            else:raise TrialError("configuration transfer timed out; no automatic retry")
            archive,entries=decode_config_export(bytes(data));(a.output_dir/"current-config.tar.gz").write_bytes(archive)
            report["configuration_export"]={"verified":True,"bytes":len(archive),"sha256":hashlib.sha256(archive).hexdigest(),"entries":entries}
            report["configuration_export_pass"]=True;status=0;emit("configuration_exported",bytes=len(archive),sha256=hashlib.sha256(archive).hexdigest())
        except (Exception,KeyboardInterrupt) as error:report["error"]=str(error);emit("configuration_export_stopped",reason=str(error))
        finally:
            if port:port.close()
            (a.output_dir/"report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return status

if __name__=="__main__":raise SystemExit(main())
