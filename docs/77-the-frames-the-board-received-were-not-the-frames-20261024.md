# The frames the board received were not the frames

**2026-10-24.** The receive path of this driver has never delivered a
frame. Three faults have been found on it, and this is the third - the one
that was under everything else.  It turned out to be two faults, and the
second one was found by the first one's fix being checked.

## What was measured

With the board's own microcode and the DSA dispatch in place (run 150,
`51ac6c6`), the datapath looked perfect and the board still answered no
ping:

| what | what it said |
|---|---|
| the PC pings the board | 4 sent, 0 answered; the PC's ARP table holds no entry for 192.168.9.1 |
| the driver's own counter on the conduit | 3012 frames received during a 3000-frame flood - every frame arrived |
| the bridge's forwarding database | source MAC addresses that are **ASCII text**: `20:73:6d:61:6c:6c` is "small", `2e:72:78:5f:74:61` is ".rx_ta", `6c:65:74:20:64:69` is "let di" |
| the test PC's own MAC (`84:47:09:5b:bd:2c`) | **absent** from the forwarding database |
| the board's ARP table | one entry, for the board's own trial client; never an entry for the test PC |
| the board's own trial client, over a veth | its DHCP lease and its traffic work - the board's stack is fine |

Two things follow. The frames *arrive* - the counters say so - and they
arrive on the right port: the ASCII entries were learned on bridge port 2,
which is `lan2`, and that is the jack the test PC is in. The port mapping
(the PPU's port number to the transport VID to the DSA port to the netdev)
and the DSA dispatch of `51ac6c6` are therefore both right. What the
stack was handed was not the frame.

## The cause

**The frame is not at the head of the buffer the driver handed the
hardware.**

The driver gives the IDM a page-pool page plus 256 bytes
(`__zx279133_idm_rx_post_page()`), and the descriptor that comes back names
that address - the driver's own check insists on it, and it passes for
every frame.  The frame is 128 bytes further in.

The run 160 build printed the page, and the frame was in it:

```
page +0180: ffffffffffffff8447095bbd2c08060001
page +0190: 0800060400018447095bbd2cc0a80919
page +01a0: 000000000000c0a80901000000000000
```

which is, in full, an ARP request from the test PC:

```
ff ff ff ff ff ff | 84:47:09:5b:bd:2c | 08 06 | 00 01 08 00 06 04 00 01
| 84:47:09:5b:bd:2c | c0 a8 09 19 | 00 00 00 00 00 00 | c0 a8 09 01
```

destination broadcast, source the PC's own MAC, ARP, hardware type
Ethernet, protocol IPv4, hlen 6, plen 4, operation *request*, sender
192.168.9.25, target hardware all zeroes, target protocol 192.168.9.1 -
the board.  A second frame, an IP/UDP datagram with an HTTP request in
it, landed in the same place, with the same 128-byte gap in front of it.

So the hardware did everything right: it took the buffer the driver
offered, it parsed the frame, and it wrote the frame into it.  The
descriptor's address names the *buffer*; the frame follows the switch
engine's own area, 128 bytes further in.  The driver read the head of
the buffer, which held whatever the page had held before - the
initramfs' ASCII text in one run, zeros in another, 0xff in a third.
**Every frame the stack was handed was the page's leftovers, and every
counter was perfect throughout, because the frames had really arrived.**

The fix is `ZX279133_IDM_RX_FRAME_OFFSET` (0x80): the data pointer, the
prefetch, the XDP path and the cache invalidation all move by it.

## The fault that was under it, and was not the cause

The CPU-side cache invalidation was in the wrong place as well, and it was
a fault on its own account - the frames the stack would have read even
at the right offset would have been stale.

    page_pool_dma_sync_for_cpu(pool, page, offset, len)

adds the pool's own offset to the offset it is handed: the pool was
created with `.offset = ZX279133_IDM_RX_PAYLOAD_OFFSET`, so it
invalidates `[dma + offset + pool->p.offset, +len)`.  The offset to pass
is therefore **zero**, and `2d11bbd` had changed it to
`PAYLOAD_OFFSET`, which put the invalidated range 256 bytes *after* the
frame.

**This was not the cause of the garbage**, and the measurement says so:
run 160 printed the frame twice - once as the data path had it and once
after the whole page had been given back - and the two were identical,
and the page dump agreed with both.  The cache was not stale; the bytes
were simply the wrong bytes.  A stale cache and a wrong offset look the
same from the stack's side, which is why the first half of this round
chased the cache.

The invalidation is fixed anyway (`0968d2d`, and `485f65d` for the
device's own coherent flag), because with the frame at buffer + 0x80 the
range to invalidate is `[dma + 0x100 + 0x80, +len)`.

## The compile error that kept two builds from running

`dma_sync_single_range_for_cpu()` in 6.18 takes **five** arguments -
`(dev, addr, offset, size, dir)` - and the diagnostic called it with
four, so runs 156 and 157 never compiled.  The CI's Actions log is not
readable without a token, so the error text could not be read; it was
found by reading the header of the exact kernel the build uses.

## What was ruled out on the way

* **The frame is in the BMU's own buffer pool.**  That was the reading
  for two rounds, and it is wrong: the frame is in the page the
  descriptor names, 128 bytes in.  The pool scan finds only the 0xff
  fill.
* **The board's own loader's 0x40 is not a red herring.**  It is the
  same phenomenon with a different number: the loader's network stack is
  the **PON** one and the PON's frame is at the buffer plus 0x40.  What
  was wrong was the conclusion drawn from it - "the frame is somewhere
  else entirely" - rather than the observation.
* **The port attribute words are the vendor's, word for word.**  The
  console dump of the SPA window shows `a0 = 0x0000a6a5`,
  `a1 = 0x0000088f`, `a2 = 0`, `a9 = 0`, `a10 = 0x0fa0000e`,
  `a13 = 0x00008008`, `a19 = 0x96040001` on the four UNI ports - the
  values docs/15 lists for the vendor's own working system. The IDM's own
  configuration is the vendor's too: the control word 0x01ff1007, the
  descriptor mode 0, the depths, and the buffer pool pointers.
* **The MAC-width ERAM tables are empty.** Tables 0x1e, 0x21 and 0x2b read
  back as zero, and so do the 128-bit tables 0x00, 0x06, 0x22 and 0x2a.
* **The trap table is not the problem.** `trapall 2 2` (trap every
  protocol) delivers exactly as many frames as the default.
* **The SA-lookup bits are not the problem.** Clearing bit 28, bit 31 or
  both in the MF copy of the port's attribute word changes nothing.
* **The frames come from the client's own port.** `echo "trapall 2 0" >/d`
  stops them: the driver's counter does not move at all.
* **The vendor's module does not trust the CPU's cache either.** It
  carries `zxOsCacheInv` and `zxOsCacheFlush` of its own and calls them
  in `se_ddr_data_read()` and `se_ddr_data_write()`, the two helpers that
  read and write the switch engine's DDR window.  That is the hardware's
  window and not the host's receive buffers, so it does not settle this,
  but it does say the vendor does not treat the chip as cache coherent.

## What this says about the earlier rounds

The counters that the earlier rounds took as proof that the datapath works
- the ISU taking 7625 frames and dropping none, the ODMA taking 7625, the
RED forwarding 7578 - all count *frames*. They say the switch moved the
frames; they never said the host could read them.

So the receive path of this driver has never been correct, end to end, in
any build: the microcode was wrong, the dispatch was missing, and the
content was read from the wrong place in the buffer. The three faults
were independent and all three had to be fixed before the board could
answer a ping.
