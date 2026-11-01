'use strict';
'require baseclass';
'require rpc';
'require skd840n.ui as ui';
var callStatus = rpc.declare({object: 'luci.skd840n-pon', method: 'status', expect: {'': {}}, reject: true});
function ponCard(data) {
	var service = data.service, transport = data.transport;
	return ui.card('PON 协议与线路', E('div', {}, [ui.grid([
		ui.tile('配置状态', !service ? '未提供有效服务状态' : !service.configuration_valid ? '配置校验失败' : '配置已保存'),
		ui.tile('通用 OMCI 引擎', !service ? '未提供' : service.engine_ready ? '就绪（不代表已注册）' : service.enabled ? '身份未就绪' : '本地服务未启用'),
		ui.tile('OMCI 发送实现', !transport ? null : transport.implemented ? '独立 IDM 队列 0' : '未提供'),
		ui.tile('OMCI 通道条件', !transport ? null : transport.channel_ready ? '满足发送前置条件（未做线路验收）' : '未满足 O5 / OMCC 前置条件'),
		ui.tile('光口收发', '硬件线路未接通'), ui.tile('软件状态机', data.pon && data.pon.software_state)
	]), ui.note('本地软件 O 状态、网口 carrier 或 send() 返回成功，均不作为 OLT 注册及光口发送成功的证据。')]));
}
function ports(data) {
	return ui.card('面板与数据侧接口', ui.grid((data.ports || []).map(function(p) {
		return ui.tile(p.name + ' · ' + p.role, p.carrier == null ? null : p.carrier ? '有链路' + (p.speed_mbps == null ? '' : ' · ' + p.speed_mbps + ' Mbps') : '无链路');
	})));
}
return baseclass.extend({callStatus: callStatus, ponCard: ponCard, ports: ports,
	card: function(title, ignored, body) { return ui.card(title, body); }});
