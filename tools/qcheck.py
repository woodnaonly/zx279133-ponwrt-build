#!/usr/bin/env python3
r"""Syntax-check the remote half of a PC-side ssh script.

Scripts like tools/flash-direct.sh run multi-line shell inside `$SSH "..."`, so their real
bug surface is not the local file - `sh -n` passes that whether the remote quoting is right
or not. A mismatched escape there is discovered *while the flash partition is being erased*.
This walks each `$SSH "..."` string, applies exactly the unescaping bash would apply inside
double quotes (`\$`->`$`, `\"`->`"`, backslash before anything else stays), substitutes the
variables the local shell would expand, and hands the result to `sh -n` as the remote script
that would actually run.

	python tools/qcheck.py tools/flash-direct.sh [VAR=value ...]
"""
import os
import re
import subprocess
import sys
import tempfile


def snippets(src):
    """Every `<$VAR holding an ssh command line> "<remote script>"` in the file.

    The variable is named SSH in most tools and LANSSH/ETHSSH in others, because some of them
    talk to two addresses of the same board, so anything whose name ends in SSH counts.
    """
    tags = {"$" + t + ' "' for t in re.findall(r"\b([A-Z0-9_]*SSH)\b", src)}
    out = []
    for tag in tags:
        i = 0
        while True:
            i = src.find(tag, i)
            if i < 0:
                break
            j = i + len(tag)
            buf = []
            while j < len(src):
                c = src[j]
                if c == "\\" and j + 1 < len(src):
                    buf.append(src[j])
                    buf.append(src[j + 1])
                    j += 2
                    continue
                if c == '"':
                    break
                buf.append(c)
                j += 1
            out.append((i, "".join(buf)))
            i = j + 1
    return [s for _, s in sorted(out, key=lambda t: t[0])]


def unescape(s):
    r, k = [], 0
    while k < len(s):
        if s[k] == "\\" and k + 1 < len(s) and s[k + 1] in "$\"`\\":
            r.append(s[k + 1])
            k += 2
            continue
        r.append(s[k])
        k += 1
    return "".join(r)


def main():
    path = sys.argv[1]
    subs = {}
    for a in sys.argv[2:]:
        k, v = a.split("=", 1)
        subs[k] = v
    src = open(path, encoding="utf-8", newline="").read().replace("\r\n", "\n")
    got = snippets(src)
    print("%s: %d remote snippet(s)" % (path, len(got)))
    rc = 0
    for n, raw in enumerate(got):
        s = unescape(raw)
        # Local expansions the real script would do before handing the string to ssh.
        for k, v in subs.items():
            s = s.replace("${" + k + "}", v).replace("$" + k, v)
        for k in ("SSH", "DEV", "KEY", "MODE"):
            if k not in subs:
                s = s.replace("$" + k, {"SSH": "ssh", "DEV": "root@192.168.9.1",
                                        "KEY": "k", "MODE": "dry"}[k])
        f = os.path.join(tempfile.gettempdir(), "qcheck-%d.sh" % n)
        open(f, "w", newline="\n").write(s)
        r = subprocess.run(["sh", "-n", f], capture_output=True, text=True)
        first = s.strip().splitlines()[0][:60] if s.strip() else ""
        if r.returncode == 0:
            print("  #%d OK    (%d lines) %s" % (n, len(s.splitlines()), first))
        else:
            rc = 1
            print("  #%d SYNTAX ERROR (%d B): %s" % (n, len(s), r.stderr.strip()[:200]))
            print("       first line: %s" % first)
    return rc


if __name__ == "__main__":
    sys.exit(main())
