// Run with: node tests/test_agent_scan.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'dump.js'), 'utf8');
const scanner = source.slice(source.indexOf('function loadAllDynamicLibrary('),
    source.indexOf('function handleMessage('));
const root = '/Apps/Example.app';
const directories = new Map([
    [root, ['.jbroot', 'Outside.framework', 'Outside.dylib', 'Sibling',
        'Libraries', 'Alias', 'Linked.framework', 'Versions', 'Extension.appex', 'Nested.app']],
    [root + '/Libraries', ['back', 'test.dylib', 'loaded.dylib']],
    [root + '/Versions', ['Test.framework']],
]);
const links = new Map([
    ['/private/Apps', '/Apps'],
    [root + '/.jbroot', '/'],
    [root + '/Outside.framework', '/Outside.framework'],
    [root + '/Outside.dylib', '/Outside.dylib'],
    [root + '/Sibling', root + '-extra'],
    [root + '/Alias', root + '/Libraries'],
    [root + '/Libraries/back', root],
    [root + '/Linked.framework', root + '/Versions/Test.framework'],
]);

function resolve(value) {
    for (let count = 0; count < 32; count++) {
        const link = [...links.keys()].find(key => value === key || value.startsWith(key + '/'));
        if (!link) return value;
        value = path.posix.join(links.get(link), value.slice(link.length));
    }
    throw new Error('Test path did not resolve');
}

class NSString {
    constructor(value) { this.value = value; }
    toString() { return this.value; }
    hasSuffix_(suffix) { return this.value.endsWith(suffix); }
    stringByAppendingPathComponent_(name) {
        return new NSString(path.posix.join(this.value, name.toString()));
    }
    stringByResolvingSymlinksInPath() { return new NSString(resolve(this.value)); }
}

const scanned = [], libraries = [], frameworks = [];
const loaded = new Set();
const manager = {
    contentsOfDirectoryAtPath_error_(directory) {
        const real = resolve(directory.toString());
        scanned.push(real);
        assert.ok(directories.has(real), 'Must not scan outside the bundle: ' + real);
        const entries = directories.get(real);
        if (entries === null) return null;
        return { count: () => entries.length, objectAtIndex_: i => new NSString(entries[i]) };
    },
    fileExistsAtPath_isDirectory_(file, pointer) {
        pointer.value = directories.has(resolve(file.toString())) ? 1 : 0;
    },
};
const context = vm.createContext({
    ObjC: { classes: {
        NSFileManager: { defaultManager: () => manager },
        NSBundle: { bundleWithPath_: file => {
            const real = resolve(file.toString());
            return {
                isLoaded: () => loaded.has(real),
                load: () => { loaded.add(real); frameworks.push(real); return true; },
            };
        } },
    } },
    Memory: { alloc: () => ({ writePointer() {}, readU8() { return this.value; } }) },
    Process: { pointerSize: 8 }, NULL: null,
    modules: [{ path: root + '/Libraries/loaded.dylib' }],
    allocStr: value => value,
    dlopen: value => { libraries.push(resolve(value)); return { isNull: () => false }; },
    console: { log() {} },
});
vm.runInContext(scanner, context);
context.loadAllDynamicLibrary(new NSString('/private' + root));
assert.deepEqual(scanned, [root, root + '/Libraries', root + '/Versions']);
assert.deepEqual(libraries, [root + '/Libraries/test.dylib']);
assert.deepEqual(frameworks, [root + '/Versions/Test.framework']);

// Visited directories are scoped to one scan, not retained across dump tasks.
scanned.length = 0;
context.loadAllDynamicLibrary(new NSString(root));
assert.equal(scanned[0], root);

// A real in-bundle scan failure remains actionable instead of becoming count(null).
directories.set(root + '/Libraries', null);
assert.throws(() => context.loadAllDynamicLibrary(new NSString(root)),
    /Cannot scan app directory: .*Libraries/);
console.log('Agent scan: external links, internal aliases, cycles, frameworks and scan errors passed');
