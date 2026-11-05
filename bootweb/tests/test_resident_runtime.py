"""Synthetic current-resident record derivation; no serial or hardware state."""
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import test_resident_image as fixture
from host.core import Identity
from host.resident import SLOT_OFFSET,DESCRIPTOR_OFFSET,resident_descriptor
from host.resident_runtime import command_record
from host.uart import TrialError


def sha(value):return hashlib.sha256(value).hexdigest()


class ResidentRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.ResidentImageTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):fixture.ResidentImageTests.tearDownClass.__func__(cls)

    def artifacts(self,path,*,duplicate=False):
        bootstrap=(b"Z"*64+b"bootweb\0"+b"resident Web; updates require native backup and timing gates\0"+
                   b"[duration-ms] | trial <host-challenge> <duration-ms>\0"+(b"bootweb\0" if duplicate else b""))
        padded=bootstrap+bytes((-len(bootstrap))%64);tail=b"A"*64+padded
        stage2=bytearray(fixture.ResidentImageTests.candidate(self))
        stage2[SLOT_OFFSET:DESCRIPTOR_OFFSET]=b"\xff"*(DESCRIPTOR_OFFSET-SLOT_OFFSET)
        stage2[SLOT_OFFSET:SLOT_OFFSET+len(tail)]=tail
        stage2[DESCRIPTOR_OFFSET:DESCRIPTOR_OFFSET+256]=resident_descriptor(tail)
        current=b"B"*0x20000+bytes(stage2)+b"P"*0x80000
        audit={"payload_sha256":sha(bootstrap),"elf_sha256":sha(b"synthetic-elf")}
        manifest={"installed_sha256":sha(stage2),"tail_sha256":sha(tail),"bootstrap_audit":audit,
                  "native_callbacks_compiled":True}
        for name,data in (("resident-bootstrap.bin",bootstrap),("resident-bootstrap.elf",b"synthetic-elf"),
                          ("resident-tail-ram-trial.bin",tail)):
            (path/name).write_bytes(data)
        (path/"manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
        lib=self.lib;base=self.base
        class Checker:
            def resident_identity(self,image):
                digest=C.create_string_buffer(65)
                if lib.bw_resident_image_check(image,len(image),digest)!=1:raise ValueError("unknown synthetic image")
                return Identity(base.encode(),digest.value)
        return current,bootstrap,manifest,audit,Checker()

    def test_complete_record_is_derived_from_exact_installed_elf_and_manifest(self):
        with tempfile.TemporaryDirectory(prefix="resident-record-test-") as d:
            path=Path(d);current,bootstrap,manifest,audit,core=self.artifacts(path)
            with patch("host.resident_runtime.verify_payload",return_value=audit) as verify:
                name,record=command_record(current,0x9ff3c000,resident_dir=path,core=core)
            verify.assert_called_once();self.assertEqual(name,"bootweb");self.assertEqual(len(record),56)
            self.assertEqual(struct.unpack_from("<Q",record,0)[0],0x89100000+bootstrap.index(b"bootweb\0"))
            self.assertEqual(struct.unpack_from("<I",record,8)[0],5)
            self.assertEqual(struct.unpack_from("<Q",record,24)[0],0x89100000)
            self.assertNotEqual(struct.unpack_from("<Q",record,32)[0],0)

    def test_unknown_changed_or_ambiguous_artifacts_never_supply_a_record(self):
        for fault in ("missing","manifest","tail","bootstrap-audit","ambiguous"):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory(prefix="resident-record-test-") as d:
                path=Path(d);current,bootstrap,manifest,audit,core=self.artifacts(path,duplicate=fault=="ambiguous")
                if fault=="manifest":manifest["installed_sha256"]="0"*64
                elif fault=="tail":manifest["tail_sha256"]="0"*64
                elif fault=="bootstrap-audit":manifest["bootstrap_audit"]={}
                (path/"manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
                with patch("host.resident_runtime.verify_payload",return_value=audit),self.assertRaises(TrialError):
                    command_record(current,0x9ff3c000,resident_dir=None if fault=="missing" else path,core=core)


if __name__=="__main__":unittest.main()
