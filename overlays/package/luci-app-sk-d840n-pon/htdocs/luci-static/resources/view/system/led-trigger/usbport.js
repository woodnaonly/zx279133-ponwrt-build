'use strict';
'require baseclass';
'require form';
'require rpc';
'require uci';

// The trigger is built into this board's kernel. The upstream optional LuCI
// package would depend on a kmod package this external-kernel target lacks.
const callUSB = rpc.declare({
	object: 'luci',
	method: 'getUSBDevices',
	expect: { ports: [] }
});

return baseclass.extend({
	trigger: _('USB 设备插拔（usbport）'),
	description: _('选中的 USB 端口接入设备时亮灯，全部拔出后熄灭。USB2 和 USB3 对应同一个物理插口。'),
	kernel: true,
	addFormOptions: function(s) {
		const o = s.option(form.MultiValue, 'port', _('USB 端口'));
		o.depends('trigger', 'usbport');
		o.rmempty = true;
		o.modalonly = true;
		o.cfgvalue = function(section_id) {
			const value = uci.get('system', section_id, 'port');
			const ports = Array.isArray(value) ? value : String(value || '').split(/\s+/);
			return ports.filter(Boolean).map(function(port) {
				const legacy = port.match(/^(\d+)-(\d+)$/);
				return legacy ? 'usb' + legacy[1] + '-port' + legacy[2] : port;
			});
		};
		o.load = function(section_id) {
			return L.resolveDefault(callUSB(), []).then(ports => {
				const names = new Set(['usb1-port1', 'usb2-port1', ...this.cfgvalue(section_id)]);
				for (const port of ports)
					if (typeof port.port === 'string')
						names.add(port.port);
				for (const name of names)
					this.value(name, name);
				return this.cfgvalue(section_id);
			});
		};
	}
});
