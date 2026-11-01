/* SPDX-License-Identifier: GPL-2.0-only */
#include <errno.h>
#include <linux/genetlink.h>
#include <linux/netlink.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>
#include "zxpon.h"
#include "zxpon-config.h"
#include "zxpon-backend.h"
#include "zxpon-transport.h"

#define STATUS_PATH "/var/run/zxpond-status.json"
static volatile sig_atomic_t stopping;
static void stop_signal(int signal_number) { (void)signal_number; stopping = 1; }

/* This only supplies the existing kernel's SOFTWARE identity model.
 * It neither initializes the optical MAC nor claims an OLT registration. */
static int kernel_identity(const struct zx_config *config)
{
	struct {
		struct nlattr serial;
		uint8_t sn[8];
		struct nlattr registration;
		uint8_t regid[36];
	} request = {0};
	int result;
	request.serial.nla_type = ZXPON_A_SN;
	request.serial.nla_len = NLA_HDRLEN + sizeof(request.sn);
	request.registration.nla_type = ZXPON_A_REGID;
	request.registration.nla_len = NLA_HDRLEN + sizeof(request.regid);
	memcpy(request.sn, config->identity.serial, sizeof(request.sn));
	memcpy(request.regid, config->regid, sizeof(request.regid));
	result = zx_genl_command(ZXPON_CMD_SET_IDENTITY, &request, sizeof(request));
	memset(&request, 0, sizeof(request));
	return result;
}

static int process_start(char output[32])
{
	char buffer[2048], *position, *token, *saved;
	unsigned int field = 3;
	FILE *stream = fopen("/proc/self/stat", "r");
	if (!stream) return -errno;
	position = fgets(buffer, sizeof(buffer), stream) ? strrchr(buffer, ')') : NULL;
	fclose(stream);
	if (!position) return -EINVAL;
	for (token = strtok_r(position + 1, " \n", &saved); token;
	     token = strtok_r(NULL, " \n", &saved), field++)
		if (field == 22) { snprintf(output, 32, "%s", token); return 0; }
	return -EINVAL;
}

static int publish_status(const char *started, int enabled, int valid, int identity, int engine, int kernel_error)
{
	struct timespec now;
	char temporary[256];
	FILE *stream;
	int result = 0;
	clock_gettime(CLOCK_BOOTTIME, &now);
	snprintf(temporary, sizeof(temporary), "%s.%ld", STATUS_PATH, (long)getpid());
	stream = fopen(temporary, "w");
	if (!stream) return -errno;
	if (fprintf(stream, "{\"schema\":1,\"pid\":%ld,\"process_start\":\"%s\",\"updated_s\":%lld,"
		"\"enabled\":%s,\"configuration_valid\":%s,\"identity_configured\":%s,"
		"\"engine_ready\":%s,\"transport_ready\":false,\"kernel_identity_error\":%d,"
		"\"error\":%d}\n", (long)getpid(), started, (long long)now.tv_sec,
		enabled ? "true" : "false", valid ? "true" : "false", identity ? "true" : "false",
		engine ? "true" : "false", kernel_error, !valid ? -EINVAL : -EOPNOTSUPP) < 0) result = -EIO;
	if (fclose(stream) && !result) result = -errno;
	if (!result && rename(temporary, STATUS_PATH)) result = -errno;
	if (result) unlink(temporary);
	return result;
}

int main(int argc, char **argv)
{
	struct zx_config config;
	struct omci_agent *agent = NULL;
	char started[32];
	int result, enabled, valid, identity, kernel_error = -EOPNOTSUPP;
	umask(077);
	if (argc == 2 && !strcmp(argv[1], "--send-omci")) {
		/* Narrow root-only single-frame transport tool. No second listener,
		 * arbitrary command/file arguments, or automatic optical activation.
		 * Input must already include its correct four-byte MIC.
		 */
		uint8_t frame[49];
		size_t length = 0;
		if (geteuid()) return 1;
		while (length < sizeof(frame)) {
			ssize_t bytes = read(STDIN_FILENO, frame + length, sizeof(frame) - length);
			if (bytes < 0 && errno == EINTR) continue;
			if (bytes < 0) return 1;
			if (!bytes) break;
			length += (size_t)bytes;
		}
		result = zx_transport_send(frame, length);
		memset(frame, 0, sizeof(frame));
		if (result) fprintf(stderr, "zxpond: OMCI TX rejected: %d\n", result);
		else puts("IDM completion confirmed; optical transmission/OLT acceptance not verified");
		return result ? 1 : 0;
	}
	if (argc == 2 && !strcmp(argv[1], "--migrate"))
		return zx_migrate("/etc/config/pon", "/etc/pon/identity") ? 1 : 0;
	if (argc == 3 && !strcmp(argv[1], "--check")) {
		result = zx_config_load(&config, argv[2]);
		memset(&config, 0, sizeof(config));
		return result ? 1 : 0;
	}
	if (argc != 1) return 2;
	result = zx_config_load(&config, "/etc/config");
	valid = !result;
	enabled = config.enabled;
	identity = config.has_identity;
	if (valid && enabled && identity) {
		agent = zxpon_omci_new(&config.identity);
		kernel_error = kernel_identity(&config);
	}
	memset(&config, 0, sizeof(config));
	if (process_start(started)) return 1;
	signal(SIGTERM, stop_signal);
	signal(SIGINT, stop_signal);
	/* No packet socket is opened while transport is unimplemented. In particular,
	 * send() succeeding against the existing drop-only omci netdev is NOT proof
	 * that a PDU reached the wire. The common engine/transaction boundary is
	 * exercised by the real C/Rust FFI tests instead. */
	while (!stopping) {
		if (publish_status(started, enabled, valid, identity, agent != NULL, kernel_error))
			fprintf(stderr, "zxpond: status publication failed\n");
		sleep(3);
	}
	if (agent) zxpon_omci_free(agent);
	unlink(STATUS_PATH);
	return 0;
}
