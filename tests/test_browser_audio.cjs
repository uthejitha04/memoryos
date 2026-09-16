// Protocol regression checks run against the actual browser helper functions.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(require('node:path').join(__dirname, '../static/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script); // Catch syntax errors in the entire page script.
const helpers = script.slice(script.indexOf('function catBytes'), script.indexOf('async function omiConnect'));
const sent = [];
const decoded = [];
const ctx = vm.createContext({ Uint8Array, Int16Array, DataView, Float32Array,
  omi: {recording:true,lastPkt:null,lastIndex:null,buf:[],ws:{readyState:1,send: b => sent.push(new Int16Array(b))}},
});
vm.runInContext(helpers, ctx);
ctx.omiEmitOpus = bytes => decoded.push([...bytes]);
function packet(id, index, values, codec=20) {
  const b = new Uint8Array([id & 255, id >> 8, index, ...values]);
  ctx.omiOnPacket(new DataView(b.buffer), codec);
}
packet(65534,0,[1]); packet(65535,1,[2]); packet(65535,1,[2]); packet(0,0,[3]);
assert.deepEqual(decoded, [[1,2]]);
packet(2,2,[9]); packet(3,0,[4]); packet(4,0,[5]);
assert.deepEqual(decoded, [[1,2],[4]]);
ctx.omi.lastPkt = null; ctx.omi.lastIndex = null; ctx.omi.buf = [];
packet(0,0,[232],1); packet(1,1,[3,24,252],1); packet(2,0,[0,0],1);
assert.deepEqual([...sent[0]], [1000,1000,-1000,-1000]);
assert.match(script, /if \(localCapture\)/);
assert.match(script, /"\/omi\/connect"/);
console.log('Browser packet assembly, loss recovery, 8k PCM, and script syntax passed');
