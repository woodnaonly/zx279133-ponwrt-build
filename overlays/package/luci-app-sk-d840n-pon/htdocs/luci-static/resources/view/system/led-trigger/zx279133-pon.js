'use strict';
'require baseclass';

return baseclass.extend({
	trigger: _('PON 注册状态（自动）'),
	description: _('PON 进入 O5 已注册状态时亮灯，其他状态熄灭。关闭灯光不会影响注册和网络连接。'),
	kernel: true,
	addFormOptions: function() {}
});
