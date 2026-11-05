"""ctypes transport ABI for in-memory tests. No hardware adapter is provided."""
import ctypes as C
from .core import Port

INIT = C.CFUNCTYPE(C.c_int, C.c_void_p)
NOW = C.CFUNCTYPE(C.c_uint64, C.c_void_p)
POLL = C.CFUNCTYPE(None, C.c_void_p)
LISTEN = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint32, C.c_uint16)
ACCEPT = C.CFUNCTYPE(C.c_int, C.c_void_p)
RECV = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_size_t, C.c_uint32)
SEND = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_size_t)
CLOSE = C.CFUNCTYPE(None, C.c_void_p, C.c_int)
UPDATE_LOCK = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p)
PREPARE_UPDATE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.c_void_p)
FINISH_UPDATE = C.CFUNCTYPE(None, C.c_void_p, C.c_int)


class Platform(C.Structure):
    _fields_ = [("nand", Port), ("context", C.c_void_p), ("upload", C.c_void_p),
                ("upload_capacity", C.c_size_t), ("scratch", C.c_void_p), ("scratch_capacity", C.c_size_t),
                ("token", C.c_char * 65), ("layout", C.c_uint32), ("listen", LISTEN),
                ("accept", ACCEPT), ("recv", RECV), ("send", SEND), ("close", CLOSE),
                ("poll", POLL), ("milliseconds", NOW), ("reboot", INIT), ("stop_requested", INIT),
                ("resident_version", C.c_char_p), ("update_lock", UPDATE_LOCK),
                ("prepare_update", PREPARE_UPDATE), ("finish_update", FINISH_UPDATE),
                ("require_continuous_power", C.c_uint32), ("update_bound_ms", C.c_uint64),
                ("progress_io", C.c_uint32), ("requested_reboot", C.c_uint32)]
