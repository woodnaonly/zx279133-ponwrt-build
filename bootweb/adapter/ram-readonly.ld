/* Explicit RAM placement is mandatory. A link address proves NO reservation. */
ENTRY(bootweb_command)
EXTERN(bw_lwip_bind)
ASSERT(DEFINED(BW_RAM_BASE), "set BW_RAM_BASE only from an inspected RAM map")
SECTIONS {
    . = BW_RAM_BASE;
    .text : { KEEP(*(.text.entry)) *(.text .text.*) }
    .rodata : { *(.rodata .rodata.*) }
    .data : { *(.data .data.*) }
    . = ALIGN(64);
    __bss_start = .;
    .bss (NOLOAD) : { *(.bss .bss.*) *(COMMON) }
    __bss_end = .;
    . = ALIGN(64);
    .ram_stack (NOLOAD) : {
        __ram_stack_bottom = .;
        . += 0x10000;
        __ram_stack_top = .;
    }
    __image_end = ALIGN(64);
    ASSERT(__image_end <= BW_RAM_BASE + 0x200000, "RAM image and private stack exceed verified reservation")
    /DISCARD/ : { *(.comment) *(.eh_frame*) }
}
