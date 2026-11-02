/* Development-only placement. Not a vendor BTL container or flash payload. */
ENTRY(bootweb_command)
EXTERN(bw_lwip_bind)
SECTIONS {
    . = 0x89000000;
    .text : { KEEP(*(.text.entry)) *(.text .text.*) }
    .rodata : { *(.rodata .rodata.*) }
    .data : { *(.data .data.*) }
    . = ALIGN(16);
    __bss_start = .;
    .bss (NOLOAD) : { *(.bss .bss.*) *(COMMON) }
    __bss_end = .;
    /DISCARD/ : { *(.comment) *(.eh_frame*) }
}
