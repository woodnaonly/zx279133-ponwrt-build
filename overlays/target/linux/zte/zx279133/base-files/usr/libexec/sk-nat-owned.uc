// SPDX-License-Identifier: GPL-2.0-only
// Accept only the exact table shape created by the old sk-nat script.
import { readfile } from 'fs';
let document;
try { document = json(readfile('/dev/stdin')); } catch (error) { exit(1); }
let tables = 0, chains = 0, rules = 0;
for (let item in document?.nftables || []) {
	if (length(keys(item)) != 1) exit(1);
	if (exists(item, 'metainfo')) continue;
	if (exists(item, 'table')) {
		let table = item.table;
		if (table.family != 'ip' || table.name != 'nat') exit(1);
		tables++;
	}
	else if (exists(item, 'chain')) {
		let chain = item.chain;
		if (chain.family != 'ip' || chain.table != 'nat' || chain.name != 'postrouting' ||
			chain.type != 'nat' || chain.hook != 'postrouting' || chain.prio != 100 || chain.policy != 'accept') exit(1);
		chains++;
	}
	else if (exists(item, 'rule')) {
		let rule = item.rule, expressions = rule.expr;
		if (rule.family != 'ip' || rule.table != 'nat' || rule.chain != 'postrouting' || length(expressions) != 3) exit(1);
		let match = expressions[0].match;
		if (length(keys(expressions[0])) != 1 || match?.op != '==' || match?.left?.meta?.key != 'oifname' ||
			type(match?.right) != 'string' || !match.right ||
			length(keys(expressions[1])) != 1 || !exists(expressions[1], 'counter') ||
			length(keys(expressions[2])) != 1 || !exists(expressions[2], 'masq') || expressions[2].masq != null) exit(1);
		rules++;
	}
	else exit(1);
}
exit(tables == 1 && chains == 1 && rules == 1 ? 0 : 1);
