// Non-interactive root shell on the flashed OpenWrt via LuCI's ubus dispatcher.
//   node tools/luci.cjs "cat /etc/openwrt_release"
// Blank root password by default; override with ONU_PASS=...
const http = require('http');

const HOST = process.env.ONU_HOST || '192.168.9.1';
const PASS = process.env.ONU_PASS === undefined ? '' : process.env.ONU_PASS;

function post(body, cookie) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const req = http.request({ host: HOST, port: 80, path: '/cgi-bin/luci/admin/ubus', method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data), ...(cookie ? { Cookie: cookie } : {}) } },
      (res) => {
        let buf = '';
        res.on('data', (d) => (buf += d));
        res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: buf }));
      });
    req.on('error', reject);
    req.setTimeout(30000, () => req.destroy(new Error('http timeout')));
    req.end(data);
  });
}

async function call(sid, cookie, type, method, params) {
  const r = await post({ jsonrpc: '2.0', id: 1, method: 'call', params: [sid, type, method, params] }, cookie);
  if (r.status !== 200) throw new Error(`http ${r.status}: ${r.body.slice(0, 200)}`);
  let j;
  try { j = JSON.parse(r.body) } catch (e) { throw new Error(`not json (status ${r.status}): ${r.body.slice(0, 200)}`) }
  if (j.error) throw new Error(`rpc error ${j.error.code}: ${j.error.message}`);
  // ubus replies with [status, payload]; a non-zero status means the call failed.
  const [status, payload] = j.result;
  if (status !== 0) throw new Error(`ubus status ${status}: ${JSON.stringify(payload)}`);
  return { result: payload, cookie: (r.headers['set-cookie'] || []).map((c) => c.split(';')[0]).join('; ') };
}

(async () => {
  const cmd = process.argv.slice(2).join(' ');
  if (!cmd) throw new Error('usage: node tools/luci.cjs "<shell command>"');
  const login = await call('00000000000000000000000000000000', null, 'session', 'login', { username: 'root', password: PASS });
  const sid = login.result.ubus_rpc_session;
  const exec = await call(sid, login.cookie, 'session', 'call', { method: 'file.exec', params: { command: '/bin/sh', params: ['-c', cmd] } });
  const o = exec.result;
  if (o.stdout) process.stdout.write(o.stdout);
  if (o.stderr) process.stdout.write('\n[stderr] ' + o.stderr);
  if (o.code !== 0) process.stdout.write(`\n[rc=${o.code}]`);
})().catch((e) => { console.error('FATAL ' + e.message); process.exit(1) });
