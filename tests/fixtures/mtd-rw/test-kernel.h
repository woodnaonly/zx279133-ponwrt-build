/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef MTD_RW_TEST_KERNEL_H
#define MTD_RW_TEST_KERNEL_H
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define MODULE 1
#define __init
#define __exit
#define KERN_WARNING ""
#define KERN_INFO ""
#define KERN_ERR ""
#define S_IRUGO 0444
#define MTD_WRITEABLE 0x400u
#define IS_ERR(pointer) ((uintptr_t)(pointer) >= (uintptr_t)-4095)
#define PTR_ERR(pointer) ((long)(intptr_t)(pointer))
#define module_param(...)
#define MODULE_PARM_DESC(...)
#define module_init(...)
#define module_exit(...)
#define MODULE_LICENSE(...)
#define MODULE_AUTHOR(...)
#define MODULE_DESCRIPTION(...)
#define MODULE_VERSION(...)
struct mtd_info { unsigned flags; };
static struct mtd_info *get_mtd_device(void *, unsigned);
static void put_mtd_device(struct mtd_info *);
static void printk(const char *, ...);
#endif
