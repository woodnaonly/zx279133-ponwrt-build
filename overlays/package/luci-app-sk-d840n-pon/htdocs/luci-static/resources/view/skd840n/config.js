'use strict';
'require view';
'require form';
'require uci';
'require skd840n.ui as ui';
'require skd840n.status as status';
function bytes(limit) {
	return function(section, value) { return !/[\x00-\x1f\x7f]/.test(value) && new TextEncoder().encode(value).length <= limit || '不能包含控制字符，最多 ' + limit + ' 字节。'; };
}
return view.extend({
	load: function() { return Promise.all([uci.load('pon'), status.callStatus().catch(function() { return null; })]); },
	render: function(data) {
		var m = new form.Map('pon', '本地协议配置', '保存和应用更新本地配置及协议服务，不代表光链路已接通。');
		m.readonly = !L.hasViewPermission();
		var s = m.section(form.NamedSection, 'pon', 'pon'), o;
		s.addremove = false; s.tab('identity', '身份与认证'); s.tab('advanced', 'OMCI 参数');
		o = s.taboption('identity', form.Flag, 'enabled', '启用本地协议服务'); o.rmempty = false;
		o.description = '加载通用 OMCI 引擎；不代表设备已向 OLT 注册。';
		o = s.taboption('identity', form.DummyValue, '_mode', '协议模型'); o.cfgvalue = function() { return 'XG-PON 软件模型；硬件线路能力以驱动实际状态为准'; };
		o = s.taboption('identity', form.Value, 'serial_number', 'ONU 序列号（SN）'); o.retain = true;
		o.validate = function(section, value) { return value === '' || /^(?:[\x20-\x7e]{4}[0-9a-fA-F]{8}|[0-9a-fA-F]{16})$/.test(value) || '请输入 12 字符 SN 或 16 位十六进制。'; };
		o.description = '没有有效 SN 时不启动协议引擎；留空不会自动读取原厂身份。';
		o = s.taboption('identity', form.ListValue, 'registration_id_format', 'Registration ID 输入格式');
		o.value('hex', '十六进制（36 字节／72 位）'); o.value('text', '文本（最多 36 字节）'); o.default = 'hex'; o.rmempty = false;
		o = s.taboption('identity', form.Value, 'registration_id', 'Registration ID'); o.password = true; o.retain = true;
		o.validate = function(section, value) {
			var format = this.section.formvalue(section, 'registration_id_format') || uci.get('pon', section, 'registration_id_format') || 'hex';
			return format === 'text' ? bytes(36)(section, value) : value === '' || /^[0-9a-fA-F]{72}$/.test(value) || '请输入 72 位十六进制，或留空使用全零值。';
		};
		[['loid', 'LOID', 24], ['loid_password', 'LOID 密码', 12], ['equipment_id', '设备型号', 20],
			['hardware_version', '硬件版本', 14], ['software_version', '软件版本', 14], ['operator_id', '运营商 ID', 4]].forEach(function(field) {
			o = s.taboption('identity', form.Value, field[0], field[1]); o.validate = bytes(field[2]); o.retain = true; o.password = field[0] === 'loid_password';
		});
		o = s.taboption('advanced', form.ListValue, 'omcc_version', 'OMCC 版本'); o.value('0xb0', '0xb0（默认）'); o.value('0x86', '0x86（兼容模式）'); o.default = '0xb0'; o.rmempty = false;
		o = s.taboption('advanced', form.Flag, 'disable_enhanced_security', '禁用增强认证（Class 332）'); o.default = '0'; o.rmempty = false;
		o = s.taboption('advanced', form.Value, 'omci_psk', 'OMCI PSK'); o.password = true; o.retain = true;
		o.validate = function(section, value) { return value === '' || /^[0-9a-fA-F]{32}$/.test(value) || '请输入 32 位十六进制或留空。'; };
		o.description = '协议配置与硬件密钥下发状态分开显示。';
		return m.render().then(function(form) { return ui.page('线路与认证', '配置与实际线路状态独立。不提供无后端的光口模式切换。', data[1], status.callStatus,
			status.ponCard, E('section', {class: 'skd-config'}, form)); });
	}
});
