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

## The cause

**The hardware does not write the frame at the address the buffer pointer
ring carries. It writes it 0x40 bytes further in.**

The board's own loader says so, and it says it with the frame in front of
you. The loader has the same chip and the same 9132, and its IDM receive
debug (`idm_rx_debug 1`, then a `ping` to start its network stack) prints
the descriptor and the buffer it filled in:

    rx desc address 0x00000000b0028000:
      0xb00e8000        <- the descriptor's address field: the buffer
      0x0782003c        <- length 0x3c = 60, source port 2, output 15
      ...
    address 0xb00e8000, addr_type 0, ptk_len 60, ... source_port 2, ... output 15
    buff address : 0x00000000b00e8040, len : 60

     00 29 47 00 00 32 84 47
     09 5b bd 2c 08 06 00 01
     08 00 06 04 00 02 84 47
     09 5b bd 2c c0 a8 09 19
     00 29 47 00 00 32 c0 a8
     01 01 00 00 00 00 00 00

That is a complete, correct Ethernet frame: the board's own address as the
destination, the test PC's address as the source, `0x0806` as the type, and
an ARP reply from 192.168.9.25. The **buffer** is at `0xb00e8040` and the
**descriptor** names `0xb00e8000` - the same address 0x40 bytes earlier.

The driver read at the descriptor's address. So the stack was handed 64
bytes that were never written - whatever the page held before - followed
by the first 42 bytes of the frame. The protocol field it read out of that
was garbage, so the frame was dropped; and the source address the bridge
learned out of it was the garbage's bytes 6..11. On this board the garbage
is ASCII text, because the pages the pool hands out are ordinary pages and
the initramfs was unpacked into ordinary pages.

Everything fits, including the numbers that did not fit anything else:

* the descriptor's length is right (106 for a 106-byte frame, 60 for the
  60-byte ARP reply) - only the *address* was wrong, so every counter was
  perfect;
* the chip's own SMAC byte counter counted 100 bytes for the 78-byte
  frames the PC sent, which is the frame plus the six bytes it does not
  count and the padding - the chip was never adding anything;
* the frames came from the client's own port (`echo "trapall 2 0" >/d`
  stops them: the driver's counter does not move at all), and the
  destination address in the dumped frame is the board's own - the 9132
  had already rewritten it.

## The fix

`ZX279133_IDM_RX_FRAME_OFFSET` (0x40), added to the three places that
read the frame:

* the CPU-side invalidate, which has to cover the range the device
  actually wrote - `[256 + 0x40, 256 + 0x40 + len)`;
* the prefetch, for the same reason;
* the address the skb and the XDP program are built on.

The XSK path moves its buffer's data pointer by the same 0x40, so the XDP
program there sees the frame as well.

## The other fault that was in the same place

The frame is not at the start of the page it arrives in, either. The
buffer pointer ring is given `page_pool_get_dma_addr(page) +
ZX279133_IDM_RX_PAYLOAD_OFFSET`, so the hardware writes the frame at that
offset - 256 bytes into the page - and the descriptor's address field
says so, which is what the driver's own check (`valid_dma`) insists on.
The CPU-side cache maintenance did not agree with that:

    page_pool_dma_sync_for_cpu(zx279133_rx_page_pool(page), page, 0, len);

Offset zero, length `len`. The range that has to be invalidated is
`[256, 256 + len)` - and the range that was invalidated was `[0, len)`.
Every frame on this wire is shorter than 256 bytes (an ARP request is 60, a
ping is 98), so **not one byte of any frame was given back to the CPU**.

That is a real fault and it is fixed (`2d11bbd`). It was not the cause of
the garbage: run 151, with that fix, produced the same ASCII source MAC
addresses in the same bridge. The frames were never stale cache; they were
the wrong bytes.

## What was ruled out on the way

* **The BMU's buffer-pointer table is the hardware's.** It can be written
  from the console in four formats and reads back the same in all of them
  (0x0a000900, 0x0c000b00, ...), so the hardware maintains it and the
  driver's identity map in it is transient.
* **The port attribute words are the vendor's, word for word.** The
  console dump of the SPA window shows `a0 = 0x0000a6a5`,
  `a1 = 0x0000088f`, `a2 = 0`, `a9 = 0`, `a10 = 0x0fa0000e`,
  `a13 = 0x00008008`, `a19 = 0x96040001` on the four UNI ports - the
  values docs/15 lists for the vendor's own working system.
* **The MAC-width ERAM tables are empty.** Tables 0x1e, 0x21 and 0x2b read
  back as zero, and so do the 128-bit tables 0x00, 0x06, 0x22 and 0x2a.
* **The trap table is not the problem.** `trapall 2 2` (trap every
  protocol) delivers exactly as many frames as the default.
* **The SA-lookup bits are not the problem.** Clearing bit 28, bit 31 or
  both in the MF copy of the port's attribute word changes nothing.

## What this says about the earlier rounds

The counters that the earlier rounds took as proof that the datapath works
- the ISU taking 7625 frames and dropping none, the ODMA taking 7625, the
RED forwarding 7578 - all count *frames*. They say the switch moved the
frames; they never said the host could read them.

So the receive path of this driver has never been correct, end to end, in
any build: the microcode was wrong, the dispatch was missing, and the
content was wrong. The three faults were independent and all three had to
be fixed before the board could answer a ping.
