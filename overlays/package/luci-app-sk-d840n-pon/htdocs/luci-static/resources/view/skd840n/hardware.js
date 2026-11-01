'use strict';
'require view';
'require rpc';
'require skd840n.ui as ui';
'require skd840n.status as status';
var board = rpc.declare({object: 'system', method: 'board', expect: {'': {}}, reject: true});
return view.extend({
	load: function() { return Promise.all([status.callStatus().catch(function() { return null; }), board().catch(function() { return {}; })]); },
	render: function(data) {
		var system = data[1];
		return ui.page('硬件身份', '读取本机信息，不从其他设备导入身份或校准。', data[0], status.callStatus, function(next) {
			return E('div', {}, [ui.card('本机平台', ui.grid([ui.tile('设备', system.model || next.platform.model), ui.tile('SoC', next.platform.soc),
				ui.tile('内核', system.kernel), ui.tile('系统', system.release && system.release.description)])), status.ports(next),
				ui.note('当前未实现原厂光学校准和身份分区解析。身份来自已迁移的本机配置或用户填写值，留空不代表恢复原厂身份。'), ui.link('线路与认证', 'admin/onu/config')]);
		});
	}, handleSaveApply: null, handleSave: null, handleReset: null
});
