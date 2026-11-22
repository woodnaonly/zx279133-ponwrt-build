#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Shadow headers that clang can parse.

GCC accepts a struct member that is a type name without a declarator:
an anonymous member.  Clang parses that as a declaration that declares
nothing, warns, and drops the member - so clang's view of the structure
is not GCC's, and every layout assertion below such a member fails.

The kernel uses the extension in a handful of headers.  For each of
them this module writes a shadow copy in which the anonymous member is
replaced by an anonymous struct definition with the same members.  The
layout is the same, the members are still reachable as "ns->ns_id", and
clang agrees with GCC about the size of the structure.

A member whose type is a macro is left alone: "struct HASH_ALG_COMMON;"
is a macro that expands to a list of fields, so there is no anonymous
member after preprocessing.
"""
import re

BARE = re.compile(r"^(\s*)(struct|union)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$")
OPEN_KIND = re.compile(r"\b(struct|union)\b\s*([A-Za-z_][A-Za-z0-9_]*)?\s*$")


def strip_comments(line):
    """The line without a trailing comment."""
    index = line.find("/*")
    if index >= 0:
        line = line[:index]
    index = line.find("//")
    if index >= 0:
        line = line[:index]
    return line


def scan_structs(text):
    """The struct and union bodies in the text, by name.

    A definition is "struct name {" at the start of a line, up to the
    matching closing brace.  The body is what is between the braces.
    """
    bodies = {}
    for match in re.finditer(r"^(struct|union)\s+([A-Za-z_][A-Za-z0-9_]*)[^;{]*\{",
                             text, re.M):
        kind = match.group(1)
        name = match.group(2)
        start = match.end()
        depth = 1
        index = start
        length = len(text)
        while index < length and depth:
            char = text[index]
            if char == "/" and text.startswith("/*", index):
                end = text.find("*/", index + 2)
                index = length if end < 0 else end + 2
                continue
            if char == "/" and text.startswith("//", index):
                end = text.find("\n", index)
                index = length if end < 0 else end
                continue
            if char in "\"'":
                quote = char
                index += 1
                while index < length:
                    if text[index] == "\\":
                        index += 2
                        continue
                    if text[index] == quote:
                        index += 1
                        break
                    index += 1
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        if depth == 0:
            bodies.setdefault(name, text[start:index])
    return bodies


def scan_members(text):
    """The bare type members in the text.

    Each one is (line number, type name, indent).  Only members inside a
    struct or a union body count: a forward declaration at file scope
    looks the same but is not an anonymous member.
    """
    members = []
    kinds = []
    depth = 0
    line = 1
    index = 0
    length = len(text)
    line_start = True
    while index < length:
        char = text[index]
        if char == "\n":
            line += 1
            line_start = True
            index += 1
            continue
        if char == "/" and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end < 0 else end + 2
            line += text.count("\n", index, end)
            index = end
            continue
        if char == "/" and text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end < 0 else end
            continue
        if char in "\"'":
            quote = char
            index += 1
            while index < length:
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        if line_start and char in " \t":
            index += 1
            continue
        if line_start:
            line_start = False
            if kinds and kinds[-1] in ("struct", "union"):
                end = text.find("\n", index)
                statement = text[index:end if end >= 0 else length]
                match = BARE.match(statement)
                if match:
                    members.append((line, match.group(3), match.group(1)))
                    index = end if end >= 0 else length
                    continue
        if char == "{":
            before = text[max(0, index - 80):index].rstrip()
            match = OPEN_KIND.search(before)
            kinds.append(match.group(1) if match else "other")
            depth += 1
            index += 1
            continue
        if char == "}":
            if kinds:
                kinds.pop()
            depth -= 1
            index += 1
            continue
        index += 1
    return members


def match_brace(text, start):
    """The index of the brace that closes the one at start, and its body."""
    depth = 1
    index = start + 1
    length = len(text)
    while index < length:
        char = text[index]
        if char == "/" and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        if char == "/" and text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end < 0 else end
            continue
        if char in "\"'":
            quote = char
            index += 1
            while index < length:
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index, text[start + 1:index]
        index += 1
    return -1, ""


def scan_nested_defs(text):
    """The type definitions that sit inside a struct or a union body.

    Each one is (line, name, indent, end line, body).  GCC treats such a
    definition as an anonymous member: the type is declared and the member
    is reachable without a name; clang drops the definition and the members
    are gone.
    """
    found = []
    kinds = []
    line = 1
    index = 0
    length = len(text)
    line_start = True
    while index < length:
        char = text[index]
        if char == "\n":
            line += 1
            line_start = True
            index += 1
            continue
        if char == "/" and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end < 0 else end + 2
            line += text.count("\n", index, end)
            index = end
            continue
        if char == "/" and text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end < 0 else end
            continue
        if char in "\"'":
            quote = char
            index += 1
            while index < length:
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        if line_start and char in " \t":
            index += 1
            continue
        if line_start:
            line_start = False
            if kinds and kinds[-1] in ("struct", "union"):
                end = text.find("\n", index)
                statement = text[index:end if end >= 0 else length]
                match = re.match(r"^(struct|union)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{",
                                 statement)
                if match:
                    brace = text.index("{", index)
                    close, body = match_brace(text, brace)
                    if close > 0:
                        end_line = text.count("\n", 0, close) + 1
                        found.append((line, match.group(2),
                                      statement[:len(statement)
                                                - len(statement.lstrip())],
                                      end_line, body))
                    index = end if end >= 0 else length
                    continue
        if char == "{":
            before = text[max(0, index - 80):index].rstrip()
            match = OPEN_KIND.search(before)
            kinds.append(match.group(1) if match else "other")
            index += 1
            continue
        if char == "}":
            if kinds:
                kinds.pop()
            index += 1
            continue
        index += 1
    return found


def macro_names(text):
    """The macro names the file defines."""
    names = set()
    for match in re.finditer(r"^#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.M):
        names.add(match.group(1))
    return names


def rewrite(text, bodies, macros):
    """The text with every anonymous member replaced by a definition.

    Two kinds are handled.  A member that is a bare type name becomes an
    anonymous struct with the type's members.  A member that is a type
    definition keeps its name - with a declarator, so the name still
    works - and gains an anonymous copy, because that is what makes the
    members reachable without a name.
    """
    edits = []
    skipped = []
    for number, name, indent in scan_members(text):
        if name in macros:
            skipped.append((number, name, "macro"))
            continue
        if name not in bodies:
            skipped.append((number, name, "no definition"))
            continue
        body = bodies[name]
        inner = "".join(indent + "\t" + row + "\n" for row in body.splitlines())
        replacement = indent + "struct {\n" + inner + indent + "};\n"
        edits.append((number, number, replacement.splitlines()))
    for number, name, indent, end_line, body in scan_nested_defs(text):
        if name in macros:
            # The name is an attribute macro - __packed and friends expand to
            # nothing - so the definition is an anonymous one already, and
            # clang parses that shape on its own.
            skipped.append((number, name, "attribute macro, left alone"))
            continue
        closing = text.splitlines()[end_line - 1].rstrip()
        if not closing.endswith("};"):
            skipped.append((number, name, "the closing line is not a plain }"))
            continue
        inner = "".join(indent + "\t" + row + "\n" for row in body.splitlines())
        original = text.splitlines()[number - 1:end_line]
        named = list(original)
        named[-1] = closing[:-1] + " __kshadow_%s;" % name
        anonymous = [indent + "struct {"] + inner.rstrip("\n").split("\n") + \
                    [indent + "};"]
        edits.append((number, end_line, named + anonymous))
    if not edits:
        return None, []
    lines = text.splitlines()
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start - 1:end] = replacement
    return "\n".join(lines) + "\n", skipped
