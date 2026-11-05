# Jitterentropy 3.4.0

Unmodified `crypto/jitterentropy.c` and `crypto/jitterentropy.h` from Linux
v6.6, commit `ffc253263a1375a65fa6c9f62a893e9767fbebfa`.
Copyright/dual BSD-GPL permission is retained in the source.

`jitterentropy.c` must be compiled separately with **-O0**. The build never
disables its optimization guard. `linux/fips.h` requests the upstream 64-bit
safety margin; it does not certify this device or firmware.

The bare-metal binding supplies CNTPCT_EL0, SHA3-256, bounded static allocation
and a hard deadline. Startup (1024 measurements), RCT and APT remain intact.
Any error disables Web entry; no MAC/time/PRNG fallback is provided.

Source SHA256: `9c5bd55fb589248b5de5d0cf7d73c66d38eba025b9c4f54527398d5f42bc679c`
Header SHA256: `9dcbc0f1b97637ca231f053f07cadbad2f7bf28fa9cd9aa8ec7fb94a1aeec43a`
