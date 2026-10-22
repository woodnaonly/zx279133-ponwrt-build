# 47 - The macro that existed, and the file that could not see it (2026-10-22)

Run 124 failed. It failed after 78 minutes of CI, and it failed with nine
lines of the same kind of error:

```
drivers/net/ethernet/zte/zx279133-switch.c:647:29: error: 'ZX279133_SMAC_COUNT' undeclared
drivers/net/ethernet/zte/zx279133-switch.c:648:28: error: implicit declaration of function 'ZX279133_SMAC_BASE'
drivers/net/ethernet/zte/zx279133-switch.c:654:51: error: implicit declaration of function 'ZX279133_SMAC_UNI_MODE'
drivers/net/ethernet/zte/zx279133-switch.c:656:54: error: 'ZX279133_SMAC_UNI_MODE_MASK' undeclared
...
```

The macro was there. It had been added to `zx279133.h` in the same commit,
that header is in the overlay, and the overlay is copied into the kernel tree
before the build starts.

**The file simply never included the header it was in.**

---

## 1. What the tree looks like

The 9132 driver is six `.c` files and three headers. The overlay carries
five of the `.c` files and one of the headers; the rest are the tree's own.

| file | include line | `zx279133.h`? |
|---|---|---|
| `zx279133-switch.c` | `#include "zx279133-lan.h"` | **no** |
| `zx279133-netdev.c` | `#include "zx279133.h"` + `"zx279133-lan.h"` | yes |
| `zx279133-np.c` | `#include "zx279133.h"` | yes |
| `zx279133-idm.c` | `#include "zx279133.h"` | yes |
| `zx279133-main.c` | `#include "zx279133.h"` | yes |
| `zx279133-mac.c` | `#include "zx279133.h"` | yes |
| `zx279133.h` | includes `"zx279133-stats.h"` and `"zx279133-lan.h"` | - |

`zx279133-lan.h` is the tree's own header, and it does **not** include
`zx279133.h` - that is why `zx279133-netdev.c` has to include both. The
guard in `zx279133-lan.h` is what makes that safe.

So the switch driver had never seen `zx279133.h`. It compiled for every run
up to 123 because it used nothing from it. Run 124 added the SMAC constants
to `zx279133.h` and used them in `zx279133-switch.c`, and that is the first
time the file needed a header it did not have.

**Five files compiled; one did not; and the five that compiled are the ones
that made the build look healthy.**

## 2. Why nothing else caught it

The build has a gate for exactly this kind of thing:

```yaml
- name: "Gate: every header the 9132 driver includes exists"
```

It reads the `#include <...>` lines out of `zx279133-switch.c` and checks
that each one exists in the tree. It found nothing wrong, because every
header the file included was there. The file was missing a header, not
including one that was absent.

`tools/prepush.py` caught the other historical mistake of this family - two
`#define`s on one line, which made the preprocessor read the second as part
of the first one's replacement list (runs 105 to 108, four builds, four
hours). It is a syntax check, and this was not a syntax error: the file
compiled perfectly well, with the macro simply absent.

## 3. The gate that is there now

`tools/macrocheck.py` does what a compiler does, without a compiler.

It follows each driver source's `#include` graph - resolving the quoted
includes relative to the including file first, then against the tree's
include directories - and collects the `ZX279133_*` macros the headers it
can reach define. Then it reports every `ZX279133_*` name the source *uses*
that it cannot see.

The distinction between *defined somewhere* and *reachable from here* is
the whole point. A first version of the script only asked "is it defined
anywhere in the tree", and it did not catch this: the macro was defined,
just not where the file could reach it. Reachability is what the C
preprocessor implements, so reachability is what the check has to ask.

Severity is split in two, because a check that cries wolf is a check that
gets switched off:

| case | verdict |
|---|---|
| the macro is defined in the driver's own headers and the file cannot reach it | **error** - this is run 124 |
| the macro is defined somewhere else in the tree | warning - more likely a gap in the resolver than in the driver |
| the macro is defined nowhere | **error** |

It runs in CI as its own step, right after the overlay is copied into the
tree - which is the only moment when the overlay's headers and the tree's
headers are in the same place:

```yaml
- name: "Gate: every macro the 9132 driver uses is reachable"
  run: |
    KT=linux-mainline-zte-zxslc-sr1010/linux-$KVER
    python3 tools/macrocheck.py "$KT" || {
      echo "::error::a driver file uses a macro it cannot see - see above"; exit 1; }
```

The cost is a few seconds. The mistake it catches cost 78 minutes.

## 4. How the check was proved

The check is only worth having if it fails on the mistake it was written for.
So it was run against the mistake:

```
=== with the include present ===
  (nothing about the SMAC macros)

=== with the include removed ===
  zx279133-switch.c: ZX279133_SMAC_AN_CTRL is defined in the driver's own headers but not in any header this file includes
  zx279133-switch.c: ZX279133_SMAC_AN_CTRL2 is defined in the driver's own headers but not in any header this file includes
  zx279133-switch.c: ZX279133_SMAC_AN_CTRL2_VALUE is defined in the driver's own headers but not in any header this file includes
  zx279133-switch.c: ZX279133_SMAC_AN_CTRL_RESET_VALUE is defined in the driver's own headers but not in any header this file includes
  zx279133-switch.c: ZX279133_SMAC_AN_CTRL_VALUE is defined in the driver's own headers but not in any header this file includes
  zx279133-switch.c: ZX279133_SMAC_BASE is defined in the driver's own headers but not in any header this file includes
  zx279133-switch.c: ZX279133_SMAC_COUNT is defined in the driver's own headers but not in any header this file includes
  ... sixteen in all, which is exactly the set the compiler named ...
```

The fixture is a throw-away tree: the upstream snapshot plus the overlay on
top of it, in the same order the CI's `cp -a kernel-overlays/. "$KT"/` leaves
them (`staging/ctl/test-macrocheck.sh`).

## 5. The other half of run 125

Run 125 carries the same fix to the header - `zx279133-switch.c` now
includes `zx279133.h`, in the order `zx279133-netdev.c` already used - and
one more thing that belongs to the same story.

The vendor's `nppt_smac_init` does three things around the MAC's control
word, and the driver was doing only the last of them:

```
an_ctrl = 0x00ba2203      the MAC, stopped
pulse the MAC's bit in the shared soft-reset word at NPPT + 0x2c0004
an_ctrl = 0x00fa2203      the MAC, running
```

The pulse is what clears the MAC's own receive state - the buffer pointers,
the descriptor watermarks, the length bookkeeping. A MAC that was never
reset keeps whatever the power-on reset left in it, and **a frame that
arrives at such a MAC is handed on without a length**. That is exactly what
the switch's "pktlen0 drop" counter counts, and it is why the ingress
counter moves while nothing ever comes out.

The pulse clears and sets one bit of one word - the same bit the vendor
pulses - so the XMAC and the PCS are not disturbed.

`docs/46` has the rest of the SMAC work and the counter map that goes with
it.
