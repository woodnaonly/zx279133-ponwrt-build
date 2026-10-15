// SK-D840N (ZTE/Sanechips ZX279133 ONU) telnet client.
// The device echoes typed input, so markers must be device-side: `echo S$(( N ))`
// prints "S<N>" but echoes literally, so the regex can never match the echo.
//   ONU_PASS=... node onu.cjs exec "<cmd>" [--timeout ms]
//   ONU_PASS=... node onu.cjs pull <remote-file> <bytes>   # device nc-sends it to the PC
const net = require('net');
const fs = require('fs');

const HOST = process.env.ONU_HOST || '192.168.9.1';
const PORT = 23;
const PC_IP = process.env.PC_IP || '192.168.1.2';
const OUT = '/var/.o';
const IAC = 255, WILL = 251, WONT = 252, DO = 253, DONT = 254, SB = 250, SE = 240;

class Telnet {
  constructor(sock) {
    this.sock = sock;
    this.buf = Buffer.alloc(0);
    this.out = '';
    this.waiters = [];
    sock.on('data', (d) => this.feed(d));
  }
  feed(chunk) {
    this.buf = Buffer.concat([this.buf, chunk]);
    while (this.buf.length) {
      const i = this.buf.indexOf(IAC);
      if (i === -1) {
        this.out += this.buf.toString('utf8');
        this.buf = Buffer.alloc(0);
        break;
      }
      this.out += this.buf.slice(0, i).toString('utf8');
      this.buf = this.buf.slice(i);
      if (this.buf.length < 3) break;
      const cmd = this.buf[1];
      if (cmd === IAC) {
        this.out += '\xff';
        this.buf = this.buf.slice(2);
        continue;
      }
      if (cmd === SB) {
        const se = this.buf.indexOf(Buffer.from([IAC, SE]));
        if (se === -1) break;
        this.buf = this.buf.slice(se + 2);
        continue;
      }
      const opt = this.buf[2];
      this.buf = this.buf.slice(3);
      if (cmd === DO) this.sock.write(Buffer.from([IAC, WONT, opt]));
      else if (cmd === WILL) this.sock.write(Buffer.from([IAC, DONT, opt]));
    }
    this.check();
  }
  check() {
    this.waiters = this.waiters.filter((w) => {
      if (w.re.test(this.out)) {
        clearTimeout(w.t);
        w.res(this.out);
        return false;
      }
      return true;
    });
  }
  expect(re, ms = 15000) {
    return new Promise((resolve, reject) => {
      if (re.test(this.out)) return resolve(this.out);
      const w = { re };
      w.t = setTimeout(() => reject(new Error('timeout /' + re.source + '/ tail=' + JSON.stringify(this.out.slice(-200)))), ms);
      w.res = resolve;
      this.waiters.push(w);
    });
  }
  send(s) {
    this.sock.write(String(s) + '\r\n');
  }
  drain() {
    this.out = '';
  }
}

let seq = 0;
async function sync(tn, ms) {
  const n = ++seq;
  tn.send(`echo S$(( ${n} ))`);
  return tn.expect(new RegExp(`^S${n}$`, 'm'), ms).catch(() => null);
}

async function connect() {
  const sock = net.createConnection({ host: HOST, port: PORT });
  const tn = await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('connect timeout')), 8000);
    sock.once('connect', () => {
      clearTimeout(t);
      resolve(new Telnet(sock));
    });
    sock.once('error', reject);
  });
  await tn.expect(/login\s*:/i, 8000);
  tn.send(process.env.ONU_USER || 'root');
  await tn.expect(/password\s*:/i, 6000);
  tn.send(process.env.ONU_PASS);
  await new Promise((r) => setTimeout(r, 1500));
  if (/(login\s*:|incorrect|fail|denied|invalid)/i.test(tn.out) && !/\#\s*$/.test(tn.out.trim())) {
    throw new Error('login rejected: ' + JSON.stringify(tn.out.slice(-120)));
  }
  tn.drain();
  return tn;
}

async function run(tn, cmd, ms = 60000) {
  if ((await sync(tn, ms)) === null) throw new Error('shell not responsive');
  tn.drain();
  tn.send(`{ ${cmd}; } > ${OUT} 2>&1; echo done-$?`);
  if ((await sync(tn, ms)) === null) throw new Error('cmd timeout: ' + cmd.slice(0, 60));
  tn.drain();
  tn.send(`cat ${OUT}`);
  const r = await sync(tn, ms);
  if (r === null) throw new Error('read timeout');
  return r
    .replace(/\r/g, '')
    .split('\n')
    .filter((l) => !/\$\(\(|^cat \{|^S\d+$|done-\d+/.test(l.trim()) && l.trim() !== `cat ${OUT}`)
    .join('\n')
    .trim();
}

(async () => {
  const [cmd, a, b, c] = process.argv.slice(2);
  const tn = await connect();
  if (cmd === 'exec') {
    console.log(await run(tn, a, Number(b) || 60000));
  } else if (cmd === 'pull') {
    // local listener first, then make the device push the file
    const port = Number(c || 9999);
    const ws = fs.createWriteStream(b);
    const srv = net.createServer((cl) => {
      cl.on('data', (d) => ws.write(d));
      cl.on('close', () => ws.end(() => console.log(`got ${fs.statSync(b).size} bytes -> ${b}`)));
    });
    srv.listen(port, '0.0.0.0', async () => {
      await run(tn, `nc ${PC_IP} ${port} < ${a}`, Number(a && 120000));
    });
  } else {
    console.log('usage: exec "<cmd>" [ms] | pull <remote> <local> [port]');
  }
  tn.sock.destroy();
})().catch((e) => {
  console.error('FATAL', e.message);
  process.exit(1);
});
