# The frames the board received were not the frames

**2026-10-24.** The receive path of this driver has never delivered a
frame. Three faults have been found on it, and this is the third - the one
that was under everything else.

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

## What the descriptor says

The run 153 build printed the descriptor of a received frame, and it is a
good one:

    descriptor: dma 84265100 len 92 word1 0782005c meta0 00000000
                meta1 c0a801ff meta2 00000000 meta3 00000000

* `dma 84265100` is a page plus 0x100 - the page-pool page the driver
  handed the hardware, plus `ZX279133_IDM_RX_PAYLOAD_OFFSET`, which is
  exactly what `__zx279133_idm_rx_post_page()` puts into the buffer
  pointer ring, and exactly what the driver's own `valid_dma` check
  insists on. It passes, for every frame.
* `len 92` is a frame length.
* `word1 0782005c` carries that length in its low fourteen bits.
* `meta1 c0a801ff` is the frame's own address - `c0 a8` is 192.168.

So the hardware took the buffer the driver gave it, parsed the frame in
it, and wrote the descriptor. The frame is in the buffer. And the 92 bytes
at that address are **all zero**:

    bytes (92): 00000000...0000

Zero is not a frame, and it is not what a frame would look like after the
stack had touched it either.

## The cause

**The CPU was reading its own cache.**

The page the hardware writes into is ordinary cacheable memory. The
hardware writes it behind the CPU's back, so the CPU's copy of it is stale
until the cache line is given back - and the copy the CPU had was the
page's *previous* contents: the initramfs' ASCII text in one run, the
switch's port bitmaps in another. Those are exactly the "source MAC
addresses" the bridge learned, and exactly what a page that has been used
for something else holds.

The invalidation was in the wrong place, by exactly the size of the
offset:

    page_pool_dma_sync_for_cpu(pool, page, offset, len)

`page_pool_dma_sync_for_cpu()` **adds the pool's own offset to the one it
is handed**. The pool was created with

    .offset = ZX279133_IDM_RX_PAYLOAD_OFFSET

so the helper invalidates

    [ dma + offset + pool->p.offset , +len )

and the frame is at `dma + PAYLOAD_OFFSET`. The offset to pass is
therefore **zero**. The fault (`2d11bbd`) changed it from zero to
`PAYLOAD_OFFSET`, which moved the invalidated range from the frame to the
**256 bytes after the frame**, where there is nothing at all.

That is the whole fault: the frame was never given back to the CPU, so the
stack read the page's leftovers, and the counters were perfect throughout
because they count frames and the frames had arrived.

## The other fault that was in the same place

The frame is not at the start of the page it arrives in - the buffer
pointer ring is given `page_pool_get_dma_addr(page) +
ZX279133_IDM_RX_PAYLOAD_OFFSET`, so the hardware writes the frame 256
bytes into the page, and the descriptor's address field says so. The old
reading of the fault was "the range that has to be invalidated is
`[256, 256 + len)` and the range that was invalidated was `[0, len)`". The
helper adds the pool's offset by itself, so `[0, len)` was in fact
`[256, 256 + len)` and was **right**; the change made it wrong. The
driver's comment on that call said the opposite, and the change was made
on the strength of it.

## The build that never ran

The two runs after that (`155`, `156`, `157`) never reached the board,
because the diagnostic that was added with them did not compile:

    dma_sync_single_range_for_cpu(eth->dev, addr, size, DMA_BIDIRECTIONAL);

In 6.18 that helper takes **five** arguments -
`dma_sync_single_range_for_cpu(dev, addr, offset, size, dir)` - and it
invalidates `[addr + offset, +size)`. Four arguments is a hard error, and
the CI's Actions log is not readable without a token, so the error text
could not be read; it was found by reading the header of the exact kernel
the build uses. The call has been given the fifth argument and the
diagnostic now also prints, from the same header, what
`page_pool_dma_sync_for_cpu()` does with the offset.

## What the next build measures

The BMU's own buffer pool has never actually been scanned: the build that
would have scanned it did not compile. The scan is still in the tree, and
it now reads the pool through `readb()` rather than through a pointer
cast out of `__iomem`.

The new part is the second view of the frame. `rxb` prints the frame
**twice**:

| line | what it is |
|---|---|
| `frame` | the bytes the data path read - the range the pool's helper gave back, and nothing else |
| `raw` | the same bytes after the whole page has been given back by the low-level call, the one that does not check the device's own flags |

and it prints, before them, whether the kernel will invalidate at all
(`dma_dev_need_sync()`, and the device's `dma_skip_sync`), because if the
device is taken to be coherent, or the kernel was built without
`CONFIG_DMA_NEED_SYNC`, the call is a no-op and the frame is lost in a
way no counter shows.

So the two views say which of the two faults this is:

| `frame` | `raw` | what it means |
|---|---|---|
| zeros | the frame | the CPU's cache was stale; the invalidation has to be forced |
| zeros | zeros | the frame really is not in that buffer, and the pool scan says where it is |
| the frame | the frame | the data path is right; the fault was elsewhere |

Both views are taken **inside the receive path**, and that is not a
detail: the page is handed back to the pool as soon as the stack is done
with it, so a console command that read the same page afterwards would
read whichever frame came next. For the same reason the earlier idea of
reading the page through an `ioremap()` was dropped - this architecture
refuses to map RAM as device memory at all - and the invalidation is
what does the work.

## What the vendor's own module says about the cache

The vendor's module carries two wrappers of its own,
`zxOsCacheInv` and `zxOsCacheFlush`, and it calls them in exactly two
places: `se_ddr_data_read()` and `se_ddr_data_write()`, the two helpers
that read and write the switch engine's own DDR window. That is the
hardware's window and not the host's receive buffers, so it does not
settle this by itself, but it does say that the vendor does not trust
the CPU's cache against this chip.

## What was ruled out on the way

* **The BMU's buffer-pointer table is the hardware's.** It can be written
  from the console in four formats and reads back the same in all of them
  (0x0a000900, 0x0c000b00, ...), so the hardware maintains it and the
  driver's identity map in it is transient.
* **The port attribute words are the vendor's, word for word.** The
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
* **The 0x40 in the board's own loader is a red herring.** The loader's
  network stack is the **PON** one, and the PON's frame is at the buffer
  plus 0x40. The 9132's own ports deliver the frame differently, and the
  descriptor says exactly where: at the address the driver handed over.

## What this says about the earlier rounds

The counters that the earlier rounds took as proof that the datapath works
- the ISU taking 7625 frames and dropping none, the ODMA taking 7625, the
RED forwarding 7578 - all count *frames*. They say the switch moved the
frames; they never said the host could read them.

So the receive path of this driver has never been correct, end to end, in
any build: the microcode was wrong, the dispatch was missing, and the
content was wrong. The three faults were independent and all three had to
be fixed before the board could answer a ping.
