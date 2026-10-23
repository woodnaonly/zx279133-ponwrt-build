# The frames the board received were not the frames

**2026-10-24.** The third fault behind the G2 gate, and the one that
explains every symptom that was left over after the microcode and the DSA
dispatch were fixed.

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

Two things follow from that table. The frames *arrive* - the counters say
so - and they arrive on the right port: the ASCII entries were learned on
bridge port 2, which is `lan2`, and that is the jack the test PC is in. So
the port mapping (the PPU's port number to the transport VID to the DSA
port to the netdev) and the DSA dispatch of `51ac6c6` are both right. What
the stack was handed was not the frame.

## The cause

The frame is not at the start of the page it arrives in.

The buffer pointer ring is given `page_pool_get_dma_addr(page) +
ZX279133_IDM_RX_PAYLOAD_OFFSET` (`__zx279133_idm_rx_post_page()`), so the
hardware writes the frame at that offset - 256 bytes into the page - and the
descriptor's address field says so, which is what the driver's own check
(`valid_dma`) insists on. The driver reads the frame from the same place:
`data = page_address(page) + ZX279133_IDM_RX_PAYLOAD_OFFSET`.

The CPU-side cache maintenance did not agree with that:

    page_pool_dma_sync_for_cpu(zx279133_rx_page_pool(page), page, 0, len);

Offset zero, length `len`. The frame is at offset 256 and is `len` bytes
long, so the range that has to be invalidated is
`[256, 256 + len)` - and the range that was invalidated was `[0, len)`.
Every frame on this wire is shorter than 256 bytes (an ARP request is 60, a
ping is 98), so **not one byte of any frame was ever invalidated**. The CPU
read the page's old contents.

What those contents are, on this board, is the initramfs: the pages the
page pool hands out are ordinary pages, and the kernel's initramfs is
unpacked into ordinary pages, so the text of the files in it - "small",
".rx_ta", "let di" - is what the bridge learned as source addresses.

The pool cannot cover for this either. It is created with
`PP_FLAG_DMA_MAP | PP_FLAG_DMA_SYNC_DEV` and without
`PP_FLAG_DMA_SYNC_CPU`, and its device-side sync only runs when a page goes
back to the pool, which for a received frame is after the stack has read it.

## The fix

    page_pool_dma_sync_for_cpu(zx279133_rx_page_pool(page), page,
                               ZX279133_IDM_RX_PAYLOAD_OFFSET, len);

One line, and it is in the right place: it is before the XDP program runs
on the data and before the skb is built over the page, so it covers the
direct path and the copy fallback alike. The fallback path had the same
fault in a worse place - it copied the frame out of the page *before* any
CPU-side invalidate at all - and it is covered by the same call now.

## What this says about the earlier rounds

The counters that the earlier rounds took as proof that the datapath works
- the ISU taking 7625 frames and dropping none, the ODMA taking 7625, the
RED forwarding 7578 - all count *frames*. They say the switch moved the
frames; they never said the host could read them, because the host's copy
of a frame was never the frame.

So the receive path of this driver has never been correct, end to end, in
any build: the microcode was wrong, the dispatch was missing, and the
content was wrong. The three faults were independent and all three had to
be fixed before the board could answer a ping.

## Still to be proven

This is the fix, not the proof. The G2 gate is the acceptance in
`staging/ctl/accept4.py`: the board answers the PC's ping, the frames reach
the jack they came in on, and two cold boots agree.
