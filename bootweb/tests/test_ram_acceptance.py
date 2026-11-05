"""Evidence aggregation never treats a partial/reused boot as two successful cycles."""
import copy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from ram_acceptance import check_cycle,check_cycles


def fixtures():
    start=datetime(2026,11,3,14,tzinfo=timezone.utc)
    inspection={"utc":start.isoformat(),"inspection_pass":True,"events":[
        {"event":"natural_cycle_guard","elapsed":0,"seconds_until_earliest_boot":7},
        {"event":"boot_detected","elapsed":10},{"event":"inspection_finished","elapsed":11}]}
    probe={"utc":(start+timedelta(seconds=12)).isoformat(),"ram_probe_pass":True,"alias_restored":True,"cache_restored":True}
    console="\n".join([f"BW_RAM_HASH {phase} {region} "+("a" if region=="boot" else "b")*64
        for phase in ("before","repeat","after") for region in ("boot","tags")]+[
        f"BW_RAM_WATCHDOG phase={phase} controls=0,0,0,0 disabled=1" for phase in ("before","after")]+[
        "BW_RAM_READONLY_EXIT rc=0 rx_restored=1 driver_restored=1 phy_modes_restored=1"])
    web={k:True for k in ("web_pass","http_ready","http_checks_pass","native_return_pass","alias_restored",
                          "cache_restored","nand_readonly_verified","watchdog_verified")}
    web.update({k:False for k in ("flashable_release","native_write_enabled","linux_userspace_used")})
    cases=[{"event":"http_check","elapsed":30,"case":case,"passed":True} for case in
           ("identity-and-write-lock","embedded-page","invalid-origin-and-package","complete-real-firmware")]
    cases[-1].update(bytes=12431637,sha256="c"*64)
    cases += [{"event":"http_check","elapsed":30,"case":"interrupted-upload","iteration":i,"passed":True} for i in range(1,11)]
    cases += [{"event":"http_check","elapsed":30,"case":path,"http_status":409,"passed":True} for path in ("/api/confirm","/api/reboot")]
    web.update(utc=(start+timedelta(seconds=20)).isoformat(),native_console=console,
               events=[{"event":"native_progress","elapsed":5,"text":"BW_RAM_READONLY_ENTRY duration_ms=180000 caps=0 writes_compiled_out=1"},
                       *cases,{"event":"ram_alias_restored","elapsed":210}],artifact={"sha256":"d"*64},
               instruction_audit={"elf_sha256":"e"*64},ram_map=[
                   {"start":0x80000000,"size":0x20000000,"name":"memory-0x0"},
                   {"start":0x91000000,"size":0x2000000,"name":"reserved-0x0"},
                   {"start":0x89000000,"size":0x200000,"name":"bootweb-image"},
                   {"start":0x8a000000,"size":0x6000000,"name":"http-upload"}],
               private_stack={"bottom":"0x8909ee40","top":"0x890aee40","bytes":65536})
    return inspection,probe,web


class RamAcceptanceTests(unittest.TestCase):
    def test_two_distinct_same_build_natural_cycles_are_required(self):
        cycle=check_cycle(*fixtures())
        later=copy.deepcopy(cycle)
        later["boot_utc"]=(datetime.fromisoformat(cycle["boot_utc"])+timedelta(seconds=1230)).isoformat()
        self.assertEqual(len(check_cycles([later,cycle])),2)
        for invalid in ([cycle],[cycle,cycle]):
            with self.assertRaises(ValueError):check_cycles(invalid)
        for key in ("payload_sha256","elf_sha256","firmware_sha256","nand_hashes"):
            changed=copy.deepcopy(later);changed[key]="different"
            with self.assertRaises(ValueError):check_cycles([cycle,changed])

    def test_missing_interruption_and_non_http_denial_fail(self):
        for case in ("interrupted-upload","/api/confirm","/api/reboot","complete-real-firmware"):
            inspection,probe,web=fixtures()
            web["events"].remove(next(e for e in web["events"] if e.get("case")==case))
            with self.assertRaises(ValueError):check_cycle(inspection,probe,web)

    def test_stale_probe_early_reset_and_late_cleanup_fail(self):
        inspection,probe,web=fixtures()
        probe["utc"]="2026-11-03T13:00:00+00:00"
        with self.assertRaises(ValueError):check_cycle(inspection,probe,web)
        inspection,probe,web=fixtures();inspection["events"][1]["elapsed"]=6
        with self.assertRaises(ValueError):check_cycle(inspection,probe,web)
        inspection,probe,web=fixtures();web["events"][-1]["elapsed"]=1080
        with self.assertRaises(ValueError):check_cycle(inspection,probe,web)
        inspection,probe,web=fixtures();web["events"][0]["elapsed"]=1000
        with self.assertRaises(ValueError):check_cycle(inspection,probe,web)

    def test_partial_native_cleanup_or_flash_flags_cannot_pass(self):
        for key in ("web_pass","alias_restored","cache_restored","nand_readonly_verified","watchdog_verified"):
            inspection,probe,web=fixtures();web[key]=False
            with self.assertRaises(ValueError):check_cycle(inspection,probe,web)
        inspection,probe,web=fixtures();web["native_write_enabled"]=True
        with self.assertRaises(ValueError):check_cycle(inspection,probe,web)

    def test_missing_memory_evidence_and_stack_overlap_fail(self):
        for change in (lambda w:w.update(ram_map=[]),
                       lambda w:w["ram_map"][1].update(start=0x89000000),
                       lambda w:w["private_stack"].update(top="0x8a000000")):
            inspection,probe,web=fixtures();change(web)
            with self.assertRaises((ValueError,RuntimeError)):check_cycle(inspection,probe,web)
        inspection,probe,web=fixtures();web["native_console"]=web["native_console"].replace("driver_restored=1","driver_restored=0")
        with self.assertRaises(ValueError):check_cycle(inspection,probe,web)


if __name__=="__main__":unittest.main(verbosity=2)
