"""Compile a separate synthetic ABI checker. No serial or NAND is opened."""
import ctypes as C
import hashlib
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from host.resident import resident_descriptor,DESCRIPTOR_OFFSET,DEVELOPMENT_HOOK,ORIGINAL_HOOK,SLOT_OFFSET


class ResidentImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        baseline=bytearray(b"S"*SLOT_OFFSET+b"\xff"*(0x160000-SLOT_OFFSET))
        baseline[0xa098:0xa0a8]=ORIGINAL_HOOK;cls.baseline=bytes(baseline)
        cls.base=hashlib.sha256(cls.baseline).hexdigest()
        cls.temporary=tempfile.TemporaryDirectory(prefix="bootweb-image-test-")
        library=Path(cls.temporary.name)/("image-test.dll" if sys.platform=="win32" else "image-test.so")
        zig=ROOT/".build/toolchain/zig-x86_64-windows-0.14.1/zig.exe"
        subprocess.run([str(zig),"cc","-std=c11","-O2","-Wall","-Wextra","-Werror","-fno-builtin",
                        '-DBW_TEST_BASE="'+cls.base+'"',"-I"+str(ROOT/"include"),"-I"+str(ROOT/"src"),
                        *[str(ROOT/"src"/(name+".c")) for name in ("hash","identity","resident_image","install_image","json","fdt","package")],"-shared",
                        *([] if sys.platform=="win32" else ["-fPIC"]),"-o",str(library)],check=True,capture_output=True)
        cls.lib=C.CDLL(str(library));cls.lib.bw_resident_image_check.argtypes=[C.c_void_p,C.c_size_t,C.c_void_p]
        cls.lib.bw_resident_image_version.argtypes=[C.c_void_p,C.c_size_t]
        cls.lib.bw_resident_image_version.restype=C.c_char_p

    @classmethod
    def tearDownClass(cls):
        # Windows keeps the loaded DLL mapped. Close its explicit module handle
        # before TemporaryDirectory removes ONLY the test-owned directory.
        if sys.platform=="win32":
            import _ctypes
            _ctypes.FreeLibrary(cls.lib._handle)
        cls.lib=None;cls.temporary.cleanup()

    def candidate(self):
        tail=b"D"*128;data=bytearray(self.baseline);data[0xa098:0xa0a8]=DEVELOPMENT_HOOK
        data[SLOT_OFFSET:SLOT_OFFSET+len(tail)]=tail
        descriptor=resident_descriptor(tail);self.assertEqual(len(descriptor),256)
        data[DESCRIPTOR_OFFSET:DESCRIPTOR_OFFSET+256]=descriptor
        return data

    def check(self,data):
        digest=C.create_string_buffer(65);buffer=C.create_string_buffer(bytes(data),len(data))
        rc=self.lib.bw_resident_image_check(buffer,len(data),digest)
        return rc,digest.value.decode()

    def test_baseline_and_resident_have_separate_installed_and_vendor_abi_hashes(self):
        self.assertEqual(self.check(self.baseline),(0,self.base))
        candidate=self.candidate();rc,digest=self.check(candidate)
        self.assertEqual(rc,1);self.assertEqual(digest,hashlib.sha256(candidate).hexdigest());self.assertNotEqual(digest,self.base)

    def test_other_vendor_changes_bad_tail_bad_hook_or_padding_fail(self):
        for offset in (0,0xa098,SLOT_OFFSET,SLOT_OFFSET+129,DESCRIPTOR_OFFSET,DESCRIPTOR_OFFSET+24,DESCRIPTOR_OFFSET+108,DESCRIPTOR_OFFSET+255):
            data=self.candidate();data[offset]^=1
            with self.subTest(offset=hex(offset)):self.assertEqual(self.check(data)[0],-1)

    def test_rehashed_tail_does_not_authorize_a_different_vendor_abi(self):
        data=self.candidate();data[0x14000]^=1
        self.assertEqual(self.check(data)[0],-1)

    def test_installed_profile_not_the_current_ram_payload_controls_version(self):
        old=bytes(self.candidate());new=bytearray(old)
        new[DESCRIPTOR_OFFSET:DESCRIPTOR_OFFSET+256]=resident_descriptor(b"D"*128,profile="native241")
        self.assertEqual(self.check(new)[0],1)
        self.assertEqual(self.lib.bw_resident_image_version(old,len(old)),b"bootweb-resident-v1")
        self.assertEqual(self.lib.bw_resident_image_version(bytes(new),len(new)),b"bootweb-resident-native241-v1")
        new[0x14000]^=1
        self.assertIsNone(self.lib.bw_resident_image_version(bytes(new),len(new)))
        self.assertIsNone(self.lib.bw_resident_image_version(self.baseline,len(self.baseline)))

    def test_wrong_size_unaligned_and_oversized_descriptors_fail(self):
        data=self.candidate()
        for size in (0,127,0x60000):
            changed=bytearray(data);struct.pack_into("<I",changed,DESCRIPTOR_OFFSET+20,size)
            self.assertEqual(self.check(changed)[0],-1)
        self.assertEqual(self.check(data[:-1])[0],-1)
        for tail in (b"",b"X",b"X"*0x60000):
            with self.assertRaises(ValueError):resident_descriptor(tail)


if __name__=="__main__":unittest.main()
