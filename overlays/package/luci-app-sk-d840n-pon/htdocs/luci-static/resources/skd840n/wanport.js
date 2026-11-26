'use strict';
'require baseclass';
'require rpc';
'require uci';

// The privileged half of the WAN port page.
//
// The port of the second WAN cannot be changed through LuCI's uci channel:
// the bridge's port list is a LIST option, and the uci channel has no
// add_list, so a list written through it is one string and the bridge the
// kernel builds then holds nothing the user asked for.  The change is
// therefore made by a program on the board, and this module is the way
// the page talks to it.
//
// The command is fixed here.  A page that could name its own program
// would be a page that could run anything as root, and the access this
// package asks for is exactly one program.
//
// The export below is a class and not a plain object because that is what
// LuCI's loader needs: it evaluates the factory and then does `new` on
// what came back, and it refuses a factory that did not hand it a
// constructor - the whole page then fails with "factory yields invalid
// constructor" before it shows anything.  Every other module in this
// package exports `baseclass.extend(...)` for the same reason.

var exec = rpc.declare({
	object: 'file',
	method: 'exec',
	params: ['command', 'params'],
	expect: { '': {} },
	reject: true
});

// The address of the machine that is talking to the board.  The router
// answers this from the connection it is serving, and it is what says
// which jack the management client is in.
var peerHints = rpc.declare({
	object: 'luci-rpc',
	method: 'getHostHints',
	expect: { '': {} },
	reject: true
});

var SWITCH = '/usr/libexec/sk-wan-switch';

function run(args) {
	return exec(SWITCH, args).then(function(res) {
		var out = (res && typeof res.stdout === 'string') ? res.stdout : '';
		var err = (res && typeof res.stderr === 'string') ? res.stderr : '';
		var code = (res && typeof res.code === 'number') ? res.code : 1;
		var parsed = null;
		try { parsed = JSON.parse(out); } catch (e) { parsed = null; }
		if (code !== 0 || !parsed || parsed.ok !== true) {
			var why = (parsed && parsed.error) ? parsed.error : (err || out || ('exit ' + code));
			return Promise.reject(new Error(String(why).trim() || 'the command failed'));
		}
		return parsed;
	});
}

function status(client) {
	var args = ['status'];
	if (client) args.push(client);
	return run(args);
}

function applyWan2(opts) {
	var args = ['apply-wan2', '--device', opts.device || 'none'];
	if (opts.device && opts.device !== 'none') {
		if (opts.proto) args.push('--proto', opts.proto);
		if (opts.proto === 'pppoe') {
			args.push('--username', opts.username || '');
			args.push('--password', opts.password || '');
		}
		if (opts.proto === 'static') {
			args.push('--ipaddr', opts.ipaddr || '');
			args.push('--netmask', opts.netmask || '');
			args.push('--gateway', opts.gateway || '');
		}
	}
	if (opts.primary) args.push('--primary', opts.primary);
	return run(args);
}

function resetWan() { return run(['reset']); }
function confirmChange() { return run(['confirm']); }
function rollbackChange() { return run(['rollback']); }

// The addresses of the machines that are talking to the board.
function clientAddresses() {
	return peerHints().then(function(hints) {
		var addrs = [];
		Object.keys(hints || {}).forEach(function(mac) {
			var entry = hints[mac] || {};
			(entry.ipaddrs || []).forEach(function(a) {
				if (addrs.indexOf(a) < 0) addrs.push(a);
			});
		});
		return addrs;
	}).catch(function() { return []; });
}

// The policy file: the adaptive mode and the jacks that must never be
// taken.  It is plain UCI, so it goes through the uci channel.
function savePolicy(auto, exclude) {
	uci.set('sk_wan', 'wan', 'auto', auto ? '1' : '0');
	if (exclude) uci.set('sk_wan', 'wan', 'exclude', exclude);
	else uci.unset('sk_wan', 'wan', 'exclude');
	return uci.apply();
}

return baseclass.extend({
	status: status,
	applyWan2: applyWan2,
	resetWan: resetWan,
	confirmChange: confirmChange,
	rollbackChange: rollbackChange,
	clientAddresses: clientAddresses,
	savePolicy: savePolicy
});
