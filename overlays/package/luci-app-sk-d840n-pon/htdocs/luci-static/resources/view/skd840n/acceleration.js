'use strict';
'require view';
'require skd840n.ui as ui';
'require skd840n.status as status';
return view.extend({
	load: function() { return status.callStatus().catch(function() { return null; }); },
	render: function(data) { return ui.page('加速与防火墙', '软件转发、防火墙配置与硬件卸载不是同一件事。', data, status.callStatus, function(next) {
		var state = next.acceleration || {};
		return E('div', {}, [ui.card('当前能力', ui.grid([ui.tile('firewall4', state.firewall4 == null ? null : state.firewall4 ? '已安装' : '未安装'),
			ui.tile('硬件卸载实机验收', '尚未完成'), ui.tile('已验证硬件流数', state.flows)])),
			ui.note('本页只读。开关配置和驱动接口存在，不等于流量已经进入硬件。'), ui.link('原生防火墙配置', 'admin/network/firewall')]);
	}); }, handleSaveApply: null, handleSave: null, handleReset: null
});
