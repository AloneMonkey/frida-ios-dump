// Run with: node tests/test_agent_temp.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'dump.js'), 'utf8');
const selector = source.slice(source.indexOf('function getDocumentDir('),
    source.indexOf('function open('));

function fixture(documents, temporary, entries) {
    const string = value => ({ toString: () => value });
    const pointer = value => ({ isNull: () => value === null, value });
    const checked = [];
    let tempCalls = 0;
    const context = vm.createContext({
        ObjC: {
            Object: pointer => pointer.value,
            classes: { NSFileManager: { defaultManager: () => ({
                fileExistsAtPath_isDirectory_: (directory, flag) => {
                    const name = directory.toString();
                    checked.push(name);
                    const entry = entries[name];
                    flag.writeU8(entry?.directory ? 1 : 0);
                    return entry !== undefined;
                },
                isWritableFileAtPath_: directory => entries[directory.toString()].writable,
            }) } },
        },
        NSSearchPathForDirectoriesInDomains: () => pointer(documents === null ? null : {
            count: () => documents.length, objectAtIndex_: index => string(documents[index]),
        }),
        NSTemporaryDirectory: () => { tempCalls++; return pointer(temporary === null ? null : string(temporary)); },
        Memory: { alloc: () => ({ writeU8(value) { this.value = value; }, readU8() { return this.value; } }) },
        Process: { pointerSize: 8 },
    });
    vm.runInContext(selector, context);
    return { select: () => context.getDocumentDir(), checked, tempCalls: () => tempCalls };
}

const writableDirectory = { directory: true, writable: true };
const defaults = fixture(['/Documents'], '/tmp', { '/Documents': writableDirectory, '/tmp': writableDirectory });
assert.equal(defaults.select(), '/Documents');
assert.equal(defaults.tempCalls(), 0, 'Normal apps must retain their Documents destination');

for (const documents of [null, [], ['/missing']]) {
    const test = fixture(documents, '/tmp', { '/tmp': writableDirectory });
    assert.equal(test.select(), '/tmp', 'Missing Documents must use the existing temporary directory');
}
for (const entry of [
    { directory: true, writable: false },
    { directory: false, writable: true },
]) {
    const test = fixture(['/Documents'], '/tmp', { '/Documents': entry, '/tmp': writableDirectory });
    assert.equal(test.select(), '/tmp', 'Read-only directories and ordinary files must be rejected');
}
for (const temporary of [null, '/missing', '/readonly', '/file']) {
    const test = fixture([], temporary, {
        '/readonly': { directory: true, writable: false },
        '/file': { directory: false, writable: true },
    });
    assert.throws(test.select, /No writable directory for dump files/);
}
console.log('Agent dump destination: Documents preference, temporary fallback and unavailable directories passed');
