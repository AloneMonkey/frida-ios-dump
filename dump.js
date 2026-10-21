import ObjC from 'frida-objc-bridge';

Process.getModuleByName('Foundation').ensureInitialized();
if (!ObjC.available) {
    throw new Error('Objective-C runtime is not available in the target process');
}

var O_RDONLY = 0;
var O_WRONLY = 1;
var O_RDWR = 2;
var O_CREAT = 512;
var O_TRUNC = 1024;

var SEEK_SET = 0;
var SEEK_CUR = 1;
var SEEK_END = 2;

function allocStr(str) {
    return Memory.allocUtf8String(str);
}

function putStr(addr, str) {
    if (typeof addr == "number") {
        addr = ptr(addr);
    }
    return addr.writeUtf8String(str);
}

function getByteArr(addr, l) {
    if (typeof addr == "number") {
        addr = ptr(addr);
    }
    return addr.readByteArray(l);
}

function getU8(addr) {
    if (typeof addr == "number") {
        addr = ptr(addr);
    }
    return addr.readU8();
}

function putU8(addr, n) {
    if (typeof addr == "number") {
        addr = ptr(addr);
    }
    return addr.writeU8(n);
}

function getU16(addr) {
    if (typeof addr == "number") {
        addr = ptr(addr);
    }
    return addr.readU16();
}

function putU16(addr, n) {
    if (typeof addr == "number") {
        addr = ptr(addr);
    }
    return addr.writeU16(n);
}

function getU32(addr) {
    if (typeof addr == "number") {
        addr = ptr(addr);
    }
    return addr.readU32();
}

function putU32(addr, n) {
    if (typeof addr == "number") {
        addr = ptr(addr);
    }
    return addr.writeU32(n);
}

function getU64(addr) {
    if (typeof addr == "number") {
        addr = ptr(addr);
    }
    return addr.readU64();
}

function putU64(addr, n) {
    if (typeof addr == "number") {
        addr = ptr(addr);
    }
    return addr.writeU64(n);
}

function getPt(addr) {
    if (typeof addr == "number") {
        addr = ptr(addr);
    }
    return addr.readPointer();
}

function putPt(addr, n) {
    if (typeof addr == "number") {
        addr = ptr(addr);
    }
    if (typeof n == "number") {
        n = ptr(n);
    }
    return addr.writePointer(n);
}

function malloc(size) {
    return Memory.alloc(size);
}

function getExportFunction(type, name, ret, args) {
    var nptr;
    nptr = Module.findGlobalExportByName(name);
    if (nptr === null) {
        throw new Error("Cannot find native export: " + name);
    } else {
        if (type === "f") {
            var funclet = new NativeFunction(nptr, ret, args);
            if (typeof funclet === "undefined") {
                console.log("parse error " + name);
                return null;
            }
            return funclet;
        } else if (type === "d") {
            var datalet = nptr.readPointer();
            if (typeof datalet === "undefined") {
                console.log("parse error " + name);
                return null;
            }
            return datalet;
        }
    }
}

var NSSearchPathForDirectoriesInDomains = getExportFunction("f", "NSSearchPathForDirectoriesInDomains", "pointer", ["int", "int", "int"]);
var NSTemporaryDirectory = getExportFunction("f", "NSTemporaryDirectory", "pointer", []);
var wrapper_open = getExportFunction("f", "open", "int", ["pointer", "int", "...", "int"]);
var nativeRead = getExportFunction("f", "read", "long", ["int", "pointer", "ulong"]);
var nativeWrite = getExportFunction("f", "write", "long", ["int", "pointer", "ulong"]);
var nativeSeek = getExportFunction("f", "lseek", "int64", ["int", "int64", "int"]);
var nativeErrno = getExportFunction("f", "__error", "pointer", []);
var close = getExportFunction("f", "close", "int", ["int"]);
var remove = getExportFunction("f", "remove", "int", ["pointer"]);
var access = getExportFunction("f", "access", "int", ["pointer", "int"]);
var dlopen = getExportFunction("f", "dlopen", "pointer", ["pointer", "int"]);

function read(fd, buffer, size) {
    while (true) {
        var result = nativeRead(fd, buffer, size);
        result = typeof result === "number" ? result : result.toNumber();
        if (result >= 0) return result;
        var error = nativeErrno().readS32();
        if (error === 4) continue; // EINTR on Darwin
        throw new Error("read failed (fd=" + fd + ", errno=" + error + ")");
    }
}

function readExact(fd, buffer, size) {
    var offset = 0;
    while (offset < size) {
        var count = read(fd, buffer.add(offset), size - offset);
        if (count === 0) {
            throw new Error("Unexpected EOF (fd=" + fd + ", read=" + offset + "/" + size + ")");
        }
        offset += count;
    }
}

function writeAll(fd, buffer, size) {
    var offset = 0;
    while (offset < size) {
        var result = nativeWrite(fd, buffer.add(offset), size - offset);
        var count = typeof result === "number" ? result : result.toNumber();
        if (count < 0) {
            var error = nativeErrno().readS32();
            if (error === 4) continue;
            throw new Error("write failed (fd=" + fd + ", errno=" + error + ")");
        }
        if (count === 0) throw new Error("write made no progress (fd=" + fd + ")");
        offset += count;
    }
}

function lseek(fd, offset, whence) {
    var result = nativeSeek(fd, offset, whence);
    var position = typeof result === "number" ? result : result.toNumber();
    if (position < 0) {
        throw new Error("lseek failed (fd=" + fd + ", errno=" + nativeErrno().readS32() + ")");
    }
    if (!Number.isSafeInteger(position)) throw new Error("File offset exceeds safe integer range");
    return position;
}

function getDocumentDir() {
    var manager = ObjC.classes.NSFileManager.defaultManager();
    function isWritableDirectory(directory) {
        var isDirectory = Memory.alloc(Process.pointerSize);
        isDirectory.writeU8(0);
        return manager.fileExistsAtPath_isDirectory_(directory, isDirectory) &&
            isDirectory.readU8() !== 0 && manager.isWritableFileAtPath_(directory);
    }
    var NSDocumentDirectory = 9;
    var NSUserDomainMask = 1;
    var npdirs = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, 1);
    if (!npdirs.isNull()) {
        var directories = ObjC.Object(npdirs);
        if (directories.count() > 0) {
            var documents = directories.objectAtIndex_(0);
            if (isWritableDirectory(documents)) return documents.toString();
        }
    }
    var temporary = NSTemporaryDirectory();
    if (!temporary.isNull()) {
        var directory = ObjC.Object(temporary);
        if (isWritableDirectory(directory)) return directory.toString();
    }
    throw new Error("No writable directory for dump files: Documents and temporary directory are unavailable");
}

function open(pathname, flags, mode) {
    if (typeof pathname == "string") {
        pathname = allocStr(pathname);
    }
    return wrapper_open(pathname, flags, mode);
}

var modules = null;
function getAllAppModules() {
    modules = new Array();
    var tmpmods = Process.enumerateModules();
    for (var i = 0; i < tmpmods.length; i++) {
        if (tmpmods[i].path.indexOf(".app") != -1) {
            modules.push(tmpmods[i]);
        }
    }
    return modules;
}

var FAT_MAGIC = 0xcafebabe;
var FAT_CIGAM = 0xbebafeca;
var MH_MAGIC = 0xfeedface;
var MH_CIGAM = 0xcefaedfe;
var MH_MAGIC_64 = 0xfeedfacf;
var MH_CIGAM_64 = 0xcffaedfe;
var LC_SEGMENT = 0x1;
var LC_SEGMENT_64 = 0x19;
var LC_ENCRYPTION_INFO = 0x21;
var LC_ENCRYPTION_INFO_64 = 0x2C;

function pad(str, n) {
    return Array(n-str.length+1).join("0")+str;
}

function swap32(value) {
    value = pad(value.toString(16),8)
    var result = "";
    for(var i = 0; i < value.length; i=i+2){
        result += value.charAt(value.length - i - 2);
        result += value.charAt(value.length - i - 1);
    }
    return parseInt(result,16)
}

function dumpModule(name, dumpId) {
    if (modules == null) {
        modules = getAllAppModules();
    }

    var targetmod = null;
    for (var i = 0; i < modules.length; i++) {
        if (modules[i].path === name) {
            targetmod = modules[i];
            break;
        }
    }
    if (targetmod == null) {
        throw new Error("Cannot find module: " + name);
    }
    var modbase = modules[i].base;
    var modsize = modules[i].size;
    var newmodpath = getDocumentDir() + "/frida-ios-dump-" + dumpId + ".fid";
    var oldmodpath = modules[i].path;


    if(!access(allocStr(newmodpath),0)){
        remove(allocStr(newmodpath));
    }

    var fmodule = open(newmodpath, O_CREAT | O_TRUNC | O_RDWR, 0o644);
    var foldmodule = open(oldmodpath, O_RDONLY, 0);

    if (fmodule == -1 || foldmodule == -1) {
        if (fmodule !== -1) close(fmodule);
        if (foldmodule !== -1) close(foldmodule);
        throw new Error("Cannot open module files: " + oldmodpath + " -> " + newmodpath);
    }

    try {
        var is64bit = false;
        var size_of_mach_header = 0;
        var magic = getU32(modbase);
        var cur_cpu_type = getU32(modbase.add(4));
        var cur_cpu_subtype = getU32(modbase.add(8));
        if (magic == MH_MAGIC || magic == MH_CIGAM) {
            is64bit = false;
            size_of_mach_header = 28;
        }else if (magic == MH_MAGIC_64 || magic == MH_CIGAM_64) {
            is64bit = true;
            size_of_mach_header = 32;
        }else{
            throw new Error("Unsupported Mach-O header");
        }

        var BUFSIZE = 64 * 1024;
        var buffer = malloc(BUFSIZE);

        readExact(foldmodule, buffer, 8);

        var fileoffset = 0;
        var filesize = 0;
        magic = getU32(buffer);
        if(magic == FAT_CIGAM || magic == FAT_MAGIC){
            var off = 4;
            var archs = swap32(getU32(buffer.add(4)));
            if (archs > Math.floor((BUFSIZE - 8) / 20)) {
                throw new Error("FAT architecture table exceeds buffer size");
            }
            lseek(foldmodule, 0, SEEK_SET);
            readExact(foldmodule, buffer, 8 + archs * 20);
            for (var i = 0; i < archs; i++) {
                var cputype = swap32(getU32(buffer.add(off + 4)));
                var cpusubtype = swap32(getU32(buffer.add(off + 8)));
                if(cur_cpu_type == cputype && cur_cpu_subtype == cpusubtype){
                    fileoffset = swap32(getU32(buffer.add(off + 12)));
                    filesize = swap32(getU32(buffer.add(off + 16)));
                    break;
                }
                off += 20;
            }

            if(fileoffset == 0 || filesize == 0) {
                throw new Error("Cannot find matching architecture in " + oldmodpath);
            }

        }else{
            filesize = lseek(foldmodule, 0, SEEK_END);
        }
        lseek(foldmodule, fileoffset, SEEK_SET);
        lseek(fmodule, 0, SEEK_SET);
        var remaining = filesize;
        while (remaining > 0) {
            var chunk = Math.min(remaining, BUFSIZE);
            readExact(foldmodule, buffer, chunk);
            writeAll(fmodule, buffer, chunk);
            remaining -= chunk;
        }

        var ncmds = getU32(modbase.add(16));
        var off = size_of_mach_header;
        var offset_cryptid = -1;
        var crypt_off = 0;
        var crypt_size = 0;
        var segments = [];
        for (var i = 0; i < ncmds; i++) {
            var cmd = getU32(modbase.add(off));
            var cmdsize = getU32(modbase.add(off + 4));
            if (cmd == LC_ENCRYPTION_INFO || cmd == LC_ENCRYPTION_INFO_64) {
                offset_cryptid = off + 16;
                crypt_off = getU32(modbase.add(off + 8));
                crypt_size = getU32(modbase.add(off + 12));
            }
            off += cmdsize;
        }

        if (offset_cryptid != -1) {
            if (crypt_off > filesize || crypt_size > filesize - crypt_off) {
                throw new Error("Encrypted region exceeds module file size");
            }
            lseek(fmodule, crypt_off, SEEK_SET);
            writeAll(fmodule, modbase.add(crypt_off), crypt_size);
            // Mark the module decrypted only after its decrypted bytes were written.
            var tpbuf = malloc(8);
            putU64(tpbuf, 0);
            lseek(fmodule, offset_cryptid, SEEK_SET);
            writeAll(fmodule, tpbuf, 4);
        }

        return newmodpath
    } catch (error) {
        throw new Error("Cannot dump " + oldmodpath + ": " + error.message);
    } finally {
        close(fmodule);
        close(foldmodule);
    }
}

function loadAllDynamicLibrary(app_path, bundle_root, visited) {
    var resolved_path = app_path.stringByResolvingSymlinksInPath().toString();
    bundle_root = bundle_root || resolved_path;
    visited = visited || new Set();
    if ((resolved_path !== bundle_root && !resolved_path.startsWith(bundle_root + "/")) ||
            visited.has(resolved_path)) {
        return;
    }
    visited.add(resolved_path);
    var defaultManager = ObjC.classes.NSFileManager.defaultManager();
    var errorPtr = Memory.alloc(Process.pointerSize);
    errorPtr.writePointer(NULL);
    var filenames = defaultManager.contentsOfDirectoryAtPath_error_(app_path, errorPtr);
    if (filenames === null) {
        throw new Error("Cannot scan app directory: " + app_path);
    }
    for (var i = 0, l = filenames.count(); i < l; i++) {
        var file_name = filenames.objectAtIndex_(i);
        var file_path = app_path.stringByAppendingPathComponent_(file_name);
        var resolved_file = file_path.stringByResolvingSymlinksInPath().toString();
        // RootHide's .jbroot points outside the app. Keep internal framework links.
        if (resolved_file !== bundle_root && !resolved_file.startsWith(bundle_root + "/")) {
            continue;
        }
        if (file_name.hasSuffix_(".framework")) {
            var bundle = ObjC.classes.NSBundle.bundleWithPath_(file_path);
            if (bundle.isLoaded()) {
                console.log("[frida-ios-dump]: " + file_name + " has been loaded. ");
            } else {
                if (bundle.load()) {
                    console.log("[frida-ios-dump]: Load " + file_name + " success. ");
                } else {
                    console.log("[frida-ios-dump]: Load " + file_name + " failed. ");
                }
            }
        } else if (file_name.hasSuffix_(".bundle") ||
                   file_name.hasSuffix_(".momd") ||
                   file_name.hasSuffix_(".strings") ||
                   file_name.hasSuffix_(".appex") ||
                   file_name.hasSuffix_(".app") ||
                   file_name.hasSuffix_(".lproj") ||
                   file_name.hasSuffix_(".storyboardc")) {
            continue;
        } else {
            var isDirPtr = Memory.alloc(Process.pointerSize);
            isDirPtr.writePointer(NULL);
            defaultManager.fileExistsAtPath_isDirectory_(file_path, isDirPtr);
            if (isDirPtr.readU8() !== 0) {
                loadAllDynamicLibrary(file_path, bundle_root, visited);
            } else {
                if (file_name.hasSuffix_(".dylib")) {
                    var is_loaded = 0;
                    for (var j = 0; j < modules.length; j++) {
                        if (modules[j].path.indexOf(file_name) != -1) {
                            is_loaded = 1;
                            console.log("[frida-ios-dump]: " + file_name + " has been dlopen.");
                            break;
                        }
                    }

                    if (!is_loaded) {
                        if (!dlopen(allocStr(file_path.toString()), 9).isNull()) {
                            console.log("[frida-ios-dump]: dlopen " + file_name + " success. ");
                        } else {
                            console.log("[frida-ios-dump]: dlopen " + file_name + " failed. ");
                        }
                    }
                }
            }
        }
    }
}

function handleMessage(message) {
    var taskId = ObjC.classes.NSUUID.UUID().UUIDString().toString();
    modules = getAllAppModules();
    var app_path = ObjC.classes.NSBundle.mainBundle().bundlePath();
    loadAllDynamicLibrary(app_path);
    // start dump
    modules = getAllAppModules();
    for (var i = 0; i  < modules.length; i++) {
        console.log("start dump " + modules[i].path);
        var result = dumpModule(modules[i].path, taskId + "-" + i);
        send({ dump: result, path: modules[i].path});
    }
    send({app: app_path.toString()});
    send({done: "ok"});
    recv(handleMessage);
}

// A spawned app must reach its main run loop before Foundation is used to dump it.
ObjC.schedule(ObjC.mainQueue, function () {
    // recv() may deliver an already queued message synchronously.
    // Register it on the script thread so dumping never blocks the app's main queue.
    setImmediate(function () {
        recv(handleMessage);
    });
});
