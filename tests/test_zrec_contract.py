#!/usr/bin/env python3
"""Contract test for the zrec recovery service - runs on the PC, no toolchain.

The first real compile of zrec.c happens ~40 minutes into a CI build, and the
first real load of the page happens over a serial console on a board that is
currently unreachable. Both are expensive places to discover that a key the
shell writes is not the key the server reads, or that a phase string has no
translation. Everything here is checkable from the four source files alone.
"""
import re
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "overlays/package/utils/zrec"
SERVER = (PKG / "src/zrec.c").read_text(encoding="utf-8")
OPS = (PKG / "files/usr/libexec/zrec-ops").read_text(encoding="utf-8")
RUNNER = (PKG / "files/usr/sbin/zrec-run").read_text(encoding="utf-8")
BRINGUP = (PKG / "files/usr/libexec/zrec-bringup").read_text(encoding="utf-8")
PREINIT = (PKG / "files/lib/preinit/85_zrec").read_text(encoding="utf-8")
BOOT = (PKG / "files/etc/rc.d/S21zrec").read_text(encoding="utf-8")
PAGE = (PKG / "files/www/zrec/index.html").read_text(encoding="utf-8")
MAKEFILE = (PKG / "Makefile").read_text(encoding="utf-8")

failures = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not ok:
        failures.append(name)


def sh_syntax(name, path):
    r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    check("sh syntax: " + name, r.returncode == 0, r.stderr.strip())


# 1. shell syntax of everything that runs on the board
sh_syntax("zrec-ops", PKG / "files/usr/libexec/zrec-ops")
sh_syntax("zrec-run", PKG / "files/usr/sbin/zrec-run")
sh_syntax("zrec-bringup", PKG / "files/usr/libexec/zrec-bringup")
sh_syntax("85_zrec", PKG / "files/lib/preinit/85_zrec")
sh_syntax("S21zrec", PKG / "files/etc/rc.d/S21zrec")

# 2. the verbs the page can post must exist in the server whitelist AND in ops
page_ops = set(re.findall(r"operation:'([a-z-]+)'", PAGE)) | set(
    re.findall(r'data-action="([a-z-]+)"', PAGE))
server_ops = set(re.findall(r'"([a-z-]+)"\s*[,\s]',
                            re.search(r"static const char \*const v\[\] = \{(.*?)\};",
                                      SERVER, re.S).group(1)))
ops_case = set(re.findall(r"^\s*([a-z|-]+)\)\s*$", OPS, re.M)) | set(
    re.findall(r"^(flash\|write|\w+\|\w+)\)", OPS, re.M))
ops_handled = set()
for m in re.findall(r"^(.+?)\)\n", OPS, re.M):
    for v in m.split("|"):
        v = v.strip().strip('"')
        if re.fullmatch(r"[a-z-]+", v):
            ops_handled.add(v)
check("page posts only known verbs: %s" % sorted(page_ops),
      page_ops <= server_ops, str(sorted(page_ops - server_ops)))
check("server whitelist is handled by zrec-ops",
      server_ops <= (ops_handled | {"cancel"}), str(sorted(server_ops - ops_handled - {"cancel"})))

# 3. state keys: whatever shell code writes must be something the server reads
written = set(re.findall(r"set_kv\s+([a-z_]+)", OPS)) | set(
    re.findall(r'printf .*>"?\$ZROOT/([a-z_]+)', RUNNER))
read = set(re.findall(r'kv_(?:get|num|set)\("([a-z_]+)"', SERVER))
check("ops writes only keys the server knows: %s" % sorted(written),
      written <= read, str(sorted(written - read)))
# addr/netif are what /api/env shows, and they are the only written record of
# "this board has a usable port" that survives without a browser.
bringup_writes = set(re.findall(r'"\$ZROOT/(addr|netif)"', BRINGUP))
check("zrec-bringup publishes addr and netif", {"addr", "netif"} <= bringup_writes,
      str(sorted({"addr", "netif"} - bringup_writes)))
check("zrec-bringup is idempotent (a preinit hook plus rc.d must not stack loops)",
      "pidof zrec" in BRINGUP)
check("rc.d entry delegates to bringup instead of duplicating it",
      "zrec-bringup" in BOOT and "/usr/sbin/zrec " not in BOOT)

# /lib/preinit files are sourced by the init script: a top-level command there
# runs during the boot, and a blocking one is a board that never finishes.
preinit_calls = [l for l in PREINIT.splitlines()
                 if l and not l.startswith(("#", "boot_hook_add", "}", "zrec_recovery(", "\t"))]
check("85_zrec only defines and registers: %s" % preinit_calls,
      preinit_calls == [], str(preinit_calls))
check("85_zrec registers both hooks",
      "boot_hook_add preinit_main zrec_recovery" in PREINIT and
      "boot_hook_add failsafe zrec_recovery" in PREINIT)
check("85_zrec calls bringup", "zrec-bringup" in PREINIT)

# 4. phase strings must be translatable, or the status line shows a raw key
phases = set(re.findall(r'(?:kv_set\("phase", "|set_kv phase )"?([a-z-]+)"?', SERVER)) | \
         set(re.findall(r'set_kv phase ([a-z-]+)', OPS)) | \
         set(re.findall(r'phase = "([a-z-]+)"', SERVER))
dict_keys_block = re.search(r"const words=\{(.*?)\n\};", PAGE, re.S).group(1)
keypat = re.compile(r"(?:^|[,{])\s*'?([a-zA-Z][a-zA-Z0-9-]*)'?:")
zh_part = dict_keys_block.split("en:{")[0]
zh = set(keypat.findall(zh_part[zh_part.index("zh:{") + 3:]))
en = set(keypat.findall(dict_keys_block.split("en:{")[1]))
dict_keys = zh & en
check("every phase has a translation: %s" % sorted(phases),
      phases <= dict_keys, str(sorted(phases - dict_keys)))

# 5. the two dictionaries must carry the same keys
check("zh/en dictionaries match", zh == en,
      "zh-en=%s en-zh=%s" % (sorted(zh - en), sorted(en - zh)))

# 6. every id the script touches and every data-t key must exist in the markup
ids_in_dom = set(re.findall(r'id="([^"]+)"', PAGE))
ids_used = set(re.findall(r"el\('([^']+)'\)", PAGE)) | set(re.findall(r"text\('([^']+)'", PAGE))
check("no el()/text() target missing from the DOM: %s" % sorted(ids_used),
      ids_used <= ids_in_dom, str(sorted(ids_used - ids_in_dom)))
data_t = set(re.findall(r'data-t="([^"]+)"', PAGE))
check("every data-t key is in the dictionaries", data_t <= dict_keys,
      str(sorted(data_t - dict_keys)))

# 7. routes the page calls must be routes the server answers
routes = set(re.findall(r"/api/[a-z_-]+", PAGE))
for r in sorted(routes):
    check("server answers %s" % r,
          ('"%s"' % r) in SERVER or ('"%s/"' % r) in SERVER, "")

# 8. the package must actually install everything zrec expects at runtime
for path in ["/usr/sbin/zrec", "/usr/sbin/zrec-run", "/usr/libexec/zrec-ops",
             "/usr/libexec/zrec-bringup", "/lib/preinit/85_zrec",
             "/etc/rc.d/S21zrec", "/www/zrec/index.html"]:
    check("Makefile installs %s" % path, path.split("/")[-1] in MAKEFILE, "")
for const in re.findall(r'#define\s+(PAGE|OPS|RUNNER)\s+"([^"]+)"', SERVER):
    check("server path %s exists in the install list" % const[1],
          const[1].split("/")[-1] in MAKEFILE, "")

# The delivery gate in the build workflow must look for exactly what the package
# installs; otherwise it passes on files nobody shipped, or fails on a rename.
wf = (PKG.parents[3] / ".github/workflows/build-sk-d840n.yml").read_text(encoding="utf-8")
m = re.search(r'want="([^"]*zrec[^"]*)"', wf)
check("build workflow carries a zrec delivery gate", bool(m), "")
if m:
    for f in m.group(1).split():
        check("gate wants %s and the package installs it" % f,
              f.split("/")[-1] in MAKEFILE, "")

# 9. busybox-isms that broke other tools on this image
check("no iflag= in dd (busybox dd has no iflag)", "iflag=" not in OPS)
check("no 'local' in zrec-ops (keeps it ash-safe under set -u)",
      not re.search(r"^\s*local ", OPS, re.M))
check("mtd argument order matches the proven flasher (image then label)",
      'mtd write "$src" "$target"' in OPS and 'mtd erase "$target"' in OPS)

print()
if failures:
    print("%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all zrec contract checks passed")
