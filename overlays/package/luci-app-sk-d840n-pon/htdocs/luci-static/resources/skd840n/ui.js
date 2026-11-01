'use strict';
'require baseclass';
'require poll';
'require rpc';
// Card layout and refresh interaction adapted from the donor; see UPSTREAM.
var callInterfaces = rpc.declare({object: 'network.interface', method: 'dump', expect: {'interface': []}, reject: true});
function value(v) { return v == null || v === '' ? '未提供' : String(v); }
function note(text) { return E('p', {class: 'skd-note'}, text); }
function tile(label, text) { return E('div', {class: 'skd-tile'}, [E('span', {class: 'skd-note'}, label), E('strong', {}, value(text))]); }
function grid(children) { return E('div', {class: 'skd-grid'}, children); }
function card(title, body) { return E('section', {class: 'skd-card'}, [E('h3', {}, title), body]); }
function link(label, path) { return E('a', {class: 'cbi-button', href: L.url(path)}, label); }
function networks(data) {
	return card('接口实时状态', grid((data || []).map(function(n) {
		return tile(n.interface, n.up === true ? '已连接' : n.up === false ? '未连接' : null);
	})));
}
function services() { return note('当前 ZX279133 光口收发尚未接通，未提供可验证的 OLT GEM/组播业务通道。'); }
function page(title, description, initial, refresh, render, extra) {
	if (!document.querySelector('link[data-skd840n]')) document.head.appendChild(E('link', {rel: 'stylesheet', href: L.resource('skd840n/status.css'), 'data-skd840n': '1'}));
	var live = E('div', {class: 'skd-live'}), updated = E('span', {class: 'skd-updated'});
	function display(data) {
		domContent(live, data == null ? card('状态不可用', note('状态读取失败或服务尚未启动。')) : render(data));
		updated.textContent = data == null ? '读取失败' : '已读取当前快照';
	}
	function reload() { return refresh().then(display).catch(function() { display(null); }); }
	display(initial);
	poll.add(reload, 5);
	return E('div', {class: 'skd-pon'}, [E('header', {class: 'skd-page-header'}, [E('div', {}, [E('p', {class: 'skd-eyebrow'}, 'SK-D840N · ZX279133'), E('h2', {}, title), note(description)]),
		E('div', {class: 'skd-refresh-controls'}, [updated, E('button', {type: 'button', class: 'cbi-button', click: reload}, '刷新')])]), live, extra || '']);
}
function domContent(node, child) { while (node.firstChild) node.removeChild(node.firstChild); node.appendChild(child); }
function diagnostics(data) {
	// A second allowlist protects exports even if future RPC methods grow fields.
	if (!data) return '状态不可用';
	var platform = data.platform || {}, capabilities = data.capabilities || {}, acceleration = data.acceleration || {}, transport = data.transport;
	return JSON.stringify({schema: data.schema, queried_at: data.queried_at, platform: {model: platform.model, soc: platform.soc},
		capabilities: {identity_configuration: capabilities.identity_configuration, generic_omci: capabilities.generic_omci,
			pon_transport: capabilities.pon_transport, hardware_offload_control: capabilities.hardware_offload_control,
			cpu_frequency_control: capabilities.cpu_frequency_control}, service: data.service && {
			live: data.service.live, enabled: data.service.enabled, configuration_valid: data.service.configuration_valid,
			identity_configured: data.service.identity_configured, engine_ready: data.service.engine_ready,
			transport_ready: data.service.transport_ready, error: data.service.error, kernel_identity_error: data.service.kernel_identity_error
		}, transport: transport && {implemented: transport.implemented, channel_ready: transport.channel_ready,
			rx_ready: transport.rx_ready, mic_ready: transport.mic_ready, submitted: transport.submitted, completed: transport.completed, error: transport.error},
		pon: {software_state: data.pon && data.pon.software_state, transport_ready: false},
		acceleration: {firewall4: acceleration.firewall4, hardware_verified: acceleration.hardware_verified, flows: acceleration.flows}, ports: (data.ports || []).map(function(p) {
			return {name: p.name, carrier: p.carrier, speed_mbps: p.speed_mbps, rx_packets: p.rx_packets, tx_packets: p.tx_packets, rx_errors: p.rx_errors, rx_dropped: p.rx_dropped};
		})}, null, 2);
}
return baseclass.extend({value: value, note: note, tile: tile, grid: grid, card: card, page: page,
	link: link, snapshot: callInterfaces, networks: networks, services: services, diagnostics: diagnostics});
