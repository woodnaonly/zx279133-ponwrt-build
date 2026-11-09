/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Unmodified phy_power_on/off from drivers/phy/phy-core.c, Linux 6.18.38,
 * cnjn/linux-mainline-zte-zxslc-sr1010
 * commit 96e5a2413524742e177e383fe523ef21de5ed238 (the SK kernel base).
 * The harness supplies PM/regulator/provider operations around this code.
 */
int phy_power_on(struct phy *phy)
{
	int ret = 0;

	if (!phy)
		goto out;

	if (phy->pwr) {
		ret = regulator_enable(phy->pwr);
		if (ret)
			goto out;
	}

	ret = phy_pm_runtime_get_sync(phy);
	if (ret < 0 && ret != -ENOTSUPP)
		goto err_pm_sync;

	ret = 0; /* Override possible ret == -ENOTSUPP */

	mutex_lock(&phy->mutex);
	if (phy->power_count == 0 && phy->ops->power_on) {
		ret = phy->ops->power_on(phy);
		if (ret < 0) {
			dev_err(&phy->dev, "phy poweron failed --> %d\n", ret);
			goto err_pwr_on;
		}
	}
	++phy->power_count;
	mutex_unlock(&phy->mutex);
	return 0;

err_pwr_on:
	mutex_unlock(&phy->mutex);
	phy_pm_runtime_put_sync(phy);
err_pm_sync:
	if (phy->pwr)
		regulator_disable(phy->pwr);
out:
	return ret;
}

int phy_power_off(struct phy *phy)
{
	int ret;

	if (!phy)
		return 0;

	mutex_lock(&phy->mutex);
	if (phy->power_count == 1 && phy->ops->power_off) {
		ret =  phy->ops->power_off(phy);
		if (ret < 0) {
			dev_err(&phy->dev, "phy poweroff failed --> %d\n", ret);
			mutex_unlock(&phy->mutex);
			return ret;
		}
	}
	--phy->power_count;
	mutex_unlock(&phy->mutex);
	phy_pm_runtime_put(phy);

	if (phy->pwr)
		regulator_disable(phy->pwr);

	return 0;
}
