// Run with: node tests/test_agent.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'dump.js'), 'utf8');
const moduleCode = source.slice(source.indexOf('function dumpModule('),
    source.indexOf('function loadAllDynamicLibrary('));
const handlerCode = source.slice(source.indexOf('function handleMessage('),
    source.indexOf('// A spawned app'));
const modules = [
    { name: 'SharedExtra', path: '/Example.app/First/SharedExtra', size: 1 },
    { name: 'Shared', path: '/Example.app/First/Shared', size: 1 },
    { name: 'Shared', path: '/Example.app/Second/Shared', size: 1 },
];
let task = 0;
const opened = [];
const sent = [];
const context = vm.createContext({
    modules,
    getAllAppModules: () => modules,
    getDocumentDir: () => '/Documents',
    loadAllDynamicLibrary: () => {},
    ObjC: { classes: {
        NSUUID: { UUID: () => ({ UUIDString: () => `task-${++task}` }) },
        NSBundle: { mainBundle: () => ({ bundlePath: () => '/Example.app' }) },
    } },
    allocStr: value => value,
    access: () => 1,
    // Stop before native memory access; exercise the actual path selection code.
    open: value => { opened.push(value); return -1; },
    close: () => {},
    O_CREAT: 512, O_TRUNC: 1024, O_RDWR: 2, O_RDONLY: 0,
    console: { log: () => {} },
    send: value => sent.push(value),
    recv: () => {},
});
vm.runInContext(moduleCode + handlerCode, context);
const dumpModule = context.dumpModule;
context.dumpModule = (name, id) => {
    const before = opened.length;
    assert.throws(() => dumpModule(name, id), /Cannot open module files/);
    assert.equal(opened[before + 1], name, 'Must select the exact module path');
    return opened[before];
};
context.handleMessage('dump');
context.handleMessage('dump');
const dumps = sent.filter(value => value.dump);
assert.equal(dumps.length, 6);
assert.equal(new Set(dumps.map(value => path.basename(value.dump))).size, 6,
    'Same-name modules and separate tasks must have unique temporary filenames');
assert.deepEqual(dumps.map(value => value.path),
    [...modules, ...modules].map(value => value.path));
console.log('Agent module paths and task isolation passed');
