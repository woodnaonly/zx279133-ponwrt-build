'use strict';
'require view';
'require skd840n.ui as ui';
'require skd840n.status as status';
return view.extend({
	load: function() { return status.callStatus().catch(function() { return null; }); },
	render: function(data) { return ui.page('运行状态', 'SK-D840N 本地协议状态与实际线路能力分开显示。', data, status.callStatus,
		function(next) { return E('div', {}, [status.ponCard(next), status.ports(next)]); }); },
	handleSaveApply: null, handleSave: null, handleReset: null
});
