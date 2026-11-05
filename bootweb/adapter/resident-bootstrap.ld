/* Final dispatcher RAM address; stage0 copies it here before execution. */
ENTRY(bw_resident_command)
SECTIONS {
    . = 0x89100000;
    .text : { KEEP(*(.text.entry)) *(.text .text.*) }
    .rodata : { *(.rodata .rodata.*) }
    .data : { *(.data .data.*) }
    . = ALIGN(64);
    __bss_start = .;
    .bss (NOLOAD) : { *(.bss .bss.*) *(COMMON) }
    __bss_end = .;
    ASSERT(__bss_end <= 0x891e0000, "resident dispatcher exceeds its separate RAM slot")
    __ram_stack_bottom = 0x891f0000;
    __ram_stack_top = 0x89200000;
    /DISCARD/ : { *(.comment) *(.eh_frame*) }
}
