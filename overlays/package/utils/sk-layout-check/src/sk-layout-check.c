/* SPDX-License-Identifier: GPL-2.0-only */
/* Read-only validator. No erase/write API, uploaded policy, or layout override. */
#include <libfdt.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_DTB (1024u * 1024u)
static const char *const labels[] = {"boot", "kernel", "dtb", "parameter tags", "root", "scratch"};
static const uint32_t offsets[] = {0, 0x200000, 0xa00000, 0xb00000, 0xf00000, 0xff00000};
static const uint32_t sizes[] = {0x200000, 0x800000, 0x100000, 0x400000, 0, 0x100000};

static int fail(const char *message)
{
	fprintf(stderr, "sk-layout-check: %s\n", message);
	return 1;
}

static int string_is(const void *p, int len, const char *s)
{
	return p && len == (int)strlen(s) + 1 && !memcmp(p, s, (size_t)len);
}

static int enabled(const void *fdt, int node)
{
	const void *p;
	int len;
	for (; node >= 0; node = fdt_parent_offset(fdt, node)) {
		p = fdt_getprop(fdt, node, "status", &len);
		if (p ? !string_is(p, len, "okay") && !string_is(p, len, "ok") : len != -FDT_ERR_NOTFOUND)
			return 0;
	}
	return 1;
}

static int range_ok(size_t off, size_t size, size_t total)
{
	return off >= 40 && off <= total && size <= total - off;
}

static int overlaps(size_t a, size_t n, size_t b, size_t m)
{
	return a < b + m && b < a + n;
}

static int header_ok(const void *fdt, size_t length)
{
	const unsigned char *data = fdt;
	const unsigned char zero[16] = {0};
	size_t total, st, ns, ss, nt, rv, end;
	if (length < 40 || fdt_magic(fdt) != FDT_MAGIC || fdt_version(fdt) != 17 ||
	    fdt_last_comp_version(fdt) < 16 || fdt_last_comp_version(fdt) > 17) return 0;
	total = fdt_totalsize(fdt); st = fdt_off_dt_struct(fdt); ns = fdt_size_dt_struct(fdt);
	ss = fdt_off_dt_strings(fdt); nt = fdt_size_dt_strings(fdt); rv = fdt_off_mem_rsvmap(fdt);
	if (total < 40 || total > length || (st & 3) || (rv & 7) ||
	    !range_ok(st, ns, total) || !range_ok(ss, nt, total) || overlaps(st, ns, ss, nt)) return 0;
	for (end = rv; range_ok(end, 16, total); end += 16) {
		if (overlaps(end, 16, st, ns) || overlaps(end, 16, ss, nt)) return 0;
		if (!memcmp(data + end, zero, 16)) return 1;
	}
	return 0;
}

static int structure_order(const void *fdt)
{
	int offset = 0, next, depth = -1;
	unsigned child_started[32] = {0};
	for (;;) {
		uint32_t tag = fdt_next_tag(fdt, offset, &next);
		if (next < 0) return 0;
		if (tag == FDT_BEGIN_NODE) {
			const char *name;
			if (depth >= 0) child_started[depth] = 1;
			if (++depth >= 32) return 0;
			child_started[depth] = 0;
			name = fdt_get_name(fdt, offset, NULL);
			if (!name || (depth && !*name)) return 0;
			for (; *name; name++) if (*name < 32 || *name > 126 || *name == '/') return 0;
		} else if (tag == FDT_END_NODE) {
			if (depth-- < 0) return 0;
		} else if (tag == FDT_PROP) {
			if (depth < 0 || child_started[depth]) return 0;
		} else if (tag == FDT_END) {
			const unsigned char *p = (const unsigned char *)fdt + fdt_off_dt_struct(fdt);
			if (depth != -1 || (unsigned)next > fdt_size_dt_struct(fdt)) return 0;
			for (unsigned i = (unsigned)next; i < fdt_size_dt_struct(fdt); i++) if (p[i]) return 0;
			return 1;
		} else if (tag != FDT_NOP) return 0;
		offset = next;
	}
}

/* libfdt checks structure/bounds; also forbid ambiguous sibling/property names. */
static int unique_tree(const void *fdt)
{
	int node = -1, depth = 0, count = 0;
	while ((node = fdt_next_node(fdt, node, &depth)) >= 0) {
		int prop, sibling;
		char path[512];
		if (depth < 0) break;
		if (++count > 2048 || depth > 32 || fdt_get_path(fdt, node, path, sizeof(path))) return 0;
		fdt_for_each_property_offset(prop, fdt, node) {
			const char *name;
			const unsigned char *data;
			int len, other = prop;
			data = fdt_getprop_by_offset(fdt, prop, &name, &len);
			if (!data) return 0;
			for (const char *p = name; *p; p++) if ((unsigned char)*p > 127) return 0;
			if (!strcmp(name, "compatible")) {
				if (len < 1 || data[len - 1]) return 0;
				for (int i = 0; i < len; i++)
					if (data[i] > 127 || (!data[i] && (!i || !data[i - 1]))) return 0;
			}
			while ((other = fdt_next_property_offset(fdt, other)) >= 0) {
				const char *next;
				if (!fdt_getprop_by_offset(fdt, other, &next, &len) || !strcmp(name, next)) return 0;
			}
			if (other != -FDT_ERR_NOTFOUND) return 0;
		}
		if (prop != -FDT_ERR_NOTFOUND) return 0;
		for (sibling = fdt_next_subnode(fdt, node); sibling >= 0; sibling = fdt_next_subnode(fdt, sibling))
			if (!strcmp(fdt_get_name(fdt, node, NULL), fdt_get_name(fdt, sibling, NULL))) return 0;
	}
	return count > 0;
}

static int bootargs_ok(const void *fdt)
{
	int len, roots = 0, types = 0, node = fdt_path_offset(fdt, "/chosen");
	const char *args = fdt_getprop(fdt, node, "bootargs", &len), *p;
	if (!args || len < 1 || len > 4096 || args[len - 1]) return 0;
	for (int i = 0; i < len - 1; i++) if (args[i] < 32 || args[i] > 126) return 0;
	for (p = args; *p;) {
		size_t n;
		if (*p == ' ') { p++; continue; }
		n = strcspn(p, " ");
		if (n >= 5 && !memcmp(p, "root=", 5)) {
			if (++roots != 1 || n != 19 || memcmp(p, "root=/dev/mtdblock5", 19)) return 0;
		} else if (n >= 11 && !memcmp(p, "rootfstype=", 11)) {
			if (++types != 1 || n != 16 || memcmp(p, "rootfstype=jffs2", 16)) return 0;
		} else if ((n >= 9 && !memcmp(p, "mtdparts=", 9)) || (n >= 8 && !memcmp(p, "ubi.mtd=", 8))) return 0;
		p += n;
	}
	return roots == 1 && types == 1;
}

static const char *validate(const void *fdt, size_t length)
{
	int node = -1, depth = 0, table = -1, tables = 0, count = 0, aliases, len, prop;
	uint32_t root_size = 0;
	const char *first;
    if (!header_ok(fdt, length) || fdt_check_full(fdt, length) ||
	    !structure_order(fdt) || !unique_tree(fdt)) return NULL;
	for (size_t i = fdt_totalsize(fdt); i < length; i++)
		if (((const unsigned char *)fdt)[i] != 0 && ((const unsigned char *)fdt)[i] != 255) return NULL;
	first = fdt_getprop(fdt, 0, "compatible", &len);
	if (!first || len < 19 || memcmp(first, "skyworth,sk-d840n", 17) || first[17] ||
	    fdt_stringlist_count(fdt, 0, "compatible") != 2 ||
	    fdt_stringlist_search(fdt, 0, "compatible", "zte,zx279133") != 1 || !bootargs_ok(fdt)) return NULL;
	while ((node = fdt_next_node(fdt, node, &depth)) >= 0 && depth >= 0) {
		if (!fdt_node_check_compatible(fdt, node, "fixed-partitions")) { table = node; tables++; }
	}
	if (tables != 1 || !enabled(fdt, table)) return NULL;
	for (int i = 0; i < 2; i++) {
		const fdt32_t *p = fdt_getprop(fdt, table, i ? "#size-cells" : "#address-cells", &len);
		if (!p || len != 4 || fdt32_to_cpu(*p) != 1) return NULL;
	}
	aliases = fdt_path_offset(fdt, "/aliases");
	if (aliases < 0) return NULL;
	fdt_for_each_subnode(node, fdt, table) {
		const fdt32_t *reg;
		const void *p;
		char alias[8], path[512];
		uint32_t size;
		int index = count++;
		if (index >= 6 || !enabled(fdt, node)) return NULL;
		p = fdt_getprop(fdt, node, "label", &len);
		if (!string_is(p, len, labels[index])) return NULL;
		reg = fdt_getprop(fdt, node, "reg", &len);
		if (!reg || len != 8 || fdt32_to_cpu(reg[0]) != offsets[index]) return NULL;
		size = fdt32_to_cpu(reg[1]);
		if (index == 4) { root_size = size; if (size != 0xf000000 && size != 0xf100000) return NULL; }
		else if (size != sizes[index]) return NULL;
		p = fdt_getprop(fdt, node, "read-only", &len);
		if (index == 0 || index == 3) { if (!p || len) return NULL; }
		else if (p || len != -FDT_ERR_NOTFOUND) return NULL;
		if (fdt_get_path(fdt, node, path, sizeof(path))) return NULL;
		snprintf(alias, sizeof(alias), "mtd%d", index + 1);
		p = fdt_getprop(fdt, aliases, alias, &len);
		if (!string_is(p, len, path)) return NULL;
	}
	if (node != -FDT_ERR_NOTFOUND || count != (root_size == 0xf100000 ? 5 : 6)) return NULL;
	fdt_for_each_property_offset(prop, fdt, aliases) {
		const char *name;
		if (!fdt_getprop_by_offset(fdt, prop, &name, &len)) return NULL;
		if (!strncmp(name, "mtd", 3) && (strlen(name) != 4 || name[3] < '1' || name[3] > '0' + count)) return NULL;
	}
	return prop == -FDT_ERR_NOTFOUND ? (count == 5 ? "native241" : "current240") : NULL;
}

int main(int argc, char **argv)
{
	FILE *file;
	unsigned char *data;
	size_t length;
	const char *layout;
	if (argc != 2) return fail("usage: sk-layout-check DTB");
	file = fopen(argv[1], "rb");
	if (!file) return fail("cannot open DTB");
	data = malloc(MAX_DTB + 1);
	if (!data) { fclose(file); return fail("out of memory"); }
	length = fread(data, 1, MAX_DTB + 1, file);
	if (ferror(file) || length > MAX_DTB) { fclose(file); free(data); return fail("unreadable/oversized DTB"); }
	fclose(file);
	layout = length >= 40 ? validate(data, length) : NULL;
	free(data);
	if (!layout) return fail("invalid DTB or unsupported SK-D840N layout");
	puts(layout);
	return 0;
}
