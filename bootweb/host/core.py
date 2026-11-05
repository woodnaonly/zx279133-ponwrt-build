"""ctypes binding to the SAME freestanding C validator/flash engine.

No writable device/file backend is exposed. The test DLL has a separate name,
fingerprint and mandatory build-mode check; it cannot be mistaken for production.
"""
import ctypes as C
from pathlib import Path
import re
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
BASE = "5b084490813b540c1a82ddf2e2d4de46080e6cfe47b15b2e6575bdf3fe198fe9"
LAYOUTS = {"current240": 0, "native241": 1, "recovery237": 2}
STATES = ("idle", "receiving", "validating", "ready", "queued", "writing", "success", "failed", "cancelled")
MAX_UPLOAD = 0x0FA10000


class ValidationError(ValueError):
    pass


class Identity(C.Structure):
    _fields_ = [("vendor_abi_sha256", C.c_char * 65), ("installed_sha256", C.c_char * 65)]


class UbootBudget(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in (
        "power_started_ms", "power_uncertainty_ms", "erase_max_ms", "page_max_ms",
        "read_max_ms", "hash_max_ms", "bad_query_max_ms", "service_max_ms", "fixed_max_ms", "power_policy")]


class Span(C.Structure):
    _fields_ = [("offset", C.c_uint32), ("capacity", C.c_uint32),
                ("data_offset", C.c_uint64), ("length", C.c_uint64),
                ("name", C.c_char * 16), ("sha256", C.c_char * 65)]


class Result(C.Structure):
    _fields_ = [("kind", C.c_uint32), ("layout", C.c_uint32), ("count", C.c_uint32),
                ("package_bytes", C.c_uint64), ("package_sha256", C.c_char * 65), ("writes", Span * 3)]

    def report(self):
        return {"kind": "system" if self.kind == 1 else "uboot", "layout": list(LAYOUTS)[self.layout],
                "bytes": self.package_bytes, "sha256": self.package_sha256.decode(),
                "writes": [{"name": s.name.decode(), "offset": f"0x{s.offset:08x}",
                            "capacity": s.capacity, "length": s.length, "sha256": s.sha256.decode()}
                           for s in self.writes[:self.count]],
                "authenticity_verified": False, "hardware_verified": False,
                "flashable_release": False}


class Task(C.Structure):
    _fields_ = [("state", C.c_uint32), ("layout", C.c_uint32), ("id", C.c_uint64),
                ("expected", C.c_size_t), ("received", C.c_size_t), ("capacity", C.c_size_t),
                ("buffer", C.c_void_p), ("token", C.c_char * 65), ("base", C.c_char * 65),
                ("error", C.c_char * 192), ("result", Result),
                ("vendor_abi_sha256", C.c_char * 65), ("image_bound_confirmation", C.c_uint32),
                ("continuous_power_confirmed", C.c_uint32)]


class Request(C.Structure):
    _fields_ = [("method", C.c_char * 8), ("path", C.c_char * 32),
                ("token", C.c_char * 65), ("host", C.c_char * 64),
                ("origin", C.c_char * 96), ("length", C.c_uint64)]


BAD = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint32)
READ = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint32, C.c_void_p, C.c_size_t)
ERASE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint32)
WRITE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint32, C.c_void_p)
VERIFY_BOOT = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.c_size_t)
WATCH = C.CFUNCTYPE(None, C.c_void_p)
PROGRESS = C.CFUNCTYPE(None, C.c_void_p, C.c_char_p, C.c_uint32, C.c_uint32)
VERIFY_IDENTITY = C.CFUNCTYPE(C.c_int, C.c_void_p, C.POINTER(Identity))
PREPARE_UBOOT = C.CFUNCTYPE(C.c_int, C.c_void_p, C.POINTER(Identity), C.c_void_p, C.c_size_t, C.POINTER(UbootBudget))
NAND_NOW = C.CFUNCTYPE(C.c_uint64, C.c_void_p)
AUTHORIZE_NO_RESCUE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.POINTER(Identity), C.c_char_p)


class Port(C.Structure):
    _fields_ = [("context", C.c_void_p), ("capabilities", C.c_uint32),
                ("is_bad", BAD), ("read", READ), ("erase", ERASE),
                ("write_page", WRITE), ("verify_boot", VERIFY_BOOT),
                ("watchdog", WATCH), ("progress", PROGRESS), ("verify_identity", VERIFY_IDENTITY),
                ("prepare_uboot_write", PREPARE_UBOOT), ("milliseconds", NAND_NOW),
                ("authorize_no_rescue", AUTHORIZE_NO_RESCUE)]


class Core:
    def __init__(self, *, testing=False):
        suffix = ".dll" if sys.platform == "win32" else ".so"
        path = ROOT / ".build" / (("bootweb-test" if testing else "bootweb") + suffix)
        if not path.is_file():
            raise RuntimeError("build the C core first: python bootweb/tools/build.py --host-only --test-core")
        self.lib = C.CDLL(str(path))
        self.lib.bw_base_hash.restype = C.c_char_p
        self.base = self.lib.bw_base_hash().decode()
        if bool(self.lib.bw_test_build()) != testing:
            raise RuntimeError("refusing mismatched test/production C library")
        self.lock = threading.RLock()
        signatures = {
            "bw_sha256": ([C.c_void_p, C.c_size_t, C.c_void_p], None),
            "bw_crc32": ([C.c_void_p, C.c_size_t], C.c_uint32),
            "bw_validate": ([C.c_void_p, C.c_size_t, C.c_char_p, C.c_uint32, C.POINTER(Result), C.c_void_p], C.c_int),
            "bw_validate_identity": ([C.c_void_p, C.c_size_t, C.POINTER(Identity), C.c_uint32, C.POINTER(Result), C.c_void_p], C.c_int),
            "bw_resident_image_check": ([C.c_void_p, C.c_size_t, C.c_void_p], C.c_int),
            "bw_resident_image_version": ([C.c_void_p, C.c_size_t], C.c_char_p),
            "bw_execute": ([C.c_void_p, C.c_size_t, C.c_char_p, C.c_uint32, C.POINTER(Port), C.c_void_p, C.c_size_t, C.c_void_p], C.c_int),
            "bw_execute_identity": ([C.c_void_p, C.c_size_t, C.POINTER(Identity), C.c_uint32, C.POINTER(Port), C.c_void_p, C.c_size_t, C.c_void_p], C.c_int),
            "bw_uboot_budget_check": ([C.POINTER(UbootBudget), C.c_uint64, C.POINTER(C.c_uint64), C.c_void_p], C.c_int),
            "bw_system_budget_bound": ([C.POINTER(Result), C.POINTER(UbootBudget), C.POINTER(C.c_uint64), C.c_void_p], C.c_int),
            "bw_task_init": ([C.POINTER(Task), C.c_void_p, C.c_size_t, C.c_char_p, C.c_uint32, C.c_char_p], C.c_int),
            "bw_task_init_identity": ([C.POINTER(Task), C.c_void_p, C.c_size_t, C.POINTER(Identity), C.c_uint32, C.c_char_p], C.c_int),
            "bw_task_begin": ([C.POINTER(Task), C.c_size_t, C.c_uint64], C.c_int),
            "bw_task_feed": ([C.POINTER(Task), C.c_void_p, C.c_size_t], C.c_int),
            "bw_task_finish": ([C.POINTER(Task)], C.c_int),
            "bw_task_confirm": ([C.POINTER(Task), C.c_uint64, C.c_char_p, C.c_char_p], C.c_int),
            "bw_task_confirm_identity": ([C.POINTER(Task), C.c_uint64, C.c_char_p, C.c_char_p, C.c_char_p], C.c_int),
            "bw_task_cancel": ([C.POINTER(Task), C.c_char_p], C.c_int),
            "bw_task_run": ([C.POINTER(Task), C.POINTER(Port), C.c_void_p, C.c_size_t], C.c_int),
            "bw_http_parse": ([C.c_void_p, C.c_size_t, C.POINTER(Request), C.c_void_p], C.c_int),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, result

    def validate(self, data, *, base=None, layout="current240"):
        if not 1536 <= len(data) <= MAX_UPLOAD:
            raise ValidationError("upload length outside fixed limits")
        result, error = Result(), C.create_string_buffer(192)
        with self.lock:
            rc = self.lib.bw_validate(data, len(data), (base or self.base).encode(),
                                      LAYOUTS[layout], C.byref(result), error)
        if rc:
            raise ValidationError(error.value.decode())
        return result

    def resident_identity(self, stage2):
        """Recognize the original ABI or the constrained resident format only.

        A hash supplied by an operator is not an ABI proof. The production C
        checker normalizes ONLY the audited cold hook and resident tail.
        """
        digest=C.create_string_buffer(65)
        with self.lock:
            kind=self.lib.bw_resident_image_check(stage2,len(stage2),digest)
        if kind<0:
            raise ValidationError("unknown vendor ABI or resident image format")
        return Identity(self.base.encode(),digest.value)

    def validate_identity(self, data, identity, *, layout="current240"):
        if not isinstance(identity,Identity) or any(not re.fullmatch(rb"[0-9a-f]{64}",value) for value in
                (identity.vendor_abi_sha256,identity.installed_sha256)):
            raise ValidationError("invalid explicit installed/vendor identity")
        if not 1536<=len(data)<=MAX_UPLOAD:
            raise ValidationError("upload length outside fixed limits")
        result,error=Result(),C.create_string_buffer(192)
        with self.lock:
            rc=self.lib.bw_validate_identity(data,len(data),C.byref(identity),LAYOUTS[layout],C.byref(result),error)
        if rc:
            raise ValidationError(error.value.decode())
        return result

    def parse_http(self, header):
        request, error = Request(), C.create_string_buffer(192)
        if self.lib.bw_http_parse(header, len(header), C.byref(request), error):
            raise ValidationError(error.value.decode())
        return request


def read_package(path):
    with Path(path).open("rb") as source:
        size = source.seek(0, 2)
        if not 1536 <= size <= MAX_UPLOAD:
            raise ValidationError("upload length outside fixed limits")
        source.seek(0)
        data = source.read(MAX_UPLOAD + 1)
    if len(data) != size:
        raise ValidationError("package changed while reading")
    return data
