"""Compare two uImages by what they carry, not by their bytes.

A uImage header holds the time the image was made, so two images built from
the same kernel at different moments are different files - the header's
timestamp and its CRC say so.  What has to match is the payload: the gzip
stream that holds the kernel, and the kernel that comes out of it.

    python staging/ctl/cmpuimage.py <a> <b>
"""
import gzip
import hashlib
import io
import struct
import sys

HDR = 64
MAGIC = 0x27051956


def head(path):
    with open(path, "rb") as fh:
        raw = fh.read(HDR)
        (magic, hcrc, ts, size, load, ep, dcrc, os_, arch, typ, comp) = \
            struct.unpack(">IIIIIIIBBBB", raw[:32])
        name = raw[32:64].split(b"\0")[0].decode("utf-8", "replace")
        return {
            "path": path,
            "magic": magic,
            "time": ts,
            "size": size,
            "load": load,
            "entry": ep,
            "os": os_,
            "arch": arch,
            "type": typ,
            "comp": comp,
            "name": name,
            "total": len(raw),
        }


def payload(path):
    with open(path, "rb") as fh:
        fh.seek(HDR)
        h = head(path)
        return fh.read(h["size"])


def kernel(path):
    p = payload(path)
    return gzip.decompress(p)


def md5(b):
    return hashlib.md5(b).hexdigest()


def main():
    a, b = sys.argv[1], sys.argv[2]
    for p in (a, b):
        h = head(p)
        print("%s" % p)
        print("   name %r  os %d arch %d type %d comp %d  load 0x%x entry 0x%x"
              % (h["name"], h["os"], h["arch"], h["type"], h["comp"],
                 h["load"], h["entry"]))
        print("   built %d  payload %d  file %d  magic 0x%08x"
              % (h["time"], h["size"], h["total"], h["magic"]))
        if h["magic"] != MAGIC:
            print("   NOT A uImage")
    pa, pb = payload(a), payload(b)
    ka, kb = kernel(a), kernel(b)
    print("")
    print("payload md5   %s  %s  %s"
          % (md5(pa), md5(pb), "MATCH" if pa == pb else "DIFFER"))
    print("kernel  md5   %s  %s  %s"
          % (md5(ka), md5(kb), "MATCH" if ka == kb else "DIFFER"))
    print("kernel bytes  %d  %d" % (len(ka), len(kb)))
    print("RESULT: %s" % ("IDENTICAL KERNEL" if ka == kb else "NOT IDENTICAL"))


if __name__ == "__main__":
    main()
