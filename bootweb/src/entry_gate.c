/* SPDX-License-Identifier: GPL-2.0-only */
#include "bootweb.h"
int bw_reset_entry(const struct bw_entry_io *p) {
    uint64_t start,now;
    if(!p||!p->gpio_verified||!p->pressed||!p->milliseconds||!p->poll)return -1;
    {int pressed=p->pressed(p->context);if(pressed<0||pressed>1)return -1;if(!pressed)return 0;}
    start=p->milliseconds(p->context);
    for(;;) {
        int pressed;p->poll(p->context);pressed=p->pressed(p->context);
        if(pressed<0||pressed>1)return -1;
        if(!pressed)return 0;
        now=p->milliseconds(p->context);
        if(now<start)return -1;
        if(now-start>=3000)return 1;
    }
}
