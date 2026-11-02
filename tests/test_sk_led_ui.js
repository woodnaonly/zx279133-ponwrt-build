// SPDX-License-Identifier: GPL-2.0-only
// Load the actual LuCI LED trigger plugins without a browser or RPC daemon.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const directory = path.join(__dirname, '../overlays/package/luci-app-sk-d840n-pon/htdocs/luci-static/resources/view/system/led-trigger');

function fixture(name, saved, ports, failure = false) {
	const options = [];
	const uci = { get: (config, section, key) => {
		assert.equal(config, 'system'); assert.equal(section, 'usb'); assert.equal(key, 'port');
		return saved;
	} };
	const rpc = { declare: spec => {
		assert.equal(spec.object, 'luci'); assert.equal(spec.method, 'getUSBDevices');
		assert.deepEqual(spec.expect, { ports: [] });
		return () => failure ? Promise.reject(Error('RPC unavailable')) : Promise.resolve(ports);
	} };
	const section = { option: (type, key, label) => {
		const o = { type, key, label, values: new Map(), depends(key, value) { this.dependency = [key, value]; },
			value(key, label) { this.values.set(key, label); } };
		options.push(o); return o;
	} };
	const source = fs.readFileSync(path.join(directory, name + '.js'), 'utf8');
	const plugin = new Function('baseclass', 'form', 'rpc', 'uci', 'L', '_', source)(
		{ extend: x => x }, { MultiValue: 'MultiValue' }, rpc, uci,
		{ resolveDefault: (promise, fallback) => promise.catch(() => fallback) }, x => x);
	plugin.addFormOptions(section);
	return { plugin, options };
}

(async function() {
	for (const name of ['zx279133-los', 'zx279133-pon']) {
		const { plugin, options } = fixture(name);
		assert.equal(plugin.kernel, true);
		assert.equal(options.length, 0, 'automatic modes must not overwrite any UCI options');
		assert.match(plugin.trigger, /LOS|PON/);
		assert.ok(plugin.description.length > 0);
	}
	for (const [saved, ports, failure, expected] of [
		[['usb1-port1', 'usb2-port1'], [{ port: 'usb1-port1' }, { port: 'usb2-port1' }], false, ['usb1-port1', 'usb2-port1']],
		['1-1 2-1', [], false, ['usb1-port1', 'usb2-port1']],
		[['usb7-port3'], [{ port: 'usb4-port2' }, {}], false, ['usb7-port3']],
		[['usb7-port3'], [], true, ['usb7-port3']],
		[undefined, [], true, []],
		['', [], false, []]
	]) {
		const { plugin, options } = fixture('usbport', saved, ports, failure);
		assert.equal(plugin.kernel, true);
		assert.equal(options.length, 1);
		const o = options[0];
		assert.equal(o.key, 'port'); assert.equal(o.type, 'MultiValue');
		assert.deepEqual(o.dependency, ['trigger', 'usbport']);
		assert.deepEqual(o.cfgvalue('usb'), expected);
		assert.deepEqual(await o.load('usb'), expected);
		for (const name of ['usb1-port1', 'usb2-port1', ...expected]) assert.ok(o.values.has(name));
		if (!failure) for (const port of ports) if (port.port) assert.ok(o.values.has(port.port));
	}
	console.log('PASS: LOS/PON automatic choices, USB2/USB3 ports, legacy UCI, custom bindings and unavailable RPC');
})().catch(error => { console.error(error); process.exitCode = 1; });
