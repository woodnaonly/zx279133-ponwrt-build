'use strict';
'require view';
'require rpc';
'require uci';
'require poll';
'require dom';
'require skd840n.ui as ui';
'require skd840n.wanport as wanport';

var PANEL = ['lan1', 'lan2', 'lan3', 'lan4'];
var PROTO = [['dhcp', 'DHCP / IPoE'], ['pppoe', 'PPPoE'], ['static', '静态地址']];

// The window the board waits in before it puts the old configuration back.
var DEADLINE = 90;
// How many rounds of three seconds the board has to answer before the
// change is taken for good.  Six rounds is eighteen seconds, which is
// long enough for the network to have been reloaded under the new
// configuration - and a link that is down never gets there.
var CONFIRM_AFTER = 6;

function domContent(node, child) {
	while (node.firstChild) node.removeChild(node.firstChild);
	node.appendChild(child);
}

function dropdown(list, value) {
	var s = E('select', {class: 'cbi-input-select'});
	list.forEach(function(o) { s.appendChild(E('option', {value: o[0]}, o[1])); });
	s.value = value;
	return s;
}

function field(label, control, hint) {
	return E('div', {class: 'cbi-value'}, [
		E('label', {class: 'cbi-value-title'}, label),
		E('div', {class: 'cbi-value-field'}, [control, hint ? ui.note(hint) : ''])
	]);
}

function facts(pairs) {
	var d = E('dl', {class: 'skd-facts'});
	pairs.forEach(function(p) {
		d.appendChild(E('dt', {}, p[0]));
		d.appendChild(E('dd', {}, p[1]));
	});
	return d;
}

function say(text, tone) {
	return E('div', {class: 'skd-badge ' + (tone || '')}, text);
}

// The status of the board, as the privileged command reports it.
function statusCard(status) {
	if (!status) {
		return ui.card('状态不可用', ui.note('读不到板子的状态。特权命令没有返回，多半是页面的权限不够。'));
	}
	var w1 = status.wan1 || {}, w2 = status.wan2 || {}, npu = status.npu || {}, ad = status.adaptive || {};
	var tiles = ui.grid([
		ui.tile('WAN1（光口）', (w1.device || 'eth0') + ' · ' + (w1.proto || '-') + ' · ' + (w1.up ? '已连接' : '未连接')),
		ui.tile('WAN2（面板口）', (w2.device ? w2.device : '未使用') + ' · ' + (w2.proto || '-') + ' · ' + (w2.up ? '已连接' : '未连接')),
		ui.tile('主备', status.primary === 'wan2' ? '面板口优先' : '光口优先'),
		ui.tile('自适应', (ad.auto === '1' ? '开' : '关') + (ad.exclude ? '（排除 ' + ad.exclude + '）' : ''))
	]);
	return ui.card('当前状态', E('div', {}, [
		tiles,
		facts([
			['桥内网口', (status.bridge || []).join('、') || '（无）'],
			['NPPT 端口类型', 'wan_ports: ' + (npu.wan_ports || '（空）') + ' / wan_type: ' + (npu.wan_type || '?')],
			['管理口', status.mgmt_port || '（未识别）']
		])
	]));
}

// The status of the board, asked for with the address of the client.
// The address is what says which jack the client is in, so it is asked
// for once and kept.
function probe(addrs) {
	var best = null;
	return addrs.reduce(function(chain, addr) {
		return chain.then(function() {
			if (best && best.mgmt_port) return null;
			return wanport.status(addr).then(function(s) {
				if (!best) best = s;
				if (s && s.mgmt_port) best = s;
			}).catch(function() { return null; });
		});
	}, Promise.resolve(null)).then(function() { return best; });
}

return view.extend({
	load: function() {
		return wanport.clientAddresses().then(function(addrs) {
			return Promise.all([
				probe(addrs),
				uci.load('sk_wan').then(function() {
					return {
						auto: uci.get('sk_wan', 'wan', 'auto') || '1',
						primary: uci.get('sk_wan', 'wan', 'primary') || 'wan',
						exclude: uci.get('sk_wan', 'wan', 'exclude') || ''
					};
				}).catch(function() { return {auto: '1', primary: 'wan', exclude: ''}; })
			]);
		});
	},
	render: function(data) {
		var status = data[0] || null;
		var policy = data[1] || {auto: '1', primary: 'wan', exclude: ''};

		var updated = E('span', {class: 'skd-updated'});
		var live = E('div', {class: 'skd-live'});
		var banner = E('div', {class: 'skd-note'});

		var devSel = dropdown([['none', '不使用（只用光口上网）']].concat(PANEL.map(function(p) { return [p, p.toUpperCase()]; })),
			(status && status.wan2 && status.wan2.device) || 'none');
		var protoSel = dropdown(PROTO, (status && status.wan2 && status.wan2.proto) || 'dhcp');
		var primarySel = dropdown([['wan', '光口（WAN1）优先'], ['wan2', '面板口（WAN2）优先']], (status && status.primary) || policy.primary || 'wan');
		var autoBox = E('input', {type: 'checkbox', class: 'cbi-input-checkbox', checked: policy.auto === '1'});
		var excludeBox = E('input', {type: 'text', class: 'cbi-input-text', value: policy.exclude || ''});
		var userBox = E('input', {type: 'text', class: 'cbi-input-text', value: ''});
		var passBox = E('input', {type: 'password', class: 'cbi-input-text', value: ''});
		var ipBox = E('input', {type: 'text', class: 'cbi-input-text', value: ''});
		var maskBox = E('input', {type: 'text', class: 'cbi-input-text', value: ''});
		var gwBox = E('input', {type: 'text', class: 'cbi-input-text', value: ''});

		var pppoeBox = E('div', {}, [field('PPPoE 用户名', userBox), field('PPPoE 密码', passBox)]);
		var staticBox = E('div', {}, [field('IP 地址', ipBox), field('子网掩码', maskBox), field('网关', gwBox)]);

		function refreshFields() {
			var p = protoSel.value;
			pppoeBox.style.display = (p === 'pppoe') ? '' : 'none';
			staticBox.style.display = (p === 'static') ? '' : 'none';
		}
		protoSel.addEventListener('change', refreshFields);
		refreshFields();

		// The warning.  The jack the management client is in is the one
		// change that cuts the page off, so it is said out loud before it
		// is done - and the board puts the old configuration back if the
		// page does not come back.
		function mgmtWarning() {
			var mgmt = status && status.mgmt_port;
			if (devSel.value === 'none' || !mgmt || devSel.value !== mgmt) return '';
			return say('注意：' + mgmt.toUpperCase() + ' 就是你现在用的管理口。切换后管理连接会断，' + DEADLINE + ' 秒内没有确认，系统会自动还原。', 'skd-danger');
		}

		var warning = E('div', {}, mgmtWarning());
		devSel.addEventListener('change', function() {
			warning.innerHTML = '';
			warning.appendChild(mgmtWarning());
		});

		var pending = false, streak = 0, timer = null;

		function doConfirm() {
			if (!pending) return;
			pending = false;
			if (timer) { clearInterval(timer); timer = null; }
			wanport.confirmChange().then(function() {
				banner.textContent = '已确认，改动保留。';
			}).catch(function(err) {
				banner.textContent = '确认没有成功：' + (err && err.message ? err.message : String(err));
			});
		}

		function armPending() {
			pending = true;
			streak = 0;
			banner.textContent = '已提交。' + DEADLINE + ' 秒内没有确认，系统会自动还原。';
			if (timer) clearInterval(timer);
			timer = setInterval(function() {
				wanport.status().then(function() {
					streak += 1;
					if (streak >= CONFIRM_AFTER) doConfirm();
				}).catch(function() { streak = 0; });
			}, 3000);
		}

		function apply() {
			var device = devSel.value;
			var proto = protoSel.value;
			if (device !== 'none') {
				if (proto === 'pppoe' && !userBox.value) { banner.textContent = 'PPPoE 需要用户名。'; return; }
				if (proto === 'static' && !ipBox.value) { banner.textContent = '静态地址需要 IP 地址。'; return; }
			}
			if (mgmtWarning() && typeof window !== 'undefined' && window.confirm &&
				!window.confirm('管理连接会断。' + DEADLINE + ' 秒内没有确认，系统会自动还原。继续？')) return;
			banner.textContent = '正在应用…';
			wanport.savePolicy(autoBox.checked, excludeBox.value.trim()).then(function() {
				return wanport.applyWan2({
					device: device, proto: proto,
					username: userBox.value, password: passBox.value,
					ipaddr: ipBox.value, netmask: maskBox.value, gateway: gwBox.value,
					primary: primarySel.value
				});
			}).then(function() {
				armPending();
			}).catch(function(err) {
				banner.textContent = '没有改成：' + (err && err.message ? err.message : String(err));
			});
		}

		function reset() {
			banner.textContent = '正在恢复…';
			wanport.resetWan().then(function() {
				banner.textContent = '已恢复：光口是唯一的 WAN，面板口都回到桥里。';
				reloadStatus();
			}).catch(function(err) {
				banner.textContent = '没有恢复：' + (err && err.message ? err.message : String(err));
			});
		}

		function rollback() {
			banner.textContent = '正在撤销…';
			wanport.rollbackChange().then(function() {
				banner.textContent = '已撤销。';
				reloadStatus();
			}).catch(function(err) {
				banner.textContent = '没有撤销：' + (err && err.message ? err.message : String(err));
			});
		}

		function reloadStatus() {
			return wanport.status().then(function(s) {
				domContent(live, statusCard(s));
				updated.textContent = '已读取当前快照';
			}).catch(function() {
				domContent(live, statusCard(null));
				updated.textContent = '读取失败';
			});
		}

		var map = E('div', {class: 'skd-config'}, [
			E('div', {class: 'cbi-map'}, [
				E('h2', {}, 'WAN 接入口'),
				E('div', {class: 'cbi-section'}, [
					E('div', {class: 'cbi-section-node'}, [
						field('接入口', devSel, '面板上的 lan1 到 lan4。选「不使用」就只留光口这一个 WAN。'),
						field('上网方式', protoSel),
						pppoeBox,
						staticBox,
						field('主备', primarySel, '两条 WAN 都在的时候哪一条优先。首选的一条断了，另一条自己顶上。'),
						field('自适应', autoBox, '插了线的那个口会被自动选中：板子把口临时拿出桥里问一次 DHCP，有人应答就算数。'),
						field('排除的网口', excludeBox, '这些口永远不会被自适应拿走，用空格分开，例如 lan3。'),
						warning,
						E('div', {class: 'skd-actions'}, [
							E('button', {type: 'button', class: 'cbi-button cbi-button-action', click: apply}, '应用'),
							E('button', {type: 'button', class: 'cbi-button', click: doConfirm}, '确认保留'),
							E('button', {type: 'button', class: 'cbi-button', click: rollback}, '撤销上次改动'),
							E('button', {type: 'button', class: 'cbi-button cbi-button-remove', click: reset}, '恢复默认（只用光口）')
						])
					])
				])
			])
		]);

		if (!document.querySelector('link[data-skd840n]'))
			document.head.appendChild(E('link', {rel: 'stylesheet', href: L.resource('skd840n/status.css'), 'data-skd840n': '1'}));

		reloadStatus();
		poll.add(reloadStatus, 5);

		return E('div', {class: 'skd-pon'}, [
			E('header', {class: 'skd-page-header'}, [
				E('div', {}, [
					E('p', {class: 'skd-eyebrow'}, 'SK-D840N · ZX279133'),
					E('h2', {}, 'WAN 接入口'),
					ui.note('把面板上的任意一个网口变成第二个 WAN 口。光口恒是第一个 WAN，不在这里改。')
				]),
				E('div', {class: 'skd-refresh-controls'}, [updated,
					E('button', {type: 'button', class: 'cbi-button', click: reloadStatus}, '刷新')])
			]),
			banner, live, map
		]);
	}
});

