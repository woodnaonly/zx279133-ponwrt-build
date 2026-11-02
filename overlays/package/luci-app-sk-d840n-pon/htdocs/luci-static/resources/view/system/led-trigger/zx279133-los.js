'use strict';
'require baseclass';

return baseclass.extend({
	trigger: _('光信号告警（LOS 自动）'),
	description: _('无光时亮红灯，有光时熄灭。选择永久关闭或其他触发器不会影响光信号检测。'),
	kernel: true,
	addFormOptions: function() {}
});
