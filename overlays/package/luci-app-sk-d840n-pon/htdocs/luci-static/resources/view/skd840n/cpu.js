'use strict';
'require view';
'require rpc';
'require skd840n.ui as ui';
var callStatus = rpc.declare({object: 'luci.skd840n-system', method: 'status', expect: {'': {}}, reject: true});
return view.extend({
	load: function() { return callStatus().catch(function() { return null; }); },
	render: function(data) { return ui.page('CPU 状态', '仅展示本机数据，不写入频率或时钟寄存器。', data, callStatus, function(next) {
		var cpu = next.cpu || {};
		return ui.card('ZX279133 CPU', E('div', {}, [ui.grid([ui.tile('在线核心数', cpu.cores), ui.tile('当前频率', cpu.current_khz == null ? null : cpu.current_khz / 1000 + ' MHz'),
			ui.tile('SoC 温度', next.temperature_mC == null ? null : (next.temperature_mC / 1000).toFixed(1) + ' °C')]), ui.note('当前平台无可用的 cpufreq 调频接口，本页只读；没有频率数据时不假定为 1000 MHz。')]));
	}); }, handleSaveApply: null, handleSave: null, handleReset: null
});
