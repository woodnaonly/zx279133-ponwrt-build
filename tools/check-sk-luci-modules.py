#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that every LuCI module in this repository exports a class.

LuCI's loader does not take a module's export at face value.  It evaluates
the factory and then does 'new' on whatever came back, and it refuses a
factory that did not hand it a constructor:

    _class = _factory.apply(_factory, [window, document, L].concat(instances));
    if (!Class.isSubclass(_class))
        LuCI.prototype.error('TypeError', '"%s" factory yields invalid constructor', name);
    ...
    const instance = new _class();

'Class.isSubclass' is 'typeof(v)=="function" && v.prototype instanceof Class',
so an export that is a plain object is not a class: the page that required
the module does not open at all, and every view on that page is lost with
it.  The module that did it was skd840n/wanport.js - it ended with a plain
object literal, and the WAN page died with that TypeError while every
other page of the package opened.

The class has to be one of the two that carry a static 'extend'.  The
loader's own map is baseclass, dom, poll, request, session and view, but
'Class.extend' copies the statics onto the subclass it builds while
'Class.singleton' returns an *instance*, so dom, poll, request and session
have no 'extend' of their own: a module that extended one of them would
fail the same way.

What is checked here, and why each one is asked the way it is:

  * every module exports a class: the export has to be
    'baseclass.extend(...)' or 'view.extend(...)'.
  * the class module is required: a module that extends 'baseclass' has to
    say "'require baseclass'", or the name is not in scope.
  * every module exports exactly one thing: the first return at the top
    level of the factory is the export and any other one is dead code that
    hides what the module really hands over.
  * the modules are found: a gate that found nothing has checked nothing,
    so it has to say how many modules it read.

Usage:
  check-sk-luci-modules.py --tree TREE
"""
import argparse
import pathlib
import re
import sys

NL = chr(10)

# The classes a module can extend.  Everything else in the loader's map is
# a singleton instance, and an instance has no static 'extend'.
CLASS_MODULES = ("baseclass", "view")

# The words after which a slash opens a regular expression and not a
# division.
REGEX_AFTER = ("return", "typeof", "case", "in", "of", "new", "delete",
               "void", "instanceof", "do", "else", "yield", "throw")

EXPORT = re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*\.\s*extend\s*\(")


def blank_code(text):
    """The text with its comments and its literals blanked out.

    Every character keeps its place, so an offset into the result is an
    offset into the original, and the braces of a string or of a comment
    cannot be counted as code.
    """
    out = []
    prev = ""
    word = ""
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != NL:
                out.append(" ")
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append(NL if text[i] == NL else " ")
                i += 1
            out.append("  ")
            i += 2
            continue
        if c in "'\"`":
            quote = c
            out.append(" ")
            i += 1
            while i < n:
                if text[i] == "\\":
                    out.append("  ")
                    i += 2
                    continue
                if text[i] == quote:
                    out.append(" ")
                    i += 1
                    break
                out.append(NL if text[i] == NL else " ")
                i += 1
            prev = quote
            word = ""
            continue
        if c == "/" and (prev in "=(,:[!&|?{};+-*%~^<>" or word in REGEX_AFTER):
            out.append(" ")
            i += 1
            in_class = False
            while i < n:
                if text[i] == "\\":
                    out.append("  ")
                    i += 2
                    continue
                if text[i] == "[":
                    in_class = True
                elif text[i] == "]":
                    in_class = False
                elif text[i] == "/" and not in_class:
                    out.append(" ")
                    i += 1
                    break
                out.append(NL if text[i] == NL else " ")
                i += 1
            while i < n and text[i].isalpha():
                out.append(" ")
                i += 1
            prev = ")"
            word = ""
            continue
        out.append(c)
        if c.isalpha() or c in "_$":
            word += c
        elif c.isspace():
            pass
        else:
            word = ""
        if not c.isspace():
            prev = c
        i += 1
    return "".join(out)


def top_level_returns(code):
    """The returns that are at the top level of the factory, with their offsets.

    A return inside a function belongs to that function and says nothing
    about what the module exports, so the depth of the braces decides.
    """
    found = []
    depth = 0
    i = 0
    n = len(code)
    while i < n:
        c = code[i]
        if c == "{":
            depth += 1
            i += 1
            continue
        if c == "}":
            depth -= 1
            i += 1
            continue
        if depth == 0 and code.startswith("return", i) and not (
                i and (code[i - 1].isalnum() or code[i - 1] in "_$")):
            j = i + 6
            inner = 0
            while j < n:
                ch = code[j]
                if ch in "{([":
                    inner += 1
                elif ch in "})]":
                    inner -= 1
                elif ch == ";" and inner == 0:
                    break
                j += 1
            found.append((i, code[i + 6:j]))
            i = j
            continue
        i += 1
    return found


def line_of(text, offset):
    return text.count(NL, 0, offset) + 1


def resource_modules(tree):
    """Every LuCI resource module under the tree."""
    root = pathlib.Path(tree)
    modules = []
    for marker in sorted(root.rglob("luci-static")):
        if not marker.is_dir():
            continue
        resources = marker / "resources"
        if not resources.is_dir():
            continue
        for path in sorted(resources.rglob("*.js")):
            modules.append(path)
    return modules


def required_modules(text):
    """The names a module requires, and the aliases it gave them."""
    names = {}
    for match in re.finditer(r"^'require ([^']+)(?: as ([A-Za-z_$][\w$]*))?'", text, re.M):
        module = match.group(1)
        alias = match.group(2) or module.replace(".", "_")
        names[alias] = module
    return names


def run_checks(tree):
    results = []
    modules = resource_modules(tree)

    bad_export = []
    bad_require = []
    bad_count = []

    for path in modules:
        text = path.read_text(encoding="utf-8", errors="replace")
        code = blank_code(text)
        returns = top_level_returns(code)
        name = path.as_posix()
        if not returns:
            bad_export.append(name + " exports nothing at all")
            continue
        if len(returns) > 1:
            bad_count.append("%s has %d top level returns, the first one at line %d" % (
                name, len(returns), line_of(text, returns[0][0])))
        offset, expression = returns[0]
        match = EXPORT.match(expression)
        if not match:
            bad_export.append("%s line %d exports %s, which is not a class" % (
                name, line_of(text, offset), expression.strip()[:40] or "nothing"))
            continue
        base = match.group(1)
        if base not in CLASS_MODULES:
            bad_export.append("%s line %d extends %s, which is not one of %s" % (
                name, line_of(text, offset), base, " and ".join(CLASS_MODULES)))
            continue
        required = required_modules(text)
        if base not in required:
            bad_require.append("%s extends %s but does not require it" % (name, base))

    results.append((
        "every module exports a class",
        not bad_export,
        "%d module(s) export a class" % len(modules) if not bad_export
        else "; ".join(bad_export),
    ))

    results.append((
        "the class module is required",
        not bad_require,
        "every module requires the class it extends" if not bad_require
        else "; ".join(bad_require),
    ))

    results.append((
        "every module exports exactly one thing",
        not bad_count,
        "every module has a single export" if not bad_count
        else "; ".join(bad_count),
    ))

    results.append((
        "the modules are found",
        bool(modules),
        "%d module(s) read" % len(modules) if modules
        else "no LuCI resource module was found under " + str(tree),
    ))

    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, help="the repository")
    args = parser.parse_args(argv)

    bad = 0
    for name, ok, detail in run_checks(args.tree):
        if ok:
            print("ok   " + name + ": " + detail)
        else:
            print("FAIL " + name + ": " + detail)
            bad += 1
    if bad:
        print("check-sk-luci-modules: %d check(s) failed" % bad)
        return 1
    print("check-sk-luci-modules: every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
