// SPDX-License-Identifier: GPL-2.0
/*
 * zrec - browser upload and flash for the SK-D840N recovery image.
 *
 * Routes, JSON field names and phase strings are the ones uboot-an758x's lwIP
 * httpd serves (net/lwip/httpd.c), so the page is a port rather than a rewrite.
 * The storage model is what differs: this board is fixed-partition MTD with no
 * UBI and no FIP, so every target is kind "mtd", addressed by its /proc/mtd
 * label, and the verbs are the ones /usr/libexec/zrec-ops implements.
 *
 * All state lives in one-file-per-key under /tmp/zrec. The server forks per
 * connection and the page polls /api/task while an upload or a job is still
 * running on another connection, so nothing may live in process memory.
 */
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include <unistd.h>
#include <netinet/in.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>

#define ZROOT		"/tmp/zrec"
#define PAGE		"/www/zrec/index.html"
#define OPS		"/usr/libexec/zrec-ops"
#define RUNNER		"/usr/sbin/zrec-run"
#define UPDIR		ZROOT "/up"
#define BKDIR		ZROOT "/bk"
#define MAX_HEAD	8192
#define MAX_TASK_BODY	2048
#define MAX_OP		32
#define MAX_NAME	128
#define MAX_CMD		512
#define MAX_VAL		1024
#define OUT_CAP		(64 * 1024)
#define JSON_CAP	(OUT_CAP + 4096)
#define DL_SEGMENT	(4 * 1024 * 1024)
#define RECV_STALE	120

static char json_buf[JSON_CAP];

/* ---------- one file per key, published with tmp+rename ---------- */

static void kv_set(const char *key, const char *fmt, ...)
{
	char path[256], tmp[256], text[512];
	va_list ap;
	int fd;

	va_start(ap, fmt);
	vsnprintf(text, sizeof(text), fmt, ap);
	va_end(ap);
	snprintf(path, sizeof(path), "%s/%s", ZROOT, key);
	snprintf(tmp, sizeof(tmp), "%s/.%s.%d", ZROOT, key, (int)getpid());
	fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0600);
	if (fd < 0)
		return;
	if (write(fd, text, strlen(text)) >= 0) {
		close(fd);
		rename(tmp, path);
		return;
	}
	close(fd);
	unlink(tmp);
}

static size_t kv_get(const char *key, char *out, size_t n)
{
	char path[256];
	ssize_t len;
	int fd;

	out[0] = '\0';
	snprintf(path, sizeof(path), "%s/%s", ZROOT, key);
	fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return 0;
	len = read(fd, out, n - 1);
	close(fd);
	if (len <= 0)
		return 0;
	out[len] = '\0';
	while (len && (out[len - 1] == '\n' || out[len - 1] == '\r'))
		out[--len] = '\0';
	return (size_t)len;
}

static long long kv_num(const char *key, long long dflt)
{
	char text[64];

	if (!kv_get(key, text, sizeof(text)))
		return dflt;
	return strtoll(text, NULL, 0);
}

static void kv_seed(const char *key, const char *value)
{
	char path[256];

	snprintf(path, sizeof(path), "%s/%s", ZROOT, key);
	if (access(path, F_OK))
		kv_set(key, "%s", value);
}

static off_t file_size(const char *path)
{
	struct stat st;

	if (stat(path, &st))
		return -1;
	return st.st_size;
}

static int read_small(const char *path, char *out, size_t n)
{
	int fd, len;

	fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return 0;
	len = read(fd, out, n - 1);
	close(fd);
	if (len <= 0)
		return 0;
	out[len] = '\0';
	return 1;
}

/* ---------- small JSON writer ---------- */

struct jb {
	char *p;
	size_t off, cap;
	int ovf;
};

static void jb_reset(struct jb *j, char *buf, size_t cap)
{
	j->p = buf;
	j->cap = cap;
	j->off = 0;
	j->ovf = 0;
	buf[0] = '\0';
}

static void jb_add(struct jb *j, const char *s, size_t len)
{
	if (j->off + len >= j->cap) {
		j->ovf = 1;
		return;
	}
	memcpy(j->p + j->off, s, len);
	j->off += len;
	j->p[j->off] = '\0';
}

static void jb_printf(struct jb *j, const char *fmt, ...)
{
	char text[512];
	va_list ap;
	int n;

	va_start(ap, fmt);
	n = vsnprintf(text, sizeof(text), fmt, ap);
	va_end(ap);
	if (n > 0)
		jb_add(j, text, (size_t)n);
}

/* Escapes only what must be escaped and lets UTF-8 through, so a Chinese log
 * line written by zrec-ops still reads in the page's output pane. */
static void jb_str(struct jb *j, const char *s)
{
	const unsigned char *p;
	char esc[8];
	int n;

	jb_add(j, "\"", 1);
	for (p = (const unsigned char *)(s ? s : ""); *p; p++) {
		if (*p == '"' || *p == '\\') {
			esc[0] = '\\';
			esc[1] = (char)*p;
			n = 2;
		} else if (*p == '\n') {
			esc[0] = '\\'; esc[1] = 'n'; n = 2;
		} else if (*p == '\t') {
			esc[0] = '\\'; esc[1] = 't'; n = 2;
		} else if (*p == '\r') {
			esc[0] = '\\'; esc[1] = 'r'; n = 2;
		} else if (*p < 0x20) {
			n = snprintf(esc, sizeof(esc), "\\u%04x", *p);
		} else {
			jb_add(j, (const char *)p, 1);
			continue;
		}
		if (n > 0)
			jb_add(j, esc, (size_t)n);
	}
	jb_add(j, "\"", 1);
}

/* ---------- MTD catalog ---------- */

struct target {
	char name[MAX_NAME];
	long long size;
	long long erasesize;
	int whole;
	int ro;
	int idx;
};

/* /sys/class/mtd/mtdN/flags is hex; MTD_WRITEABLE is 0x400. Two of this board's
 * partitions are marked read-only in the DTS (boot, "parameter tags"), and a
 * write to one of them fails with -EROFS from inside jffs2 or mtd erase, which
 * this project already mistook for a broken flash stack once. Say which one it
 * is before the erase, not after. */
static int mtd_writeable(int idx)
{
	char path[256], text[64];
	int fd, n;

	snprintf(path, sizeof(path), "/sys/class/mtd/mtd%d/flags", idx);
	fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return 1;
	n = read(fd, text, sizeof(text) - 1);
	close(fd);
	if (n <= 0)
		return 1;
	text[n] = '\0';
	return !!(strtoul(text, NULL, 0) & 0x00000400);
}

/* Index order, because /proc/mtd listing order is not physical order on this
 * device. Nothing downstream keys off the index: a target is always the label. */
static int catalog_load(struct target *t, int max)
{
	int count = 0, idx;

	for (idx = 0; idx < 64 && count < max; idx++) {
		char path[256];
		struct target *e = &t[count];
		FILE *f;

		snprintf(path, sizeof(path), "/sys/class/mtd/mtd%d/name", idx);
		if (!read_small(path, e->name, sizeof(e->name)))
			continue;
		e->name[strcspn(e->name, "\n")] = '\0';
		if (!e->name[0])
			continue;
		e->idx = idx;
		e->size = 0;
		e->erasesize = 0;
		snprintf(path, sizeof(path), "/sys/class/mtd/mtd%d/size", idx);
		f = fopen(path, "re");
		if (f) {
			if (fscanf(f, "%lld", &e->size) != 1)
				e->size = 0;
			fclose(f);
		}
		snprintf(path, sizeof(path), "/sys/class/mtd/mtd%d/erasesize", idx);
		f = fopen(path, "re");
		if (f) {
			if (fscanf(f, "%lld", &e->erasesize) != 1)
				e->erasesize = 0;
			fclose(f);
		}
		snprintf(path, sizeof(path), "/sys/class/mtd/mtd%d/subdevices_count", idx);
		e->whole = access(path, R_OK) == 0;
		e->ro = !mtd_writeable(idx);
		count++;
	}
	return count;
}

static struct target *catalog_find(struct target *t, int n, const char *name)
{
	int i;

	for (i = 0; i < n; i++)
		if (!strcmp(t[i].name, name))
			return &t[i];
	return NULL;
}

/* ---------- endpoint bodies ---------- */

static void build_storage(char *buf, size_t cap)
{
	struct target t[32];
	struct jb j;
	int n, i;

	n = catalog_load(t, 32);
	jb_reset(&j, buf, cap);
	jb_printf(&j, "{\"targets\":[");
	for (i = 0; i < n; i++) {
		if (i)
			jb_printf(&j, ",");
		jb_printf(&j, "{\"kind\":\"mtd\",\"name\":");
		jb_str(&j, t[i].name);
		/* idx is published for the partition table only: U-Boot's bootargs
		 * hard-code root=/dev/mtdblock5, so seeing which index root actually
		 * got is a diagnostic, never an address - every operation here takes
		 * the label. */
		jb_printf(&j, ",\"idx\":%d,\"size\":%lld,\"erasesize\":%lld,"
			  "\"whole\":%s,\"ro\":%s}",
			  t[i].idx, t[i].size, t[i].erasesize,
			  t[i].whole ? "true" : "false", t[i].ro ? "true" : "false");
	}
	jb_printf(&j, "]}");
	if (j.ovf)
		snprintf(buf, cap, "{\"targets\":[],\"error\":\"catalog too large\"}");
}

static void device_version(char *out, size_t n)
{
	char line[256], rel[64] = "";
	FILE *f;

	out[0] = '\0';
	f = fopen("/etc/openwrt_release", "re");
	if (f) {
		while (fgets(line, sizeof(line), f)) {
			char *d = strstr(line, "DISTRIB_DESCRIPTION='");

			if (!d)
				continue;
			d += strlen("DISTRIB_DESCRIPTION='");
			d[strcspn(d, "'\n")] = '\0';
			snprintf(out, n, "%s", d);
			break;
		}
		fclose(f);
	}
	if (read_small("/proc/sys/kernel/osrelease", rel, sizeof(rel))) {
		rel[strcspn(rel, "\n")] = '\0';
		if (out[0])
			strncat(out, " / Linux ", n - strlen(out) - 1);
		else
			snprintf(out, n, "OpenWrt");
		strncat(out, rel, n - strlen(out) - 1);
	}
	if (!out[0])
		snprintf(out, n, "zrec");
}

static void build_device(char *buf, size_t cap)
{
	struct target t[32];
	struct jb j;
	char model[256], version[160];
	long long mem = 0, flash = 0, parts = 0;
	int n, i, have_parts = 0;
	FILE *f;

	n = catalog_load(t, 32);
	for (i = 0; i < n; i++) {
		if (t[i].whole) {
			if (t[i].size > flash)
				flash = t[i].size;
		} else {
			parts += t[i].size;
			have_parts = 1;
		}
	}
	/* This DTS registers partitions-only, so there is no chip-wide master to
	 * read the size off; the sum of the partitions is the honest figure. */
	if (have_parts)
		flash = parts;
	if (!read_small("/proc/device-tree/model", model, sizeof(model)))
		snprintf(model, sizeof(model), "unknown");
	model[strcspn(model, "\0\n")] = '\0';
	f = fopen("/proc/meminfo", "re");
	if (f) {
		char line[128];
		long long kb;

		while (fgets(line, sizeof(line), f))
			if (sscanf(line, "MemTotal: %lld kB", &kb) == 1) {
				mem = kb * 1024;
				break;
			}
		fclose(f);
	}
	device_version(version, sizeof(version));

	jb_reset(&j, buf, cap);
	jb_printf(&j, "{\"model\":");
	jb_str(&j, model);
	jb_printf(&j, ",\"ram_bytes\":%lld,\"flash_bytes\":%lld,\"version\":", mem, flash);
	jb_str(&j, version);
	jb_printf(&j, ",\"ubi\":false,\"rebuild\":false,\"board_data\":[]}");
	if (j.ovf)
		snprintf(buf, cap, "{\"error\":\"device too large\"}");
}

static int download_active(void)
{
	char path[256];
	long long id = kv_num("dlid", 0);

	if (id <= 0 || kv_num("id", 0) != id)
		return 0;
	if (!kv_get("dlpath", path, sizeof(path)))
		return 0;
	return file_size(path) >= 0;
}

static int receiving(void)
{
	char text[64];
	struct stat st;

	if (stat(ZROOT "/recv", &st))
		return 0;
	if (time(NULL) - st.st_mtime > RECV_STALE)
		return 0;
	kv_get("recv", text, sizeof(text));
	return atoi(text) ? 1 : 0;
}

static int job_running(void)
{
	char state[32];
	long long pid;

	if (!kv_get("state", state, sizeof(state)))
		return 0;
	if (strcmp(state, "queued") && strcmp(state, "running"))
		return 0;
	pid = kv_num("jobpid", 0);
	if (pid > 0 && kill((pid_t)pid, 0) == 0)
		return 1;
	/* Dead without reporting: a killed or OOM'd worker would otherwise leave
	 * every button on the page greyed out for the rest of the session. */
	kv_set("state", "done");
	kv_set("code", "%d", 137);
	kv_set("phase", "error");
	return 0;
}

static void build_task(char *buf, size_t cap)
{
	struct jb j;
	char state[32] = "idle", phase[32] = "idle", output[OUT_CAP + 1];
	size_t len;

	len = kv_get("output", output, sizeof(output));
	output[len] = '\0';
	if (receiving()) {
		strcpy(state, "receiving");
		strcpy(phase, "upload");
	} else {
		if (!kv_get("state", state, sizeof(state)))
			strcpy(state, "idle");
		if (!kv_get("phase", phase, sizeof(phase)))
			strcpy(phase, "idle");
		if (!strcmp(state, "done") && download_active())
			strcpy(phase, "backup-ready");
	}

	jb_reset(&j, buf, cap);
	jb_printf(&j, "{\"id\":%lld,\"state\":", kv_num("id", 0));
	jb_str(&j, state);
	jb_printf(&j, ",\"busy\":%s,\"phase\":",
		  job_running() || receiving() ? "true" : "false");
	jb_str(&j, phase);
	jb_printf(&j, ",\"code\":%lld,\"upload_id\":%lld,\"download_id\":%lld,"
		  "\"download_size\":%lld,\"bytes\":%lld,\"total\":%lld,\"output\":",
		  kv_num("code", 0), kv_num("upid", 0),
		  download_active() ? kv_num("dlid", 0) : 0,
		  kv_num("dlsize", 0), kv_num("bytes", 0), kv_num("total", 0));
	jb_str(&j, output);
	jb_printf(&j, "}");
	if (j.ovf)
		snprintf(buf, cap, "{\"code\":-75,\"error\":\"result too large\"}");
}

/* The page does not edit a bootloader environment (this build has no
 * fw_setenv and the env block's real size is still unknown), so these are the
 * keys that answer the question /api/env answered in the original: how is this
 * thing reachable, and what did it boot with. */
static void build_env(char *buf, size_t cap)
{
	struct jb j;
	char text[512];

	jb_reset(&j, buf, cap);
	kv_get("addr", text, sizeof(text));
	jb_printf(&j, "{\"addr\":");
	jb_str(&j, text[0] ? text : "192.168.9.1");
	kv_get("netif", text, sizeof(text));
	jb_printf(&j, ",\"netif\":");
	jb_str(&j, text);
	if (read_small("/proc/cmdline", text, sizeof(text)))
		text[strcspn(text, "\n")] = '\0';
	else
		text[0] = '\0';
	jb_printf(&j, ",\"cmdline\":");
	jb_str(&j, text);
	jb_printf(&j, "}");
	if (j.ovf)
		snprintf(buf, cap, "{}");
}

/* ---------- HTTP plumbing ---------- */

static void send_all(int fd, const char *buf, size_t len)
{
	size_t off = 0;

	while (off < len) {
		ssize_t n = write(fd, buf + off, len - off);

		if (n <= 0)
			return;
		off += (size_t)n;
	}
}

static void http_head(int fd, int code, const char *ctype, size_t len, const char *disp)
{
	char head[512];
	int n;

	n = snprintf(head, sizeof(head),
		     "HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %zu\r\n"
		     "Cache-Control: no-store\r\nConnection: close\r\n",
		     code, code == 200 ? "OK" : code == 409 ? "Conflict" : "Error",
		     ctype, len);
	if (disp && *disp && n > 0)
		n += snprintf(head + n, sizeof(head) - (size_t)n,
			      "Content-Disposition: attachment; filename=\"%s\"\r\n", disp);
	if (n > 0)
		n += snprintf(head + n, sizeof(head) - (size_t)n, "\r\n");
	if (n > 0)
		send_all(fd, head, (size_t)n);
}

static void send_json(int fd, int code, const char *body)
{
	http_head(fd, code, "application/json", strlen(body), NULL);
	send_all(fd, body, strlen(body));
}

static void send_file(int fd, const char *path, const char *ctype)
{
	char buf[8192];
	off_t size = file_size(path);
	int in;

	if (size < 0 || (in = open(path, O_RDONLY | O_CLOEXEC)) < 0) {
		send_json(fd, 404, "{\"error\":\"page missing\"}");
		return;
	}
	http_head(fd, 200, ctype, (size_t)size, NULL);
	for (;;) {
		ssize_t n = read(in, buf, sizeof(buf));

		if (n <= 0)
			break;
		send_all(fd, buf, (size_t)n);
	}
	close(in);
}

static void send_segment(int fd, const char *path, long long offset, long long length,
			 const char *name)
{
	char buf[8192], disp[MAX_NAME + 16];
	int in;

	if ((in = open(path, O_RDONLY | O_CLOEXEC)) < 0) {
		send_json(fd, 409, "{\"error\":\"backup expired\"}");
		return;
	}
	if (lseek(in, (off_t)offset, SEEK_SET) == (off_t)-1) {
		close(in);
		send_json(fd, 400, "{\"error\":\"invalid range\"}");
		return;
	}
	snprintf(disp, sizeof(disp), "%s-main.bin", name);
	http_head(fd, 200, "application/octet-stream", (size_t)length, disp);
	while (length > 0) {
		size_t want = length > (long long)sizeof(buf) ? sizeof(buf) : (size_t)length;
		ssize_t n = read(in, buf, want);

		if (n <= 0)
			break;
		send_all(fd, buf, (size_t)n);
		length -= n;
	}
	close(in);
}

/* ---------- request handling ---------- */

static int name_ok(const char *s)
{
	size_t len = 0;

	for (; s[len]; len++) {
		if (len >= MAX_NAME - 1)
			return 0;
		if (!isalnum((unsigned char)s[len]) && !strchr("_-.: @,+", s[len]))
			return 0;
	}
	return len > 0;
}

/* POST body is the file, raw: no multipart, Content-Length mandatory, one
 * upload in flight - same as the U-Boot original, so the page's XHR needs no
 * change. The id handed back in the JSON is what the consuming task names. */
static void handle_upload(int fd, long long content_len, const char *pre, size_t pre_len)
{
	char path[256];
	long long id = kv_num("upid", 0) + 1, received = 0;
	time_t marked = 0;
	int out;

	snprintf(path, sizeof(path), UPDIR "/%lld.bin", id);
	out = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
	if (out < 0) {
		send_json(fd, 400, "{\"error\":\"cannot store upload\"}");
		return;
	}
	kv_set("recv", "%lld", content_len);
	kv_set("phase", "upload");
	/* A browser often sends the first kilobytes of the file in the same
	 * segment as the headers, and those bytes are already consumed out of the
	 * socket - dropping them here is how a 250 KiB image lands 250 KiB short. */
	if (pre_len > (size_t)content_len)
		pre_len = (size_t)content_len;
	if (pre_len && write(out, pre, pre_len) != (ssize_t)pre_len)
		pre_len = 0;
	received = (long long)pre_len;
	while (received < content_len) {
		char buf[8192];
		ssize_t n = recv(fd, buf, sizeof(buf), 0);

		if (n <= 0)
			break;
		if (received + n > content_len)
			n = (ssize_t)(content_len - received);
		if (n <= 0)
			break;
		if (write(out, buf, (size_t)n) != n)
			break;
		received += n;
		if (time(NULL) != marked) {
			marked = time(NULL);
			kv_set("recv", "%lld", received);
		}
		if (received >= content_len)
			break;
	}
	close(out);
	unlink(ZROOT "/recv");
	if (received != content_len) {
		unlink(path);
		kv_set("phase", "error");
		send_json(fd, 400, "{\"error\":\"transfer incomplete\"}");
		return;
	}
	kv_set("upid", "%lld", id);
	kv_set("bytes", "%lld", 0);
	kv_set("total", "%lld", 0);
	kv_set("phase", "ready");
	build_task(json_buf, sizeof(json_buf));
	send_json(fd, 200, json_buf);
}

static int jvalue(const char *body, const char *key, char *out, size_t n)
{
	char pat[64];
	const char *p;
	size_t i = 0;

	snprintf(pat, sizeof(pat), "\"%s\"", key);
	p = strstr(body, pat);
	if (!p)
		return 0;
	p += strlen(pat);
	while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')
		p++;
	if (*p != ':')
		return 0;
	p++;
	while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')
		p++;
	if (*p != '"') {
		const char *e = p;

		while (*e && *e != ',' && *e != '}' && *e != ' ' && *e != '\n' && *e != '\r')
			e++;
		if ((size_t)(e - p) >= n)
			return 0;
		memcpy(out, p, (size_t)(e - p));
		out[e - p] = '\0';
		return 1;
	}
	p++;
	for (; *p && *p != '"' && i + 1 < n; p++) {
		if (*p == '\\' && p[1]) {
			p++;
			switch (*p) {
			case 'n': out[i++] = '\n'; continue;
			case 't': out[i++] = '\t'; continue;
			case 'r': out[i++] = '\r'; continue;
			case '"': out[i++] = '"'; continue;
			case '\\': out[i++] = '\\'; continue;
			default: break;
			}
		}
		out[i++] = *p;
	}
	out[i] = '\0';
	return 1;
}

static int verb_known(const char *op)
{
	static const char *const v[] = { "flash", "write", "install", "backup",
					 "command", "reset", "boot-production", "cancel" };
	size_t i;

	for (i = 0; i < sizeof(v) / sizeof(v[0]); i++)
		if (!strcmp(op, v[i]))
			return 1;
	return 0;
}

static int verb_needs_upload(const char *op)
{
	return !strcmp(op, "flash") || !strcmp(op, "write") || !strcmp(op, "install");
}

/* A job is /bin/sh RUNNER <operation> [flags]: the runner owns the job lock and
 * publishes state/code, zrec-ops owns the work and the log. The argument vector
 * is built here so no client string is ever assembled into a shell command line
 * by the server - only zrec-ops' own "command" verb runs a shell, on purpose. */
static void handle_task(int fd, long long content_len, const char *pre, size_t pre_len)
{
	static char body[MAX_TASK_BODY + 1];
	char op[MAX_OP] = "", target[MAX_NAME] = "", command[MAX_CMD] = "";
	char value[MAX_VAL] = "", offset[64] = "", size[64] = "", upath[256] = "";
	char arg1[MAX_NAME + 32], arg2[sizeof(upath) + 16], arg3[MAX_CMD + 16];
	char arg4[MAX_VAL + 16], arg5[64 + 16], arg6[64 + 16];
	char *argv[10];
	int argc = 0, out, lock;
	long long upload_id;
	pid_t pid;

	if (content_len <= 0 || content_len > MAX_TASK_BODY) {
		send_json(fd, 400, "{\"error\":\"invalid request\"}");
		return;
	}
	{
		size_t got = 0;

		if (pre_len > (size_t)content_len)
			pre_len = (size_t)content_len;
		if (pre_len) {
			memcpy(body, pre, pre_len);
			got = pre_len;
		}
		while (got < (size_t)content_len) {
			ssize_t n = recv(fd, body + got, sizeof(body) - 1 - got, 0);

			if (n <= 0) {
				send_json(fd, 400, "{\"error\":\"body truncated\"}");
				return;
			}
			got += (size_t)n;
		}
		body[got] = '\0';
	}
	if (!jvalue(body, "operation", op, sizeof(op)) || !name_ok(op) ||
	    !verb_known(op)) {
		send_json(fd, 400, "{\"error\":\"invalid request\"}");
		return;
	}
	if (job_running() && strcmp(op, "cancel")) {
		/* cancel is exempt: it is the only thing that can be issued while a job
		 * is running, and a busy check in front of it makes it unreachable. */
		send_json(fd, 409, "{\"error\":\"busy\"}");
		return;
	}
	jvalue(body, "target", target, sizeof(target));
	jvalue(body, "command", command, sizeof(command));
	jvalue(body, "value", value, sizeof(value));
	jvalue(body, "offset", offset, sizeof(offset));
	jvalue(body, "size", size, sizeof(size));
	upload_id = kv_num("upid", 0);
	{
		char text[64];

		if (jvalue(body, "upload_id", text, sizeof(text)))
			upload_id = strtoll(text, NULL, 0);
	}

	if (!strcmp(op, "cancel")) {
		long long victim = kv_num("jobpid", 0);

		/* Negative pid: the worker runs in its own process group (setpgid below),
		 * so the erase/write child of zrec-ops dies with it. Killing only the
		 * shell would leave an mtd write running against the page's "cancelled". */
		if (victim > 0)
			kill((pid_t)-victim, SIGKILL);
		kv_set("state", "done");
		kv_set("phase", "error");
		kv_set("code", "%d", 130);
		build_task(json_buf, sizeof(json_buf));
		send_json(fd, 200, json_buf);
		return;
	}
	if (verb_needs_upload(op)) {
		struct target t[32];

		snprintf(upath, sizeof(upath), UPDIR "/%lld.bin", upload_id);
		if (file_size(upath) < 0) {
			send_json(fd, 400, "{\"error\":\"no such upload\"}");
			return;
		}
		if (strcmp(op, "install")) {
			int n = catalog_load(t, 32);
			struct target *e = catalog_find(t, n, target);

			if (!e) {
				send_json(fd, 400, "{\"error\":\"unknown target\"}");
				return;
			}
			if (!strcmp(op, "write") && e->whole) {
				send_json(fd, 400, "{\"error\":\"refusing whole-chip write\"}");
				return;
			}
			/* boot and "parameter tags" carry read-only in the DTS, and an
			 * erase there returns -EROFS from the core, which this project
			 * once spent a day reading as "mainline cannot program this
			 * chip". Refuse it by name, before any block is touched. */
			if (e->ro) {
				send_json(fd, 400, "{\"error\":\"target is read-only\"}");
				return;
			}
		}
	}
	if ((!strcmp(op, "flash") || !strcmp(op, "write") || !strcmp(op, "backup")) &&
	    !name_ok(target)) {
		send_json(fd, 400, "{\"error\":\"invalid target\"}");
		return;
	}

	argv[argc++] = op;
	if (target[0])
		snprintf(arg1, sizeof(arg1), "--target=%s", target), argv[argc++] = arg1;
	if (upath[0])
		snprintf(arg2, sizeof(arg2), "--upload=%s", upath), argv[argc++] = arg2;
	if (command[0])
		snprintf(arg3, sizeof(arg3), "--command=%s", command), argv[argc++] = arg3;
	if (value[0])
		snprintf(arg4, sizeof(arg4), "--value=%s", value), argv[argc++] = arg4;
	if (offset[0])
		snprintf(arg5, sizeof(arg5), "--offset=%s", offset), argv[argc++] = arg5;
	if (size[0])
		snprintf(arg6, sizeof(arg6), "--size=%s", size), argv[argc++] = arg6;
	argv[argc] = NULL;

	kv_set("id", "%lld", kv_num("id", 0) + 1);
	kv_set("state", "queued");
	kv_set("phase", "queued");
	kv_set("code", "%d", 0);
	kv_set("bytes", "%lld", 0);
	kv_set("total", "%lld", 0);
	/* Cut the log before the worker can append to it, so the pane always
	 * shows one job rather than the last four. */
	{
		int trunc_fd = open(ZROOT "/output", O_WRONLY | O_CREAT | O_TRUNC, 0600);

		if (trunc_fd >= 0)
			close(trunc_fd);
	}
	build_task(json_buf, sizeof(json_buf));

	pid = fork();
	if (pid < 0) {
		kv_set("state", "done");
		kv_set("phase", "error");
		kv_set("code", "%d", 1);
		send_json(fd, 400, "{\"error\":\"fork failed\"}");
		return;
	}
	if (pid == 0) {
		char *sh_argv[12];
		int i;

		close(fd);
		/* Own process group, so cancel can kill the whole job tree rather than
		 * just the shell that is holding the stdout redirect. */
		setpgid(0, 0);
		sh_argv[0] = "sh";
		sh_argv[1] = RUNNER;
		for (i = 0; i < argc; i++)
			sh_argv[2 + i] = argv[i];
		sh_argv[2 + argc] = NULL;
		out = open(ZROOT "/output", O_WRONLY | O_CREAT | O_APPEND, 0600);
		if (out >= 0) {
			dup2(out, STDOUT_FILENO);
			dup2(out, STDERR_FILENO);
			if (out > STDERR_FILENO)
				close(out);
		}
		lock = open(ZROOT "/job.lock", O_RDWR | O_CREAT, 0600);
		if (lock >= 0)
			flock(lock, LOCK_EX);
		/* Re-announce after taking the lock: two queued tasks must not both
		 * report themselves as the running one. */
		kv_set("state", "running");
		execv("/bin/sh", sh_argv);
		kv_set("state", "done");
		kv_set("phase", "error");
		kv_set("code", "%d", 127);
		_exit(127);
	}
	kv_set("jobpid", "%d", (int)pid);
	send_json(fd, 200, json_buf);
}

static void handle_download(int fd, const char *arg)
{
	char path[256], name[MAX_NAME] = "";
	long long id, offset, length;
	char *end;
	off_t size;

	id = strtoll(arg, &end, 10);
	if (*end != '/') {
		send_json(fd, 400, "{\"error\":\"invalid range\"}");
		return;
	}
	offset = strtoll(end + 1, &end, 10);
	if (*end != '/') {
		send_json(fd, 400, "{\"error\":\"invalid range\"}");
		return;
	}
	length = strtoll(end + 1, &end, 10);
	if (*end || id != kv_num("dlid", 0) || id != kv_num("id", 0)) {
		send_json(fd, 409, "{\"error\":\"backup expired\"}");
		return;
	}
	if (!download_active()) {
		send_json(fd, 409, "{\"error\":\"backup expired\"}");
		return;
	}
	kv_get("dlpath", path, sizeof(path));
	kv_get("dlname", name, sizeof(name));
	size = file_size(path);
	if (!length || length > DL_SEGMENT || offset < 0 || size < 0 ||
	    offset > size || length > size - offset) {
		send_json(fd, 400, "{\"error\":\"invalid range\"}");
		return;
	}
	send_segment(fd, path, offset, length, name[0] ? name : "backup");
}

static void handle(int fd)
{
	static char head[MAX_HEAD];
	char *tok, *save, *eoh, *body;
	const char *pre;
	char method[8] = "", path[256] = "";
	size_t got = 0, pre_len = 0;
	long long content_len = 0;

	for (;;) {
		ssize_t n;

		n = recv(fd, head + got, sizeof(head) - 1 - got, 0);
		if (n <= 0)
			return;
		got += (size_t)n;
		head[got] = '\0';
		eoh = strstr(head, "\r\n\r\n");
		if (eoh)
			break;
		if (got + 1 >= sizeof(head))
			return;
	}
	body = eoh + 4;
	pre = body;
	pre_len = got - (size_t)(body - head);
	*eoh = '\0';	/* the strtok pass below must not walk into the POST body,
			 * which is binary for an upload */
	tok = strtok_r(head, "\r\n", &save);
	if (!tok || sscanf(tok, "%7s %255s", method, path) != 2)
		return;
	for (tok = strtok_r(NULL, "\r\n", &save); tok; tok = strtok_r(NULL, "\r\n", &save))
		if (!strncasecmp(tok, "Content-Length:", 15))
			content_len = strtoll(tok + 15, NULL, 10);

	if (!strcmp(method, "GET")) {
		if (!strcmp(path, "/") || !strcmp(path, "/index.html")) {
			send_file(fd, PAGE, "text/html; charset=utf-8");
			return;
		}
		if (!strcmp(path, "/api/device")) {
			build_device(json_buf, sizeof(json_buf));
			send_json(fd, 200, json_buf);
			return;
		}
		if (!strcmp(path, "/api/storage")) {
			build_storage(json_buf, sizeof(json_buf));
			send_json(fd, 200, json_buf);
			return;
		}
		if (!strcmp(path, "/api/task") || !strcmp(path, "/api/reply")) {
			build_task(json_buf, sizeof(json_buf));
			send_json(fd, 200, json_buf);
			return;
		}
		if (!strcmp(path, "/api/env")) {
			build_env(json_buf, sizeof(json_buf));
			send_json(fd, 200, json_buf);
			return;
		}
		if (!strncmp(path, "/api/download/", 14)) {
			handle_download(fd, path + 14);
			return;
		}
		send_json(fd, 404,
			  strncmp(path, "/api/", 5) ? "{\"error\":\"not found\"}"
					   : "{\"error\":\"unknown API\"}");
		return;
	}
	if (!strcmp(method, "POST")) {
		if (!strcmp(path, "/api/upload") || !strcmp(path, "/api/upload-all-flash")) {
			if (content_len <= 0) {
				send_json(fd, 400, "{\"error\":\"invalid request\"}");
				return;
			}
			handle_upload(fd, content_len, pre, pre_len);
			return;
		}
		if (!strcmp(path, "/api/task")) {
			handle_task(fd, content_len, pre, pre_len);
			return;
		}
		send_json(fd, 400, "{\"error\":\"invalid request\"}");
		return;
	}
	send_json(fd, 400, "{\"error\":\"invalid request\"}");
}

int main(int argc, char **argv)
{
	struct sockaddr_in sin;
	int fd, port = argc > 1 ? atoi(argv[1]) : 80, one = 1;

	signal(SIGCHLD, SIG_IGN);
	signal(SIGPIPE, SIG_IGN);
	mkdir(ZROOT, 0700);
	mkdir(UPDIR, 0700);
	mkdir(BKDIR, 0700);
	kv_seed("state", "idle");
	kv_seed("phase", "idle");
	kv_seed("id", "1");
	kv_seed("upid", "0");

	fd = socket(AF_INET, SOCK_STREAM, 0);
	if (fd < 0) {
		perror("socket");
		return 1;
	}
	setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
	memset(&sin, 0, sizeof(sin));
	sin.sin_family = AF_INET;
	sin.sin_addr.s_addr = htonl(INADDR_ANY);
	sin.sin_port = htons((uint16_t)port);
	if (bind(fd, (struct sockaddr *)&sin, sizeof(sin))) {
		perror("bind");
		return 1;
	}
	if (listen(fd, 16)) {
		perror("listen");
		return 1;
	}
	for (;;) {
		struct timeval tv = { .tv_sec = 120, .tv_usec = 0 };
		pid_t pid;
		int c;

		c = accept(fd, NULL, NULL);
		if (c < 0) {
			if (errno == EINTR)
				continue;
			break;
		}
		setsockopt(c, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
		setsockopt(c, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
		pid = fork();
		if (pid == 0) {
			close(fd);
			handle(c);
			shutdown(c, SHUT_WR);
			close(c);
			_exit(0);
		}
		close(c);
	}
	return 0;
}
