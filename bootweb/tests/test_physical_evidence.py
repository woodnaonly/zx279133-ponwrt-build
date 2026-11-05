"""Offline framing checks; synthetic samples never certify a physical button."""
import hashlib
import io
from pathlib import Path
import struct
import sys
import tarfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"tools"))
from host.reset import reset_mapping,reset_pinctrl_mapping,physical_reset_evidence,CONTROL_DTB_SHA256
from host.uart import TrialError
from reset_sample import evaluate
from resident_cold_trial import accepted_nonwriting_trial,decode_config_export,terminal_plain


def control_dtb(*,line=0,flags=1,base=0x10d10000,duplicate=False,pinctrl=False,pin=14,function=1):
    strings=bytearray();structure=bytearray();names={}
    def u32(*values):return struct.pack(">"+"I"*len(values),*values)
    def align(data):return data+b"\0"*((-len(data))%4)
    def node(name):structure.extend(u32(1)+align(name.encode()+b"\0"))
    def end():structure.extend(u32(2))
    def prop(name,value):
        if name not in names:names[name]=len(strings);strings.extend(name.encode()+b"\0")
        structure.extend(u32(3,len(value),names[name])+align(value))
    node("");node("soc");prop("#address-cells",u32(2));prop("#size-cells",u32(2))
    node("gpio@10d10000");prop("phandle",u32(0x52));prop("#gpio-cells",u32(2))
    prop("reg",u32(0,base,0,0x1000));end()
    if pinctrl:
        node("pinctrl@10e20000");prop("phandle",u32(15));prop("compatible",b"zte,zx-pinctrl\0")
        prop("reg",u32(0,0x10e20000,0,0x1000))
        prop("pinctrl-zx,bank-range-0",u32(12,5,3,12,1,2,12,2,1,12,2,2,12,2,1,12,3,2))
        prop("pinctrl-zx,gpio-range",u32(13,14,1,function))
        node("bank-range");prop("phandle",u32(12));prop("#pinctrl-zx,bank-range-cells",u32(2));end()
        node("gpio-range");prop("phandle",u32(13));prop("#pinctrl-zx,gpio-range-cells",u32(3));end();end()
    if duplicate:
        node("other-gpio");prop("phandle",u32(0x52));end()
    node("keys");node("keys@restore");prop("gpios",u32(0x52,line,flags));end();end();end();end()
    structure.extend(u32(9));off_struct=56;off_strings=off_struct+len(structure)
    blob=u32(0xd00dfeed,off_strings+len(strings),off_struct,off_strings,40,17,16,0,len(strings),len(structure))+bytes(16)+structure+strings
    if pinctrl:
        # Add gpio-ranges as a normal property inside the existing GPIO node.
        return control_dtb_with_ranges(blob,u32(15,0,pin,1,15,1,0,14,15,15,15,1))
    return blob


def control_dtb_with_ranges(blob,value):
    data=bytearray(blob);off_strings=struct.unpack_from(">I",data,12)[0]
    at=data.find(b"gpio@10d10000\0");at=(at+len(b"gpio@10d10000\0")+3)&~3
    name_at=struct.unpack_from(">I",data,32)[0]
    prop=struct.pack(">III",3,len(value),name_at)+value
    data[at:at]=prop;data.extend(b"gpio-ranges\0")
    for offset,delta in ((4,len(prop)+12),(12,len(prop)),(32,12),(36,len(prop))):
        struct.pack_into(">I",data,offset,struct.unpack_from(">I",data,offset)[0]+delta)
    return bytes(data)


class ResetEvidenceTests(unittest.TestCase):
    def physical_fixture(self):
        return {"schema":"sk-d840n-reset-physical-readonly-v1","physical_reset_pass":True,"gpio_written":False,
                "mmio_access_width":32,
                "nand_written":False,"control_dtb_sha256":CONTROL_DTB_SHA256,"gpio_registers":{"0x0":"0x2600"},
                "reset_mapping":{"register":"0x10d10014","bit":0,"active_low":True},
                "samples":[[0,1]]+[[i/10,0] for i in range(1,33)]+[[3.3,1]]}

    def test_builder_recomputes_physical_trace_instead_of_accepting_boolean(self):
        report=self.physical_fixture();self.assertTrue(physical_reset_evidence(report)["physical_reset_pass"])
        for key,value in (("samples",[]),("samples",[[0,0],[.1,0],[.2,1]]),
                          ("control_dtb_sha256","0"*64),("gpio_written",True),("physical_reset_pass",False),
                          ("mmio_access_width",8),("mmio_access_width",None)):
            changed={**report,key:value}
            with self.subTest(key=key),self.assertRaises(ValueError):physical_reset_evidence(changed)

    def test_physical_trace_gap_nan_and_wrong_polarity_are_not_accepted(self):
        report=self.physical_fixture()
        for trace in (report["samples"][:-1],[[0,1],[.1,0],[3.2,0],[3.3,1]],
                      [[0,1],[float("nan"),0],[.2,1]],[[.2,1],[.1,0],[.3,1]]):
            with self.assertRaises(ValueError):physical_reset_evidence({**report,"samples":trace})
        with self.assertRaises(ValueError):physical_reset_evidence({**report,"reset_mapping":{"register":"0x10d10014","bit":0,"active_low":False}})

    def test_exact_dtb_mapping_and_rejects_unknown_wiring(self):
        mapping=reset_mapping(control_dtb())
        self.assertEqual(mapping["register"],"0x10d10014")
        self.assertTrue(mapping["active_low"])
        for changes in ({"line":1},{"flags":0},{"base":0x10d20000},{"duplicate":True}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):reset_mapping(control_dtb(**changes))

    def test_truncated_and_overlapping_framing_does_not_prove_mapping(self):
        original=control_dtb()
        for n in (0,39,40,len(original)-1):
            with self.assertRaises((ValueError,KeyError,struct.error)):reset_mapping(original[:n])
        data=bytearray(original);struct.pack_into(">I",data,12,len(data))
        with self.assertRaises(ValueError):reset_mapping(data)

    def test_release_hold_three_seconds_release_required(self):
        samples=[(0,1)]+[(i/10,0) for i in range(1,33)]+[(3.3,1)]
        self.assertTrue(evaluate(samples))
        for trace in ([],[(0,0),(3,0),(3.1,1)],[(0,1)]+[(i/10,0) for i in range(1,30)]+[(3,1)],
                      samples[:-1],[(0,1),(0.1,0),(3.2,0),(3.3,1)],[(1,1),(.9,0),(1,1)]):
            with self.subTest(trace=trace):self.assertFalse(evaluate(trace))

    def test_short_release_resets_the_continuity_requirement(self):
        trace=[(0,1)]+[(i/10,0) for i in range(1,21)]+[(2.1,1)]+[(i/10,0) for i in range(22,43)]+[(4.3,1)]
        self.assertFalse(evaluate(trace))

    def test_gpio_zero_maps_to_pin14_variable_width_field_not_pin0(self):
        result=reset_pinctrl_mapping(control_dtb(pinctrl=True))
        self.assertEqual((result["global_pin"],result["shift"],result["width"],result["value"]),(14,29,2,"0x20000000"))
        for change in ({"pin":0},{"function":0},{"function":3}):
            with self.subTest(change=change),self.assertRaises(ValueError):reset_pinctrl_mapping(control_dtb(pinctrl=True,**change))

    def test_volatile_pinctrl_evidence_requires_exact_restoration_and_code(self):
        mapping=reset_pinctrl_mapping(control_dtb(pinctrl=True))
        report={**self.physical_fixture(),"schema":"sk-d840n-reset-physical-pinctrl-v2",
                "pinctrl_configuration":mapping,"pinctrl_runtime_code_verified":True,
                "mmio_access_width":32,
                "pinctrl_before":3,"pinctrl_configured":0x20000003,"pinctrl_after":3,"pinctrl_restored":True}
        self.assertEqual(physical_reset_evidence(report)["pinctrl_configuration"],mapping)
        for change in ({"pinctrl_after":0},{"pinctrl_configured":0x20000007},
                       {"pinctrl_runtime_code_verified":False},{"pinctrl_restored":False},{"mmio_access_width":8}):
            with self.subTest(change=change),self.assertRaises(ValueError):physical_reset_evidence({**report,**change})


class CurrentConfigEvidenceTests(unittest.TestCase):
    @staticmethod
    def archive():
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode="w:gz") as archive:
            data=b"config interface 'lan'\n";entry=tarfile.TarInfo("etc/config/network");entry.size=len(data)
            archive.addfile(entry,io.BytesIO(data))
        return stream.getvalue()

    @staticmethod
    def frame(data):
        return (f"BW_CONFIG_META {len(data)}\n{hashlib.sha256(data).hexdigest()}  /tmp/bootweb-current-config.tar.gz\n"
                f"BW_CONFIG_HEX_BEGIN\n{data.hex()}\nBW_CONFIG_END\n").encode()

    def test_complete_hex_export_is_length_and_hash_verified(self):
        data=self.archive();decoded,entries=decode_config_export(self.frame(data).replace(b"\n",b"\r\n"))
        self.assertEqual(decoded,data);self.assertIn("etc/config/network",entries)

    def test_corrupt_truncated_or_wrong_hash_export_is_rejected(self):
        data=self.archive();frame=self.frame(data)
        damaged=frame.replace(data.hex().encode(),b"00"+data.hex()[2:].encode())
        for value in (frame[:-10],damaged,frame.replace(hashlib.sha256(data).hexdigest().encode(),b"0"*64)):
            with self.assertRaises(TrialError):decode_config_export(value)

    def test_terminal_ansi_does_not_change_prompt_text(self):
        self.assertEqual(terminal_plain(b"\x1b[32mroot@OpenWrt:~# \x1b[0m"),b"root@OpenWrt:~# ")

    def test_native_locked_cold_trial_is_explicit_and_not_final_acceptance(self):
        clean={"build_manifest_sha256":"digest","resident_native_locked_trial_pass":True,"resident_ram_trial_pass":False}
        manifest={"native_service_trial":True,"resident_service_write_capable":False,
                  "nand_written":False,"native_write_enabled":False}
        accepted_nonwriting_trial(clean,manifest,"digest",native_locked=True)
        with self.assertRaises(TrialError):accepted_nonwriting_trial(clean,manifest,"digest")
        for change in ({"native_service_trial":False},{"resident_service_write_capable":True},{"native_write_enabled":True}):
            with self.assertRaises(TrialError):accepted_nonwriting_trial(clean,{**manifest,**change},"digest",native_locked=True)
        with self.assertRaises(TrialError):accepted_nonwriting_trial(clean,manifest,"different",native_locked=True)


if __name__=="__main__":unittest.main()
