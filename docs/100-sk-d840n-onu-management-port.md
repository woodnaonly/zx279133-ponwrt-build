# SK-D840N ONU management port

The image now selects the eight ONU/CPU views, the common Rust OMCI engine,
Argon and firewall4. This is not a working-line acceptance report. In
particular, the daemon cannot yet authenticate and answer an OLT request
automatically: optical activation, RX demultiplexing and the OMCI integrity-key
lifecycle are still missing.

## Sources and scope

The donor is the local PonWRT-TYFZA1 0.8.4-beta snapshot. Its OMCI upstream is
`pbs05/openwrt-pon-userspace` at
`cce9d756742487b378749c606b30e13ee9b0f093`; the embedded crate retains its
original 0.8.2-beta version. The source declarations and GPLv2 text are kept
beside both imported components. `UPSTREAM` records the donor and upstream
history. No vendor modules, calibration or device identities are distributed.

`luci-app-sk-d840n-pon` keeps `admin/onu` and uses `skd840n` modules. The status,
hardware, authentication, internet, IPTV, acceleration, diagnostics and CPU
pages read SK-D840N interfaces rather than Realtek addresses. Internet/IPTV
forms edit existing WAN/bridge sections only. There is no mode switch, port
renumbering, bridge creation or frequency-control driver.

Argon is pinned to `539e3e1db56b2348c3655615540e45638b797c73`. This revision
integrates its config app, so the build removes only the managed old LuCI feed
symlinks and scans the theme's upstream compatibility recipe instead. The
first-boot marker preserves later user theme choices.

## OMCI TX: correction to the earlier feasibility note

The stub `fi_Configure_Ponmac_Send_Omci_Msg` is not the installed netdev TX
entry. The earlier inference that the vendor never transmitted OMCI was
incorrect. In the local `np_133.ko`, `idm_net_init` creates private-type-2
`omci`; `.rodata+0x12c8` installs `idm_net_cpu_tx` at `.text+0x11fa4`.
Its type-2 branch at `.text+0x12e00` checks registration, invokes the MIC
callback, reads the OMCC GEM and submits the dedicated IDM queue 0.

The new driver follows that submission layout: a 48-byte baseline frame,
length word `0x0f000060`, control word `0x00400001`, GEM word
`0x1e070000 | gem`, doorbell at IDM+0x80 and completion at IDM+0x84. It does
not pad the packet to Ethernet's minimum length or touch LAN queues 1..3.
A timeout retains the coherent payload until a later completion; it is not
reported as success. The existing datapath lock serializes submission and
shutdown.

Root-only `zxpond --send-omci` accepts exactly one 48-byte baseline
response/notification on stdin, already carrying the correct MIC. It uses
the privileged `zxpon` generic-netlink `OMCI_TX` command. The kernel requires
O5, a known OMCC GEM and a prepared TX datapath; absent prerequisites return
an error. ACK means IDM completion, not proof of optical transmission or OLT
acceptance. The `omci` netdev remains an unavailable RX endpoint, not a
second daemon or a falsely successful packet-socket TX path.

The Rust engine is linked into the sole `zxpond` process. Hardware-mutating
MEs return command-not-supported before state or replay cache commits,
including disconnected GEM/T-CONT graphs. The software model does not
substitute for hardware tables. The default process publishes configuration
and engine readiness but does not claim automatic authenticated OMCI I/O.

## Configuration and visibility

`/etc/config/pon` owns local identity and protocol settings. First migration
parses, rather than sources, `/etc/pon/identity`; it preserves the original
and never replaces an existing UCI file. Both credential files are mode
0600. SN and REGID retain their original byte encodings with strict length
checks. An absent identity does not select a donor/default identity.

`luci.skd840n-pon.status` and `luci.skd840n-system.status` are read-only.
Missing nodes and stale daemon/PID markers produce unavailable values.
Ethernet carrier and the software O-state are not evidence of registration.
Diagnostic downloads contain only explicitly allowed status fields, never
SN, REGID, PSK, LOID passwords or arbitrary files/register dumps.

Firewall4 replaces the legacy single-rule NAT service. Its nft dependencies
are built into this external kernel and checked after the merge. The
upgrade removes an old NAT table only after matching both service ownership
and its exact JSON rule shape. Custom tables are preserved. Hardware
offload remains read-only and unverified; installing firewall4 does not
establish offload acceptance.

## Verification

`test_sk_pon_ui.js` renders every view under four platform/error fixtures.
`test_sk_pon_host.sh` exercises migration, malformed frames, the production
netlink sender, descriptor words and Rust/C transaction rollback.
`test_sk_pon_rpc.py` executes the production ucode RPCs with isolated
proc/sysfs fixtures. `test_sk_pon_firstboot.py` checks one-time theme choice
and conservative NAT ownership matching.

The Actions management gate checks the actual staged root and image
manifest, runs the RPC fixtures with the build's ucode, and runs the pinned
firewall4 upstream tests. Existing image-size/recovery checks remain in
place; flash images must still exclude `zrec`. CI compilation and real
optical/hardware-offload acceptance must be reported separately.
