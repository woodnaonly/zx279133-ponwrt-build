#!/bin/bash
# Runs in openwrt/ BEFORE ./scripts/feeds update -a.
# Phase 1 keeps the upstream SR1010 tree untouched; edits belong here once we
# start carrying SK-D840N changes that are not plain file overlays.
echo "== diy-part1: no changes (upstream sr1010-zx279133 as-is) =="
