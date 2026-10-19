// Run with: node tests/test_agent_io.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'dump.js'), 'utf8');
const ioCode = source.slice(source.indexOf('function read('), source.indexOf('function getDocumentDir('));
const moduleCode = source.slice(source.indexOf('function pad('),
    source.indexOf('function loadAllDynamicLibrary('));

class Pointer {
    constructor(buffer, offset = 0) { this.buffer = buffer; this.offset = offset; }
    add(offset) { return new Pointer(this.buffer, this.offset + offset); }
}

function fixture(options = {}) {
    const original = Buffer.alloc(256, 0xee);
    const header = [0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0,
        0x2c, 24, 128, 8, 1, 0];
    header.forEach((value, index) => original.writeUInt32LE(value, index * 4));
    if (options.badRegion) original.writeUInt32LE(500, 44);
    const memory = Buffer.from(original);
    memory.fill(0x42, 128, 136);
    let input = original;
    if (options.fat) {
        input = Buffer.alloc(4096 + original.length);
        [0xcafebabe, 1, 0x0100000c, 0, 4096, original.length, 12]
            .forEach((value, index) => input.writeUInt32BE(value, index * 4));
        original.copy(input, 4096);
    }
    const output = Buffer.alloc(256);
    const positions = { 1: 0, 2: 0 };
    const closed = [];
    let errno = 0;
    let readInterrupted = false, writeInterrupted = false;
    const int64 = value => ({ toNumber: () => value });
    const context = vm.createContext({
        modules: [{ path: '/Example.app/Main', base: new Pointer(memory), size: memory.length }],
        getDocumentDir: () => '/Documents', allocStr: value => value, access: () => 1,
        open: (_, flags) => flags === 0 ? 2 : 1,
        close: fd => closed.push(fd),
        malloc: size => new Pointer(Buffer.alloc(size)),
        getU32: pointer => pointer.buffer.readUInt32LE(pointer.offset),
        putU64: (pointer, value) => pointer.buffer.writeBigUInt64LE(BigInt(value), pointer.offset),
        nativeErrno: () => ({ readS32: () => errno }),
        nativeRead: (fd, pointer, size) => {
            if (options.readError) { errno = 5; return int64(-1); }
            if (options.interrupt && !readInterrupted) {
                readInterrupted = true; errno = 4; return int64(-1);
            }
            const limit = options.eof ? input.length - 1 : input.length;
            const count = Math.max(0, Math.min(size, 3, limit - positions[fd]));
            input.copy(pointer.buffer, pointer.offset, positions[fd], positions[fd] + count);
            positions[fd] += count;
            return int64(count);
        },
        nativeWrite: (fd, pointer, size) => {
            if (options.interrupt && !writeInterrupted) {
                writeInterrupted = true; errno = 4; return int64(-1);
            }
            if (options.diskFull && pointer.buffer === memory) {
                errno = 28; return int64(-1);
            }
            if (options.zeroWrite) return int64(0);
            const count = Math.min(size, 2);
            pointer.buffer.copy(output, positions[fd], pointer.offset, pointer.offset + count);
            positions[fd] += count;
            return int64(count);
        },
        nativeSeek: (fd, offset, whence) => {
            if (options.seekError) { errno = 22; return int64(-1); }
            positions[fd] = whence === 2 ? input.length + offset : offset;
            return int64(positions[fd]);
        },
        O_CREAT: 512, O_TRUNC: 1024, O_RDWR: 2, O_RDONLY: 0, SEEK_SET: 0, SEEK_END: 2,
        MH_MAGIC: 0xfeedface, MH_CIGAM: 0xcefaedfe,
        MH_MAGIC_64: 0xfeedfacf, MH_CIGAM_64: 0xcffaedfe,
        FAT_MAGIC: 0xcafebabe, FAT_CIGAM: 0xbebafeca,
        LC_ENCRYPTION_INFO: 0x21, LC_ENCRYPTION_INFO_64: 0x2c,
    });
    vm.runInContext(ioCode + moduleCode, context);
    return { run: () => context.dumpModule('/Example.app/Main', 'task-0'), output, memory, closed };
}

for (const options of [{}, { fat: true }, { interrupt: true }, { fat: true, interrupt: true }]) {
    const item = fixture(options);
    assert.equal(item.run(), '/Documents/frida-ios-dump-task-0.fid');
    const expected = Buffer.from(item.memory);
    expected.writeUInt32LE(0, 48);
    assert.deepEqual(item.output, expected, 'Short reads and writes must preserve all bytes');
    assert.deepEqual(item.closed, [1, 2]);
}

for (const [options, message] of [
    [{ eof: true }, /Unexpected EOF/],
    [{ fat: true, eof: true }, /Unexpected EOF/],
    [{ readError: true }, /read failed.*errno=5/],
    [{ seekError: true }, /lseek failed.*errno=22/],
    [{ zeroWrite: true }, /write made no progress/],
    [{ diskFull: true }, /write failed.*errno=28/],
    [{ badRegion: true }, /Encrypted region exceeds/],
]) {
    const item = fixture(options);
    assert.throws(item.run, message);
    assert.deepEqual(item.closed, [1, 2], 'Failure must close both descriptors');
    if (options.diskFull) {
        assert.equal(item.output.readUInt32LE(48), 1,
            'A failed decrypted write must not clear cryptid');
    }
}
console.log('Native I/O: 4 success scenarios and 7 failure scenarios passed');
