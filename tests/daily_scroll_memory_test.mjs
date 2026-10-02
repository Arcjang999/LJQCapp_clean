// Deterministic DOM-event contract only; actual browser restoration is separate.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../ui/daily_navigation.py', import.meta.url), 'utf8');
const js = source.match(/SCROLL_JS = r'''([\s\S]*?)'''/)[1];
function surface() {
  const listeners = new Map();
  return {
    listeners,
    addEventListener(name, handler) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(handler);
    },
    removeEventListener(name, handler) { listeners.get(name)?.delete(handler); },
    fire(name, event = {}) { for (const handler of listeners.get(name) || []) handler(event); },
  };
}
const main = Object.assign(surface(), {
  scrollTop: 710, scrollHeight: 3000, clientHeight: 800,
  clientWidth: 1000, offsetWidth: 1000,
  getBoundingClientRect: () => ({left: 0, right: 1000, top: 0, bottom: 800}),
});
const document = Object.assign(surface(), {querySelector: () => main, scrollingElement: main});
const memory = new Map(), frames = new Map();
let frameID = 0;
const context = vm.createContext({
  document, location: {pathname: '/'},
  sessionStorage: {getItem: key => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value)},
  requestAnimationFrame: fn => { frames.set(++frameID, fn); return frameID; },
  cancelAnimationFrame: id => frames.delete(id),
});
const render = vm.runInContext(js.replace('export default function(component)', '(function(component)') + ')', context);
const page = 'show_daily_overview_page', key = '/:LJQC:daily-scroll:' + page;
const component = data => ({data});
memory.set(key, '916');
memory.set(key + ':restored', '1'); // A completed token from a prior server session.
const tick = count => {
  for (let n = 0; n < count; n++) {
    const queue = [...frames.values()]; frames.clear();
    for (const fn of queue) fn();
  }
};
let cleanup = render(component({page, restore_nonce: 0}));
document.fire('pointerdown', {clientX: 300, clientY: 700, pointerType: 'mouse'});
document.fire('click');
assert.equal(memory.get(key), '710');
assert.equal(memory.get(key + ':restored'), '0'); // User input marks interrupted restoration complete.
main.scrollTop = 0;
main.fire('scroll'); // Navigation can replace children before component cleanup.
assert.equal(memory.get(key), '710');
// Real Streamlit v2 calls default again when data changes WITHOUT first
// invoking the previous returned cleanup. The component must dispose itself.
assert.equal(render(component({page: null})), undefined);
main.fire('scroll');
document.fire('click');
assert.equal(memory.get(key), '710');
assert.equal([...main.listeners.values(), ...document.listeners.values()].reduce((n, set) => n + set.size, 0), 0);
main.scrollHeight = 1000;
const oldCleanup = cleanup;
const returnToken = '56c564229dec450cbaad9f0c79cc5bdd';
cleanup = render(component({page, restore_nonce: returnToken}));
oldCleanup(); // A later framework unmount of the old execution is harmless.
tick(9);
assert.equal(main.scrollTop, 200);
assert.equal(memory.get(key), '710');
main.scrollHeight = 3000;
tick(9);
assert.equal(main.scrollTop, 710);
assert.equal(memory.get(key + ':restored'), returnToken);
document.fire('click'); // Non-navigation button clicks can freeze, too.
main.fire('wheel');
main.scrollTop = 430; main.fire('scroll');
assert.equal(memory.get(key), '430');
document.fire('pointerdown', {clientX: 995, clientY: 200, pointerType: 'mouse'});
main.scrollTop = 530; main.fire('scroll');
assert.equal(memory.get(key), '530');
document.fire('click');
document.fire('keydown', {key: 'PageDown'});
main.scrollTop = 630; main.fire('scroll');
assert.equal(memory.get(key), '630');
cleanup();
assert.equal([...main.listeners.values(), ...document.listeners.values()].reduce((n, set) => n + set.size, 0), 0);
assert.equal(frames.size, 0);
console.log('PASS: leave-time zero scroll cannot overwrite; late layout restores; user scrolling resumes; auxiliary page and cleanup are isolated.');
