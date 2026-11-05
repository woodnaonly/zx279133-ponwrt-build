/* Node-only DOM model: no real browser, network, device or firmware writes. */
const vm=require('node:vm'),assert=require('node:assert/strict'),fs=require('node:fs');
const html=fs.readFileSync(process.argv[2],'utf8'),script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{value:'',checked:false,textContent:'',classList:{toggle(){}}});return elements.get(id);}
const context=vm.createContext({document:{getElementById:element},setInterval(){},fetch:()=>new Promise(()=>{})});
vm.runInContext(script,context);
const base={token:'a'.repeat(64),id:'1',installed_sha256:'b'.repeat(64),mode:'native-development',state:'ready',can_confirm:true,
  result:{sha256:'c'.repeat(64),kind:'uboot',writes:[]}};
function render(v){context.input=v;vm.runInContext('render(input)',context);}
function agree(){element('board').value='SK-D840N';element('agree').checked=true;vm.runInContext('buttons()',context);}
render(base);agree();assert.equal(element('confirm').disabled,false);
render({...base,done:1});assert.equal(element('confirm').disabled,false);
for(const change of [{token:'d'.repeat(64)},{id:'2'},{installed_sha256:'d'.repeat(64)},
                     {result:{...base.result,sha256:'d'.repeat(64)}}]){
  render(base);agree();render({...base,...change});
  assert.equal(element('board').value,'');assert.equal(element('agree').checked,false);
  assert.equal(element('confirm').disabled,true);
}
render({...base,mode:'native-ram-readonly'});agree();assert.equal(element('confirm').disabled,true);
assert.equal(element('reboot').disabled,true);
render({...base,mode:'native-resident-readonly'});agree();assert.equal(element('confirm').disabled,true);
assert.equal(element('reboot').disabled,true);
const continuous={...base,mode:'native-resident',power_mode:'continuous_required',result:{...base.result,kind:'system'}};
render(continuous);agree();assert.equal(element('confirm').disabled,true);
element('powerAck').checked=true;vm.runInContext('buttons()',context);assert.equal(element('confirm').disabled,false);
render({...continuous,done:1});assert.equal(element('powerAck').checked,true);
render({...continuous,id:'3'});assert.equal(element('powerAck').checked,false);assert.equal(element('confirm').disabled,true);
