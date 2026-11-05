"""Synthetic build-gate tests. No device, real image or write authorization."""
import copy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"tools"))
from build_installer import real_evidence,native241_request_evidence
from host.reset import CONTROL_DTB_SHA256


class InstallerBuildGateTests(unittest.TestCase):
    def native241_inputs(self):
        digest="d"*64
        manifest={"installed_sha256":"c"*64,"source_installed_sha256":"a"*64,"native241_service":True,
                  "native_image_format_offline_pass":True,"static_space_pass":True,"reset_cold_entry_enabled":True,
                  "device_entropy_trial":True,"payload_build_policy":{"bounded_io_enforced":True,"layout":"native241",
                  "power_mode":"continuous_required","web_uboot_enabled":False,"timing_ledger":{"TIMING_PROVEN":0}}}
        request={"schema":"sk-d840n-native241-install-request-v1","operation":"update-resident-native241-then-web-install-337",
                 "resident_manifest_sha256":digest,"candidate_sha256":"c"*64,"source_installed_sha256":"a"*64,
                 "permitted_physical_write":["0x20000","0x180000"],"continuous_power_required":True,
                 "web_uboot_enabled":False,"system_package_sha256":"f92c1ff53d8cf7c64eabc97122eeda316228d787b0aa448638670df88603cca2"}
        return manifest,digest,request

    def test_native241_request_is_new_exact_bound_and_not_hardware_acceptance(self):
        result=native241_request_evidence(*self.native241_inputs())
        self.assertFalse(result["prior_authorization_reused"]);self.assertFalse(result["hardware_acceptance_complete"])
        self.assertFalse(result["rescue_verified"]);self.assertTrue(result["power_confirmation_at_execution_required"])
        for key,value in (("schema","sk-d840n-user-directed-install-v1"),("candidate_sha256","e"*64),
                          ("source_installed_sha256","e"*64),("resident_manifest_sha256","e"*64),
                          ("continuous_power_required",False),("web_uboot_enabled",True),
                          ("permitted_physical_write",["0x0","0x200000"]),("system_package_sha256","e"*64)):
            m,d,r=self.native241_inputs();r[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):native241_request_evidence(m,d,r)
        m,d,r=self.native241_inputs();m["payload_build_policy"]["timing_ledger"]["TIMING_PROVEN"]=1
        with self.assertRaises(ValueError):native241_request_evidence(m,d,r)

    def inputs(self):
        digest="d"*64;candidate="c"*64
        manifest={"resident_service_write_capable":True,"installed_sha256":candidate}
        ram=[{"resident_ram_trial_pass":True,"build_manifest_sha256":digest,"natural_boot_utc":stamp}
             for stamp in ("2026-11-04T01:00:00+00:00","2026-11-04T01:20:30+00:00")]
        cold={"normal_openwrt_boot_pass":True,"build_manifest_sha256":digest,"reset_web_entry_pass":True,"serial_bootweb_entry_pass":True}
        reset={"schema":"sk-d840n-reset-physical-readonly-v1","physical_reset_pass":True,"gpio_written":False,
               "mmio_access_width":32,
               "nand_written":False,"control_dtb_sha256":CONTROL_DTB_SHA256,"gpio_registers":{"0x0":"0x2600"},
               "reset_mapping":{"register":"0x10d10014","bit":0,"active_low":True},
               "samples":[[0,1]]+[[i/10,0] for i in range(1,33)]+[[3.3,1]]}
        preflight={"installer_preflight_pass":True,"candidate_sha256":candidate}
        timing={"bounded_native_nand_pass":True,"candidate_sha256":candidate}
        return [manifest,digest,ram,cold,reset,preflight,timing]

    def test_rescue_waiver_does_not_fabricate_or_require_rescue_verification(self):
        value=real_evidence(*self.inputs())
        self.assertTrue(value["rescue_waived"]);self.assertFalse(value["rescue_verified"])

    def test_readonly_build_is_not_final_even_after_other_synthetic_gates(self):
        args=self.inputs();args[0]["resident_service_write_capable"]=False
        with self.assertRaisesRegex(ValueError,"not the final"):real_evidence(*args)

    def test_exact_build_two_natural_cycles_cold_and_physical_reset_required(self):
        faults=(lambda a:a[2].pop(),lambda a:a[2][1].update(natural_boot_utc=a[2][0]["natural_boot_utc"]),
                lambda a:a[2][1].update(build_manifest_sha256="e"*64),
                lambda a:a[3].update(normal_openwrt_boot_pass=False),
                lambda a:a[3].update(reset_web_entry_pass=False),
                lambda a:a[3].update(serial_bootweb_entry_pass=False),
                lambda a:a[4].update(samples=[]),
                lambda a:a[5].update(candidate_sha256="e"*64),
                lambda a:a[6].update(bounded_native_nand_pass=False))
        for fault in faults:
            args=self.inputs();fault(args)
            with self.assertRaises(ValueError):real_evidence(*args)


if __name__=="__main__":unittest.main()
