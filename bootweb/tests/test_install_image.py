"""Exact candidate/package reconstruction from a preserved synthetic original."""
import ctypes as C
import hashlib
import io
import tarfile
import unittest
import test_resident_image as fixture
from fixtures import boot
from host.resident import DESCRIPTOR_OFFSET,SLOT_OFFSET
from host.resident import resident_descriptor

class Parts(C.Structure):
    _fields_=[(name,C.c_void_p) for name in ("prefix","tail","descriptor","suffix")]+[
        (name,C.c_size_t) for name in ("prefix_bytes","tail_bytes","descriptor_bytes","suffix_bytes")]+[
        (name,C.c_char_p) for name in ("original_sha256","candidate_sha256","package_sha256")]

class InstallImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.ResidentImageTests.setUpClass.__func__(cls)
        cls.lib.bw_install_prepare.argtypes=[C.POINTER(Parts),C.c_void_p,C.c_size_t,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t),C.c_void_p]

    @classmethod
    def tearDownClass(cls):fixture.ResidentImageTests.tearDownClass.__func__(cls)

    def values(self):
        candidate=bytes(fixture.ResidentImageTests.candidate(self));package=boot(self.base,payload=candidate)
        with tarfile.open(fileobj=io.BytesIO(package)) as tar:offset=tar.getmember("uboot.bin").offset_data
        values=(package[:offset],candidate[SLOT_OFFSET:SLOT_OFFSET+128],candidate[DESCRIPTOR_OFFSET:DESCRIPTOR_OFFSET+256],package[offset+len(candidate):])
        buffers=[C.create_string_buffer(value,len(value)) for value in values]
        parts=Parts(*[C.addressof(value) for value in buffers],*[len(value) for value in values],self.base.encode(),
                    hashlib.sha256(candidate).hexdigest().encode(),hashlib.sha256(package).hexdigest().encode())
        return parts,buffers,package

    def run_prepare(self,parts,original,output):
        n=C.c_size_t(123);error=C.create_string_buffer(192)
        rc=self.lib.bw_install_prepare(C.byref(parts),original,len(original),output,len(output),C.byref(n),error)
        return rc,n.value,error.value.decode()

    def test_reconstructs_identical_complete_candidate_without_touching_original(self):
        parts,buffers,expected=self.values();original=C.create_string_buffer(self.baseline,len(self.baseline));output=C.create_string_buffer(len(expected))
        rc,n,error=self.run_prepare(parts,original,output)
        self.assertEqual(rc,0,error);self.assertEqual(n,len(expected));self.assertEqual(output.raw,expected);self.assertEqual(original.raw,self.baseline)

    def test_resident_upgrade_clears_a_longer_previous_tail_and_binds_current_hash(self):
        old=bytearray(fixture.ResidentImageTests.candidate(self))
        old_tail=b"O"*512
        old[SLOT_OFFSET:SLOT_OFFSET+512]=old_tail
        old[DESCRIPTOR_OFFSET:DESCRIPTOR_OFFSET+256]=resident_descriptor(old_tail)
        old_digest=hashlib.sha256(old).hexdigest()
        candidate=bytes(fixture.ResidentImageTests.candidate(self))
        package=boot(old_digest,payload=candidate)
        with tarfile.open(fileobj=io.BytesIO(package)) as tar:offset=tar.getmember("uboot.bin").offset_data
        values=(package[:offset],candidate[SLOT_OFFSET:SLOT_OFFSET+128],candidate[DESCRIPTOR_OFFSET:DESCRIPTOR_OFFSET+256],package[offset+len(candidate):])
        buffers=[C.create_string_buffer(value,len(value)) for value in values]
        parts=Parts(*[C.addressof(value) for value in buffers],*[len(value) for value in values],old_digest.encode(),
                    hashlib.sha256(candidate).hexdigest().encode(),hashlib.sha256(package).hexdigest().encode())
        original=C.create_string_buffer(bytes(old),len(old));output=C.create_string_buffer(len(package))
        rc,n,error=self.run_prepare(parts,original,output)
        self.assertEqual(rc,0,error);self.assertEqual(output.raw,package);self.assertEqual(original.raw,bytes(old))
        self.assertEqual(output.raw[offset+SLOT_OFFSET+128:offset+SLOT_OFFSET+512],b"\xff"*384)
        parts.original_sha256=self.base.encode()
        self.assertNotEqual(self.run_prepare(parts,original,output)[0],0)

    def test_bad_hash_corrupt_parts_and_original_erases_nothing(self):
        for fault in ("original","candidate","package","tail","descriptor","suffix"):
            parts,buffers,expected=self.values();original=C.create_string_buffer(self.baseline,len(self.baseline));output=C.create_string_buffer(len(expected))
            if fault=="original":original[0]=b"X"
            elif fault in ("candidate","package"):setattr(parts,fault+"_sha256",b"0"*64)
            else:buffers[{"tail":1,"descriptor":2,"suffix":3}[fault]][0]=b"X"
            rc,n,error=self.run_prepare(parts,original,output)
            with self.subTest(fault=fault):self.assertNotEqual(rc,0,error);self.assertEqual(n,0)

    def test_overlap_short_capacity_and_out_of_slot_parts_fail(self):
        parts,buffers,expected=self.values();original=C.create_string_buffer(self.baseline,len(self.baseline));output=C.create_string_buffer(len(expected))
        self.assertNotEqual(self.run_prepare(parts,original,original)[0],0)
        self.assertNotEqual(self.run_prepare(parts,original,C.create_string_buffer(16))[0],0)
        parts.tail=parts.prefix=C.addressof(output)
        self.assertNotEqual(self.run_prepare(parts,original,output)[0],0)
        parts,buffers,expected=self.values();parts.tail_bytes=0x60000
        self.assertNotEqual(self.run_prepare(parts,original,output)[0],0)

    def test_original_overlap_is_rejected_with_sufficient_output_capacity(self):
        parts,buffers,expected=self.values()
        shared=C.create_string_buffer(len(expected)+len(self.baseline))
        C.memmove(shared,self.baseline,len(self.baseline))
        n=C.c_size_t(123);error=C.create_string_buffer(192)
        rc=self.lib.bw_install_prepare(C.byref(parts),shared,len(self.baseline),shared,len(expected),C.byref(n),error)
        self.assertNotEqual(rc,0);self.assertEqual(n.value,0)
        self.assertIn(b"overlaps",error.value)
        self.assertEqual(shared.raw[:len(self.baseline)],self.baseline)

if __name__=="__main__":unittest.main()
