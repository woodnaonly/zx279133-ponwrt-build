// SPDX-License-Identifier: GPL-2.0-only
/*
 * XG-PON activation glue for the ZX279133 WAN netdev.
 *
 * The 49-word xgpon_nsyn image is not programmed. The vendor modules on
 * hand have mode_xgpon_nsyn_cfg, not mode_xgpon_nsyn_cfg_133, so that
 * image is not the one this board is allowed to write. The same rule
 * keeps the 0x17000000 PON MAC window untouched: a register is written
 * only when a Hal_WritePonGreg / Hal_WriteXmacReg trace names it, and
 * there is no such trace checked in here.
 *
 * What this file does is the part that can be true with no fibre:
 * the activation state starts at O1, eth0's carrier follows O5, the
 * red LOS input moves the state, and userspace can name one data GEM.
 */

#include <linux/etherdevice.h>
#include <linux/if_arp.h>
#include <linux/leds.h>
#include <net/genetlink.h>
#include <net/netlink.h>

#include "zx279133.h"
#include "zxpon.h"

static const struct nla_policy zxpon_policy[ZXPON_A_MAX] = {
	[ZXPON_A_STATE] = { .type = NLA_NUL_STRING },
	[ZXPON_A_SN] = NLA_POLICY_EXACT_LEN(ZXPON_SN_LEN),
	[ZXPON_A_REGID] = NLA_POLICY_EXACT_LEN(ZXPON_REGID_LEN),
	[ZXPON_A_ALLOC] = { .type = NLA_U16 },
	[ZXPON_A_GEM] = { .type = NLA_U16 },
	[ZXPON_A_OMCC] = { .type = NLA_U8 },
	[ZXPON_A_KEY] = NLA_POLICY_EXACT_LEN(ZXPON_KEY_LEN),
};

static struct zxpon zx_state;
static DEFINE_MUTEX(zx_pon_lock);
static struct net_device *zx_wan;
static struct net_device *zx_omci;
static struct led_classdev *zx_green;
static struct delayed_work zx_tick;
static int zx_alive;
static int zx_family_ok;
static int zx_nb_ok;

static void zxpon_apply_carrier(void)
{
	int o5;

	mutex_lock(&zx_pon_lock);
	o5 = zxpon_state(&zx_state) == ZXPON_O5;
	mutex_unlock(&zx_pon_lock);
	if (zx_wan) {
		if (o5)
			netif_carrier_on(zx_wan);
		else if (netif_carrier_ok(zx_wan))
			netif_carrier_off(zx_wan);
	}
	if (zx_green)
		led_set_brightness(zx_green, o5 ? zx_green->max_brightness : LED_OFF);
}

static void zxpon_tick_work(struct work_struct *work)
{
	mutex_lock(&zx_pon_lock);
	if (zx_alive)
		zxpon_tick(&zx_state, 100);
	mutex_unlock(&zx_pon_lock);
	zxpon_apply_carrier();
	if (zx_alive)
		schedule_delayed_work(&zx_tick, msecs_to_jiffies(100));
}

static int zxpon_net_event(struct notifier_block *nb, unsigned long event, void *ptr)
{
	struct net_device *dev = netdev_notifier_info_to_dev(ptr);

	if (!zx_wan || dev != zx_wan)
		return NOTIFY_DONE;
	if (event == NETDEV_CHANGE || event == NETDEV_UP)
		zxpon_apply_carrier();
	return NOTIFY_DONE;
}

static struct notifier_block zxpon_net_nb = {
	.notifier_call = zxpon_net_event,
};

static ssize_t pon_state_show(struct device *dev, struct device_attribute *attr,
			      char *buf)
{
	const char *name;

	mutex_lock(&zx_pon_lock);
	name = zxpon_state_name(&zx_state);
	mutex_unlock(&zx_pon_lock);
	return sysfs_emit(buf, "%s\n", name);
}
static DEVICE_ATTR_RO(pon_state);

static int zxpon_get_state(struct sk_buff *skb, struct genl_info *info)
{
	struct sk_buff *msg;
	void *hdr;
	const char *name;

	msg = genlmsg_new(NLMSG_GOODSIZE, GFP_KERNEL);
	if (!msg)
		return -ENOMEM;
	hdr = genlmsg_put_reply(msg, info, &zxpon_family, 0, info->genlhdr->cmd);
	if (!hdr) {
		nlmsg_free(msg);
		return -ENOMEM;
	}
	mutex_lock(&zx_pon_lock);
	name = zxpon_state_name(&zx_state);
	mutex_unlock(&zx_pon_lock);
	if (nla_put_string(msg, ZXPON_A_STATE, name)) {
		genlmsg_cancel(msg, hdr);
		nlmsg_free(msg);
		return -EMSGSIZE;
	}
	genlmsg_end(msg, hdr);
	return genlmsg_reply(msg, info);
}

static int zxpon_set_identity(struct sk_buff *skb, struct genl_info *info)
{
	const uint8_t *sn, *regid;

	if (!info->attrs[ZXPON_A_SN] || !info->attrs[ZXPON_A_REGID])
		return -EINVAL;
	sn = nla_data(info->attrs[ZXPON_A_SN]);
	regid = nla_data(info->attrs[ZXPON_A_REGID]);
	mutex_lock(&zx_pon_lock);
	zxpon_set_identity(&zx_state, sn, regid);
	mutex_unlock(&zx_pon_lock);
	zxpon_apply_carrier();
	return 0;
}

static int zxpon_add_tcont_cmd(struct sk_buff *skb, struct genl_info *info)
{
	u16 alloc, gem = 0;
	u8 omcc = 0;

	if (!info->attrs[ZXPON_A_ALLOC])
		return -EINVAL;
	alloc = nla_get_u16(info->attrs[ZXPON_A_ALLOC]);
	if (info->attrs[ZXPON_A_GEM])
		gem = nla_get_u16(info->attrs[ZXPON_A_GEM]);
	if (info->attrs[ZXPON_A_OMCC])
		omcc = nla_get_u8(info->attrs[ZXPON_A_OMCC]);
	mutex_lock(&zx_pon_lock);
	zxpon_add_tcont(&zx_state, alloc, omcc, gem);
	mutex_unlock(&zx_pon_lock);
	return 0;
}

static int zxpon_map_cmd(struct sk_buff *skb, struct genl_info *info)
{
	u16 gem;
	int rc;

	if (!info->attrs[ZXPON_A_GEM])
		return -EINVAL;
	gem = nla_get_u16(info->attrs[ZXPON_A_GEM]);
	mutex_lock(&zx_pon_lock);
	rc = zxpon_map_gem(&zx_state, gem);
	mutex_unlock(&zx_pon_lock);
	return rc;
}

static int zxpon_set_key_cmd(struct sk_buff *skb, struct genl_info *info)
{
	if (!info->attrs[ZXPON_A_KEY])
		return -EINVAL;
	mutex_lock(&zx_pon_lock);
	zxpon_set_key(&zx_state, nla_data(info->attrs[ZXPON_A_KEY]));
	mutex_unlock(&zx_pon_lock);
	return 0;
}

static const struct genl_ops zxpon_ops[] = {
	{
		.cmd = ZXPON_CMD_GET_STATE,
		.policy = zxpon_policy,
		.doit = zxpon_get_state,
	},
	{
		.cmd = ZXPON_CMD_SET_IDENTITY,
		.flags = GENL_ADMIN_PERM,
		.policy = zxpon_policy,
		.doit = zxpon_set_identity,
	},
	{
		.cmd = ZXPON_CMD_ADD_TCONT,
		.flags = GENL_ADMIN_PERM,
		.policy = zxpon_policy,
		.doit = zxpon_add_tcont_cmd,
	},
	{
		.cmd = ZXPON_CMD_ADD_GEM,
		.flags = GENL_ADMIN_PERM,
		.policy = zxpon_policy,
		.doit = zxpon_map_cmd,
	},
	{
		.cmd = ZXPON_CMD_MAP_GEM,
		.flags = GENL_ADMIN_PERM,
		.policy = zxpon_policy,
		.doit = zxpon_map_cmd,
	},
	{
		.cmd = ZXPON_CMD_SET_KEY,
		.flags = GENL_ADMIN_PERM,
		.policy = zxpon_policy,
		.doit = zxpon_set_key_cmd,
	},
};

static struct genl_family zxpon_family = {
	.name = "zxpon",
	.version = 1,
	.maxattr = ZXPON_A_MAX - 1,
	.ops = zxpon_ops,
	.n_ops = ARRAY_SIZE(zxpon_ops),
	.resv_start_op = ZXPON_CMD_MAX,
	.module = THIS_MODULE,
};

/* Userspace OMCI frames have nowhere traced to go on the wire. Count
 * them and drop them rather than invent a PON MAC write.
 */
static netdev_tx_t zxpon_omci_xmit(struct sk_buff *skb, struct net_device *dev)
{
	dev_kfree_skb(skb);
	dev->stats.tx_dropped++;
	return NETDEV_TX_OK;
}

static int zxpon_omci_open(struct net_device *dev)
{
	netif_carrier_on(dev);
	netif_start_queue(dev);
	return 0;
}

static int zxpon_omci_stop(struct net_device *dev)
{
	netif_stop_queue(dev);
	netif_carrier_off(dev);
	return 0;
}

static const struct net_device_ops zxpon_omci_ops = {
	.ndo_open = zxpon_omci_open,
	.ndo_stop = zxpon_omci_stop,
	.ndo_start_xmit = zxpon_omci_xmit,
};

static void zxpon_omci_setup(struct net_device *dev)
{
	dev->netdev_ops = &zxpon_omci_ops;
	dev->mtu = ZXPON_OMCI_LEN;
	dev->min_mtu = ZXPON_OMCI_LEN;
	dev->max_mtu = ZXPON_OMCI_LEN;
	dev->hard_header_len = 0;
	dev->addr_len = 0;
	dev->type = ARPHRD_NONE;
	dev->flags = IFF_POINTOPOINT | IFF_NOARP;
	dev->priv_flags |= IFF_NO_QUEUE;
	dev->needs_free_netdev = true;
}

void zx279133_pon_set_led(struct led_classdev *led)
{
	zx_green = led;
}

void zx279133_pon_note_rxsd(int light)
{
	if (!zx_alive)
		return;
	mutex_lock(&zx_pon_lock);
	zxpon_set_los(&zx_state, light ? 0 : 1);
	mutex_unlock(&zx_pon_lock);
	zxpon_apply_carrier();
}

void zx279133_pon_carrier_kick(struct net_device *ndev)
{
	if (ndev)
		zx_wan = ndev;
	zxpon_apply_carrier();
}

void zx279133_pon_attach(struct net_device *ndev)
{
	int rc;

	if (zx_alive)
		return;
	zxpon_init(&zx_state);
	zx_wan = ndev;
	if (device_create_file(&ndev->dev, &dev_attr_pon_state))
		dev_warn(&ndev->dev, "pon_state sysfs not created\n");
	zx_omci = alloc_netdev(0, "omci", NET_NAME_UNKNOWN, zxpon_omci_setup);
	if (zx_omci) {
		zx_omci->dev.parent = ndev->dev.parent;
		rc = register_netdev(zx_omci);
		if (rc) {
			free_netdev(zx_omci);
			zx_omci = NULL;
			dev_warn(&ndev->dev, "omci netdev failed: %d\n", rc);
		}
	}
	if (!zx_family_ok) {
		rc = genl_register_family(&zxpon_family);
		if (!rc)
			zx_family_ok = 1;
		else
			dev_warn(&ndev->dev, "zxpon genl failed: %d\n", rc);
	}
	rc = register_netdevice_notifier(&zxpon_net_nb);
	if (rc)
		dev_warn(&ndev->dev, "pon notifier failed: %d\n", rc);
	else
		zx_nb_ok = 1;
	zx_alive = 1;
	INIT_DELAYED_WORK(&zx_tick, zxpon_tick_work);
	schedule_delayed_work(&zx_tick, msecs_to_jiffies(100));
	zxpon_apply_carrier();
}

void zx279133_pon_stop(void)
{
	if (!zx_alive)
		return;
	zx_alive = 0;
	cancel_delayed_work_sync(&zx_tick);
	if (zx_nb_ok) {
		unregister_netdevice_notifier(&zxpon_net_nb);
		zx_nb_ok = 0;
	}
	if (zx_wan)
		device_remove_file(&zx_wan->dev, &dev_attr_pon_state);
	if (zx_omci) {
		unregister_netdev(zx_omci);
		zx_omci = NULL;
	}
	if (zx_family_ok) {
		genl_unregister_family(&zxpon_family);
		zx_family_ok = 0;
	}
	zx_wan = NULL;
}