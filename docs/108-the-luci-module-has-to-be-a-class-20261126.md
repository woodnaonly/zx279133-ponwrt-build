# SK-D840N：LuCI 的模块要交出一个类，否则整页都打不开（2026-11-26）

## 判决

LuCI 的加载器不把模块的返回值当现成的对象用：它先把工厂函数求值，再对返回值
`new` 一次。所以**每个模块工厂必须交出一个构造器**。交一个普通对象，加载器就抛

```
TypeError: "skd840n.wanport" factory yields invalid constructor
```

而且这一页是**整个打不开** —— 不是某个视图坏掉，是页面在加载阶段就死了：加载器
把错误抛给 `LuCI.prototype.error`，主内容区被替换成一条报错，别的一个视图都看不到。

罪魁是 `skd840n/wanport.js`：它末尾 `return { ... }`，交了一个普通对象。修法是
`return baseclass.extend({ ... })`，并补上 `'require baseclass'`。

## 加载器的真规则（板上实读，不是猜的）

板上 `/www/luci-static/resources/luci.js` 里加载器的原文：

```js
_class = _factory.apply(_factory, [window, document, L].concat(instances));
if (!Class.isSubclass(_class))
    LuCI.prototype.error('TypeError', '"%s" factory yields invalid constructor', name);
...
const instance = new _class();
```

而 `Class` 的实现（同一文件里）：

```js
isSubclass(classValue) {
    return (typeof(classValue) == 'function' && classValue.prototype instanceof this);
}
```

⇒ **返回值必须是函数**，而且它的原型链上要有 `Class.prototype`。普通对象在
`typeof` 这一关就出局了（`'object'` 而不是 `'function'`）。

还剩一个问题：哪些东西能拿来 `.extend()`？同一个文件里加载器的类表是

```js
const classes = {baseclass: Class, dom: DOM, poll: Poll, request: Request,
                  session: Session, view: View};
```

但 `Class.extend` 里有一句 `Object.assign(ClassConstructor, this)` —— 静态方法被复制到
子类上；而 `Class.singleton(properties)` 返回的是 `Class.extend(properties).instantiate(...)`，
也就是**实例**。所以：

| require 到的名字 | 是什么 | 有 `.extend()` 吗 |
|---|---|---|
| `baseclass` | `Class` 本身 | 有 |
| `view` | `View`（子类构造器，静态已复制） | 有 |
| `dom` / `poll` / `request` / `session` | singleton 的**实例** | **没有** |
| 别的业务模块（如 `skd840n.ui`） | 实例 | **没有** |

⇒ 模块只能 `baseclass.extend({...})` 或 `view.extend({...})`。这也正是仓库里其余 14 个
模块的写法。

## 证据

### 1 板上的文件与仓库 HEAD 逐字节相同 ⇒ 不是旧 rootfs 的锅

```
7099d252ce636ff731b837ec87000a56  /www/luci-static/resources/skd840n/wanport.js   （板上，修复前）
7099d252ce636ff731b837ec87000a56  仓库 HEAD overlays/.../skd840n/wanport.js
```

所以这是仓库当前 HEAD 上的**活 bug**，不是刷机刷出来的。

### 2 用 node 复刻加载器：修前失败、修后通过

把板上 `luci.js` 里的 `Class` 实现和加载器的 `require` 逻辑照抄成一段 node 程序，
喂它真正的模块文件：

```
FAIL  wanport.before.js : TypeError: factory yields invalid constructor
ok    wanport.js        : a class, and new works (7 method(s))
```

7 个方法正是 `status` / `applyWan2` / `resetWan` / `confirmChange` / `rollbackChange` /
`clientAddresses` / `savePolicy`。修前那份精确复现了用户截图里的那句报错。

### 3 浏览器实测（板上，lan1）

修好之后把文件写进板子的 `/www`（板上 rootfs 是 jffs2 直接挂在 `/`，rw），回读校验
md5 一致（`f427d5aaded6652680a63a693725b91c`，4020 字节），然后在浏览器里打开
`/cgi-bin/luci/admin/onu/wanport`：

* 页面**完整渲染**：接入口 / 上网方式 / 主备 / 自适应 / 排除的网口，以及
  应用、确认保留、撤销上次改动、恢复默认 四个按钮，全在；
* 浏览器 console **0 条报错**（修前那里是一条 TypeError）。

## 为什么仓库自己的契约测试没抓住

`tests/test_sk_pon_ui.js` 跑 36 个视图渲染，全绿 —— 因为它的 fixture 把 baseclass
退化成了 `{extend: x => x}`：**返回普通对象和返回类，在它眼里一模一样**。
契约测试测的是「视图渲染」，不是「模块交出去的东西是不是构造器」，所以这个洞一直在。

## 门禁

* `tools/check-sk-luci-modules.py` —— 4 条：每个模块都导出类、扩展的基类确实被
  require、每个模块只有一个导出、门禁确实找到了模块（找到 0 个等于什么都没查）。
* `tests/test_sk_luci_modules.py` —— 7 条反控：把 `return baseclass.extend({` 改成
  `return {`（**就是这个 bug 本身**）、把 `view.extend` 改成 `return {`、把 `.extend`
  改成 `.extendX`、去扩展一个实例模块、整个删掉导出、删掉 `'require baseclass'`、
  再加一个顶层 `return` —— 每一种都必须被门禁点名。

两条工作流（6.18 发布线与 7.3 线）的 gates 作业都跑这两个文件，paths 列表里也登记了
它们 —— 改了门禁或反控本身，也要重新跑一次构建来验它。

## 上板

板子的 rootfs 是 jffs2 直接挂载在 `/`（rw，241 MB 用了 14.6 MB），所以修一个 `/www`
下的 JS **不必刷机**：PC 起一个只提供这一个文件的 HTTP 端，板子自己 `wget` 下来
校验 md5 再 `cp` 到位即可。原文件备份在板上 `/root/wanport.js.orig`。

## 状态卡片也读不到数据：契约少了一个 `ok` 字段

修完 TypeError 之后页面能开了，但状态卡片仍写着「状态不可用」。这是**另一处**缺陷，
和 TypeError 无关：

* 页面的 `run()` 要求 `parsed.ok === true` 才算成功；
* 而 `sk-wan-switch status` 输出的 JSON **没有 `ok` 字段** —— helper 的错误路径有
  `{"ok":false,"error":...}`、各个改配置的命令都有 `{"ok":true,...}`，唯独 `status`
  直接吐状态对象；
* `tests/test_sk_wan_switch.py` 从来没有断言过 `ok`，所以这条契约没有人守。

⇒ 改 helper：`status` 的输出补上 `"ok":true`，与它其它命令的协议一致（页面读到的还是
同一个对象，只是多一个字段）。消费方查过了：`sk-wan-adapt` 只看退出码、不解析 JSON，
页面读的是 `wan1`/`wan2`/`bridge`/`primary` 这些字段 —— 多一个 `ok` 对谁都没有影响。

契约测试补了两条断言：状态里的 `ok` 必须是 true；被拒绝的端口那条路（`die()`）必须报
`ok:false`。**改判据前先查实了旧依据**：`die()` 是唯一的错误出口（6 处 `die` 全走它），
所以失败路径的 `ok:false` 是有保证的。

板上的前后对照就是这道契约的反向验证：

| 板上的 helper | 页面上的状态卡片 |
|---|---|
| 旧版（没有 `ok`） | 读取失败 · 状态不可用 |
| 补上 `ok:true` 之后 | 当前状态：WAN1 eth0 · dhcp · 未连接 / WAN2 未使用 / 主备 光口优先 / 桥 lan1 lan2 lan3 lan4 / NPPT wan_type: N / 管理口 lan1 |

### 这条契约在本机跑不了（既有环境问题，与本次改动无关）

`tests/test_sk_wan_switch.py` 在本机（Windows + Git Bash）**会卡死**：脚本里
`json_escape()` 的 `sed | tr` 那一步在 MSYS 的管道里收不到 EOF。**用 HEAD 的原版
helper 跑同样卡住** ⇒ 与本次改动无关，是 Windows 特有的怪癖；板上（Linux）同样的代码
是秒回的。CI 在 Ubuntu 上跑这个测试（两条线的 gates 作业都跑），所以那里的判据才算数。

## 未做

* 浏览器里根账号**没有设密码**（`/etc/shadow` 里 `root:::0:99999:7:::`），页面顶部
  一直在提示「未设置密码」。
