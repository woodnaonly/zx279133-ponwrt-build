'use strict';
'require view';
'require skd840n.ui as ui';
'require skd840n.status as status';
return view.extend({
	load: function() { return status.callStatus().catch(function() { return null; }); },
	render: function(data) { return ui.page('诊断', '导出当前轻量状态快照，不执行命令或寄存器写入。', data, status.callStatus, function(next) {
		return E('div', {}, [status.ponCard(next), ui.card('诊断工具', E('div', {class: 'skd-actions'}, [
			E('button', {type: 'button', class: 'cbi-button', click: function() {
				var url = URL.createObjectURL(new Blob([ui.diagnostics(next)], {type: 'text/plain;charset=utf-8'}));
				var anchor = E('a', {href: url, download: 'sk-d840n-pon-status.txt'}); anchor.click(); setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
			}}, '下载脱敏快照'), ui.link('系统日志', 'admin/status/logs'), ui.link('网络诊断', 'admin/network/diagnostics') ])),
			ui.note('快照不包含 SN、REGID、LOID、密码、PSK 或原厂镜像。'), ui.card('驱动与协议状态', E('pre', {class: 'skd-raw'}, ui.diagnostics(next)))]);
	}); }, handleSaveApply: null, handleSave: null, handleReset: null
});
