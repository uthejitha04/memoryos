const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('static/index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = {};
const empty = { innerHTML: '' };
const state = { micOn: false, omiBt: false };
const context = { state, omi: { on: false }, $: id => elements[id] ||= {setAttribute(k,v) {this[k]=v;}}, document: {querySelector: () => empty}, setRec() {}, toast() {} };
vm.createContext(context);
vm.runInContext(script.slice(script.indexOf('function applyStatus('), script.indexOf('function setRec(')), context);
const status = { model:'small', model_ready:true, omi:'disconnected', capture_busy:false };
context.applyStatus(status);
assert.equal(elements.omiBtn.textContent, 'Connect Omi');
assert.equal(elements.micBtn.disabled, false);
context.applyStatus({...status, omi:'connected', omi_active:true, omi_connected:true, omi_recording:true, capture_busy:true, session:'test'});
assert.equal(elements.omiBtn.textContent, 'Disconnect Omi');
assert.equal(elements.omiBtn.disabled, false);
assert.equal(elements.micBtn.disabled, true);
assert.equal(elements.micSel.disabled, true);
assert.match(empty.innerHTML, /Recording/);
context.applyStatus({...status, omi:'connecting...', omi_active:true, capture_busy:true});
assert.match(empty.innerHTML, /Connecting audio/);
state.micOn = true;
context.applyStatus({...status, capture_busy:true, session:'test'});
assert.equal(elements.micBtn.disabled, false);
assert.equal(elements.omiBtn.disabled, false);
state.micOn = false;
context.applyStatus(status);
assert.equal(elements.micSel.disabled, false);
context.applyStatus({...status, omi_active:true, omi_connected:true});
assert.equal(elements.omiRecordBtn.disabled, false);
assert.equal(elements.omiRecordBtn.textContent, 'Start recording');
assert.match(elements.captureHint.textContent, /not recording/);
assert.match(empty.innerHTML, /Ready to listen/);
console.log('Capture controls: idle, Omi recording, connecting, microphone, and stopped states passed');

(async () => {
  let disconnected = false, closed = false;
  class Socket {
    constructor() { this.readyState = 1; queueMicrotask(() => this.onopen()); }
    close() { closed = true; }
  }
  const browser = {
    omi: {on:true, recording:false, dev:{gatt:{disconnect(){disconnected=true;}}}},
    state:{status:{}}, WebSocket:Socket, location:{protocol:'https:',host:'test'},
    setTimeout, clearTimeout, applyStatus(){}, toast(){}
  };
  vm.createContext(browser);
  vm.runInContext(script.slice(script.indexOf('async function omiStartRecording'), script.indexOf('$("omiRecordBtn").onclick')), browser);
  await browser.omiStartRecording();
  assert.equal(browser.omi.recording, true);
  browser.omiStopRecording();
  assert.equal(closed, true);
  assert.equal(disconnected, false);
  assert.equal(browser.omi.on, true);
  assert.equal(browser.omi.recording, false);
  await browser.omiStartRecording();
  assert.equal(browser.omi.recording, true);
  browser.omiStopRecording();
  console.log('Browser Omi recording stop/restart preserves Bluetooth connection');
})().catch(e => { console.error(e); process.exitCode = 1; });
