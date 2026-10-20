# SPDX-License-Identifier: GPL-2.0
#
# This file replaces the kernel tree's drivers/net/ethernet/zte/Makefile
# wholesale, because the overlay is applied with `cp -a kernel-overlays/. $KT/`
# and a shadowed file has to carry the tree's own content as well.  The tree
# content below is the one from
# cnjn/linux-mainline-zte-zxslc-sr1010 at 96e5a2413524742e177e383fe523ef21de5ed238,
# plus the SK-D840N's own switch driver.

obj-$(CONFIG_ZTE_ZX279133_ETH) += zx279133.o
zx279133-y := zx279133-main.o zx279133-tm.o zx279133-np.o zx279133-idm.o \
	       zx279133-mac.o zx279133-netdev.o zx279133-offload.o \
	       zx279133-ptp.o

obj-$(CONFIG_ZTE_ZX279133_LAN_CONDUIT) += zx279133-lan-conduit.o
obj-$(CONFIG_ZTE_ZX279133_RTL8372N) += zx279133-rtl8372n.o
obj-$(CONFIG_ZTE_ZX279133_SWITCH) += zx279133-switch.o
