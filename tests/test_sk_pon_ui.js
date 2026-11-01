// SPDX-License-Identifier: GPL-2.0-only
// Execute the actual LuCI view renderers with a small DOM/UCI/RPC fixture.
// No browser framework or network dependency is needed for these regression tests.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const resources = path.join(root, 'overlays/package/luci-app-sk-d840n-pon/htdocs/luci-static/resources');
const menu = JSON.parse(fs.readFileSync(path.join(root, 'overlays/package/luci-app-sk-d840n-pon/root/usr/share/luci/menu.d/luci-app-sk-d840n-pon.json'), 'utf8'));

class Element {
	constructor(tag, attrs = {}, children = []) {
		this.tag = tag; this.attrs = attrs; this.children = []; this.textContent = '';
		for (const child of [children].flat(Infinity)) if (child != null) this.appendChild(child);
	}
	appendChild(child) { this.children.push(child); return child; }
	removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
	get firstChild() { return this.children[0]; }
}
function content(node) {
	return node instanceof Element ? [node.textContent, ...node.children.map(content)].join(' ') : String(node);
}

function fixture(scenario) {
	const modules = {}, maps = [], polls = [];
	const configs = scenario.network || {};
	const uci = {load: async () => {}, get: (config, section, option) => (configs[section] || {})[option],
		sections: (config, type) => Object.entries(configs).filter(([, s]) => s['.type'] === type).map(([name, s]) => ({'.name': name, ...s}))};
	const form = {Map: class {
		constructor(...args) { this.args = args; this.sections = []; maps.push(this); }
		section(type, ...args) {
			const section = {args, options: [], tab() {}, formvalue() {}, option(type, name, title) {
				const option = {name, title, section: this, values: [], value(...v) { this.values.push(v); }, depends() {}};
				this.options.push(option); return option;
			}, taboption(tab, ...args) { return this.option(...args); }};
			this.sections.push(section); return section;
		}
		render() { return Promise.resolve(new Element('form', {}, this.args)); }
	}};
	const rpc = {declare: ({object, method}) => async () => {
		if (scenario.failRPC) throw Error('unavailable');
		if (object === 'luci.skd840n-pon') return scenario.pon;
		if (object === 'luci.skd840n-system') return scenario.system;
		if (object === 'system') return {};
		if (method === 'get_proto_handlers') return scenario.protocols || {};
		if (method === 'dump') return [];
		throw Error('unexpected RPC: ' + object + '.' + method);
	}};
	const context = vm.createContext({
		E: (tag, attrs, children) => new Element(tag, attrs, children),
		L: {url: x => '/cgi-bin/luci/' + x, resource: x => '/luci-static/resources/' + x,
			hasViewPermission: () => scenario.writable !== false, toArray: x => x == null ? [] : [].concat(x)},
		document: {head: new Element('head'), querySelector() { return null; }},
		TextEncoder, JSON, Object, String, Array, Promise, Blob, URL, setTimeout
	});
	function load(name) {
		if (modules[name]) return modules[name];
		const source = fs.readFileSync(path.join(resources, name + '.js'), 'utf8');
		const dependencies = [];
		const code = source.replace(/'require ([^']+)';/g, (_, dependency) => {
			const [module, alias] = dependency.split(' as ');
			dependencies.push([module, alias || module.split('.').at(-1)]); return '';
		});
		const builtins = {baseclass: {extend: x => x}, view: {extend: x => x}, poll: {add: f => polls.push(f)}, rpc, uci, form, 'tools.widgets': {}};
		const names = [], values = [];
		for (const [module, alias] of dependencies) {
			names.push(alias); values.push(builtins[module] || load(module.replaceAll('.', '/')));
		}
		return modules[name] = vm.runInContext('(function(' + names.join(',') + '){' + code + '\n})', context, {filename: name})(...values);
	}
	return {load, maps, polls};
}

const state = {schema: 1, platform: {model: 'SK-D840N', soc: 'ZX279133'}, capabilities: {},
	service: {live: true, enabled: true, configuration_valid: true, engine_ready: true},
	pon: {software_state: 'O1'}, ports: [], acceleration: {firewall4: false, flows: null}};
const views = Object.values(menu).filter(m => m.action.type === 'view').map(m => 'view/' + m.action.path);
assert.equal(views.length, 8);

(async function main() {
	let renders = 0;
	for (const scenario of [
		{pon: state, system: {cpu: {cores: 2, current_khz: null}, temperature_mC: null}},
		{pon: {...state, service: null, acceleration: {firewall4: true}}, system: {cpu: {cores: null}}},
		{failRPC: true},
		{pon: state, system: {cpu: {cores: 2}}, writable: false}
	]) {
		for (const name of views) {
			const test = fixture(scenario), page = test.load(name);
			const dom = await page.render(await page.load());
			assert.ok(dom instanceof Element, name);
			const text = content(dom);
			assert.ok(!/NaN|undefined|已生效/.test(text), name + ': fabricated or invalid display');
			if (name.endsWith('overview') && !scenario.failRPC) assert.match(text, /硬件线路未接通/);
			if (name.endsWith('cpu') && !scenario.failRPC) assert.match(text, /只读/);
			if (scenario.writable === false) assert.ok(test.maps.every(m => m.readonly));
			for (const poll of test.polls) await poll();
			renders++;
		}
	}
	const wan = {'.type': 'interface', device: 'eth0', proto: 'dhcp'};
	for (const protocols of [{}, {dhcp: {}}, {dhcp: {}, pppoe: {}, static: {}}]) {
		const test = fixture({pon: state, protocols, network: {wan}}), page = test.load('view/skd840n/internet');
		await page.render(await page.load());
		const sections = test.maps[0].sections;
		assert.equal(sections.length, protocols.dhcp ? 1 : 0);
		if (sections.length) assert.deepEqual(sections[0].options[0].values.map(v => v[0]), Object.keys(protocols));
	}
	const test = fixture({pon: state}), ui = test.load('skd840n/ui');
	const sensitive = {...state, password: 'SECRET', registration_id: 'SECRET', psk: 'SECRET',
		platform: {...state.platform, password: 'SECRET'}, capabilities: {password: 'SECRET'},
		service: {...state.service, password: 'SECRET'}, acceleration: {password: 'SECRET'}, ports: [{name: 'eth0', mac: 'SECRET', regid: 'SECRET'}]};
	assert.ok(!ui.diagnostics(sensitive).includes('SECRET'), 'recursive diagnostic field allowlists');
	assert.match(ui.diagnostics(sensitive), /SK-D840N/);
	console.log(`PASS: ${renders} view renders, menu routes, refresh, protocol discovery, read-only and credential redaction`);
})().catch(error => { console.error(error); process.exitCode = 1; });
