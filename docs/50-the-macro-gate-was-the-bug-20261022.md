# The macro gate was the bug, three runs in a row

*2026-10-22, after run 126 failed and before run 127 was pushed.*

Runs 124, 125 and 126 each died on the same gate, in three different ways.
All three ways were bugs in the gate, not in the driver. The gate had never
once passed a build, so it had never been right.

| run | what the gate said | what was actually wrong |
|---|---|---|
| 124 | `ZX279133_SMAC_COUNT undeclared` | the gate did not exist yet; the driver really was missing an include |
| 125 | a traceback | the gate's include walk recursed until Python gave up |
| 126 | 71 problems | the gate called 71 enum members undefined, because it only knew about `#define` |

## 1. What run 126 actually said

The failure annotation on the run page is one line:

```
a driver file uses a macro it cannot see - see above
```

The "above" is the gate's own output, and the Actions log endpoints answer
404 to an anonymous client, so the annotation alone says nothing. The gate's
problems were found by running the gate here, against the tree as the CI has
it - the tree snapshot in `staging/upstream` plus the overlay on top of it,
which is the state the CI's gate sees.

All 71 problems were of one kind:

```
zx279133-netdev.c: ZX279133_STAT_COUNT is used but nothing in the tree defines it
zx279133-offload.c: ZX279133_FLOW_PORT_LAN is used but nothing in the tree defines it
```

and every one of those names is defined - as an **enum member**, in the same
file that uses it:

```c
enum {
	ZX279133_STAT_RX_PACKETS,
	...
	ZX279133_STAT_COUNT,
};
```

The gate collected definitions with one regex, `^\s*#\s*define\s+(...)`, so
every enum member looked undefined. The local push gate
(`tools/prepush.py`) has known about enum members all along, which is why it
reported "0 problems" while the CI failed: **the two gates were not the same
gate, and only one of them was in the build.**

## 2. The fix

`tools/macrocheck.py` now collects two kinds of definition:

```python
ENUM_BLOCK = re.compile(r'\benum\b[^{]*\{(.*?)\}', re.S)
ENUM_MEMBER = re.compile(r'\b(ZX279133_[A-Z0-9_]+)\s*(?:=[^,}]*)?\s*(?:,|$)', re.M)

def defines_in(text):
	names = set(DEFINE.findall(text))
	for block in ENUM_BLOCK.findall(text):
		names.update(ENUM_MEMBER.findall(block))
	return names
```

The reachability logic is untouched, and that is the part that matters: the
gate is still asking whether a name a file uses can be reached through that
file's includes, which is what run 124 was about. Changing how a definition is
*recognised* does not change whether it is *reachable*.

## 3. The gate now has a test, and the test is in the repository

`tools/test-macrocheck.py` builds a throw-away tree in a temporary directory
and asserts four things:

* a file that includes the header gets no complaint;
* a file that uses a macro **without** including the header is reported -
  this is run 124's case, and the gate has to keep catching it;
* a file that uses an **enum member** without including the header is
  reported;
* a file whose own enum supplies the name is clean - this is run 126's case.

The fixture used to live in `staging/ctl/`, outside the repository, which is
why none of the three bugs was caught before it cost an hour. It is now
`tools/test-macrocheck.py`, and the workflow runs it as its own step before
the gate:

```yaml
- name: "Gate: the macro gate is right about macros and enum members"
  run: |
    set -e
    python3 tools/test-macrocheck.py
```

## 4. A gate failure has to be readable without the job log

The gate's step now keeps the gate's exit code (a `| tee` would hide a
crash), prints the gate's output, and turns the first twenty problem lines
into annotations:

```yaml
set +e
python3 tools/macrocheck.py "$KT" > /tmp/macrocheck.txt 2>&1
rc=$?
set -e
cat /tmp/macrocheck.txt
if [ "$rc" -ne 0 ]; then
  grep -E '^  ' /tmp/macrocheck.txt | head -20 | while read -r l; do
    echo "::error::$l"
  done
  echo "::error::a driver file uses a macro it cannot see - see above"
  exit 1
fi
```

The next failure of this kind is then readable from the run page, without
anyone having to find a way into the job log.

## 5. And the push gate runs the CI's gate

`tools/prepush.py` now does two more things before its own checks:

* it runs `tools/test-macrocheck.py`, so the gate's own test is not only a
  CI step;
* it builds the tree the way the CI does - the tree snapshot plus the
  overlay on top of it - and runs `tools/macrocheck.py` against it.

The second one is the point: the local gate used to look only at the
overlay's own thirteen files, so it could never have found a problem that
only exists where the overlay meets the tree. Run 126's problem was exactly
that kind, and it took an hour of CI to find out.

On a PC without the tree snapshot - which is every PC but this one - the
second check says so and is skipped:

```
prepush: no tree snapshot at ... - the CI's macro gate was not run here
```

## 6. What this does not change

Nothing about the driver. Run 126's tree was the tree of run 123 plus the
SMAC reset pulse, the BMU buffer sizes and the BPPI threshold fixup, and
none of those is in question: the compiler never saw them, because the gate
stopped the build five minutes in. Run 127 builds the same tree, with a gate
that can say yes.
