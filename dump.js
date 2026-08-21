// frida-ios-dump agent -- ported to the Frida 17 API surface.
// No ObjC bridge: Frida 17 no longer bundles it, so directory walking and
// path discovery are done with libSystem exports instead.

var O_RDONLY = 0;
var O_WRONLY = 1;
var O_RDWR = 2;
var O_CREAT = 512;

var SEEK_SET = 0;
var SEEK_CUR = 1;
var SEEK_END = 2;

var DT_DIR = 4;
var DT_REG = 8;
var DT_LNK = 10;

function allocStr(str) {
    return Memory.allocUtf8String(str);
}

function toPtr(addr) {
    return (typeof addr == "number") ? ptr(addr) : addr;
}

function getU8(addr) {
    return toPtr(addr).readU8();
}

function getU16(addr) {
    return toPtr(addr).readU16();
}

function getU32(addr) {
    return toPtr(addr).readU32();
}

function getU64(addr) {
    return toPtr(addr).readU64();
}

function putU64(addr, n) {
    return toPtr(addr).writeU64(n);
}

function getPt(addr) {
    return toPtr(addr).readPointer();
}

function malloc(size) {
    return Memory.alloc(size);
}

function findGlobalExport(name) {
    if (typeof Module.findGlobalExportByName === "function") {
        return Module.findGlobalExportByName(name);
    }
    return Module.findExportByName(null, name);
}

function getExportFunction(type, name, ret, args) {
    var nptr = findGlobalExport(name);
    if (nptr === null) {
        console.log("cannot find " + name);
        return null;
    }
    if (type === "f") {
        return new NativeFunction(nptr, ret, args);
    } else if (type === "d") {
        return nptr.readPointer();
    }
}

var wrapper_open = getExportFunction("f", "open", "int", ["pointer", "int", "int"]);
var read = getExportFunction("f", "read", "int", ["int", "pointer", "int"]);
var write = getExportFunction("f", "write", "int", ["int", "pointer", "int"]);
var lseek = getExportFunction("f", "lseek", "int64", ["int", "int64", "int"]);
var close = getExportFunction("f", "close", "int", ["int"]);
var remove = getExportFunction("f", "remove", "int", ["pointer"]);
var access = getExportFunction("f", "access", "int", ["pointer", "int"]);
var dlopen = getExportFunction("f", "dlopen", "pointer", ["pointer", "int"]);
var getenv = getExportFunction("f", "getenv", "pointer", ["pointer"]);
var opendir = getExportFunction("f", "opendir", "pointer", ["pointer"]);
var readdir = getExportFunction("f", "readdir", "pointer", ["pointer"]);
var closedir = getExportFunction("f", "closedir", "int", ["pointer"]);

function open(pathname, flags, mode) {
    if (typeof pathname == "string") {
        pathname = allocStr(pathname);
    }
    return wrapper_open(pathname, flags, mode);
}

function exists(path) {
    return access(allocStr(path), 0) === 0;
}

// The app sandbox HOME is the data container, which always holds Documents/.
function getDocumentDir() {
    var home = getenv(allocStr("HOME"));
    if (home.isNull()) {
        return "/tmp";
    }
    var docs = home.readUtf8String() + "/Documents";
    return exists(docs) ? docs : home.readUtf8String() + "/tmp";
}

// struct dirent (arm64, 64-bit inode): ino 0, seekoff 8, reclen 16,
// namlen 18, type 20, name 21.
var DIRENT_TYPE_OFFSET = 20;
var DIRENT_NAME_OFFSET = 21;

function listDir(path) {
    var entries = [];
    var dir = opendir(allocStr(path));
    if (dir.isNull()) {
        return entries;
    }
    var entry;
    while (!(entry = readdir(dir)).isNull()) {
        var name = entry.add(DIRENT_NAME_OFFSET).readUtf8String();
        if (name === "." || name === "..") {
            continue;
        }
        entries.push({ name: name, type: getU8(entry.add(DIRENT_TYPE_OFFSET)) });
    }
    closedir(dir);
    return entries;
}

var modules = null;
function getAllAppModules() {
    modules = [];
    var tmpmods = Process.enumerateModules();
    for (var i = 0; i < tmpmods.length; i++) {
        if (tmpmods[i].path.indexOf(".app") != -1) {
            modules.push(tmpmods[i]);
        }
    }
    return modules;
}

// The main executable lives directly inside the .app bundle. Note that module
// index 0 is not reliable: jailbreak tooling (e.g. Dopamine's systemhook.dylib)
// is injected ahead of the app binary.
function getAppPath() {
    var main = Process.mainModule;
    if (main === undefined || main === null) {
        var mods = Process.enumerateModules();
        for (var i = 0; i < mods.length; i++) {
            if (mods[i].path.indexOf(".app/") != -1) {
                main = mods[i];
                break;
            }
        }
    }
    return main.path.substring(0, main.path.lastIndexOf("/"));
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
    return Array(n - str.length + 1).join("0") + str;
}

function swap32(value) {
    value = pad(value.toString(16), 8)
    var result = "";
    for (var i = 0; i < value.length; i = i + 2) {
        result += value.charAt(value.length - i - 2);
        result += value.charAt(value.length - i - 1);
    }
    return parseInt(result, 16)
}

function dumpModule(name) {
    if (modules == null) {
        modules = getAllAppModules();
    }

    var targetmod = null;
    for (var i = 0; i < modules.length; i++) {
        if (modules[i].path.indexOf(name) != -1) {
            targetmod = modules[i];
            break;
        }
    }
    if (targetmod == null) {
        console.log("Cannot find module");
        return;
    }
    var modbase = targetmod.base;
    var modsize = targetmod.size;
    var newmodname = targetmod.name;
    var newmodpath = getDocumentDir() + "/" + newmodname + ".fid";
    var oldmodpath = targetmod.path;

    if (exists(newmodpath)) {
        remove(allocStr(newmodpath));
    }

    var fmodule = open(newmodpath, O_CREAT | O_RDWR, 0);
    var foldmodule = open(oldmodpath, O_RDONLY, 0);

    if (fmodule == -1 || foldmodule == -1) {
        console.log("Cannot open file" + newmodpath);
        return;
    }

    var is64bit = false;
    var size_of_mach_header = 0;
    var magic = getU32(modbase);
    var cur_cpu_type = getU32(modbase.add(4));
    var cur_cpu_subtype = getU32(modbase.add(8));
    if (magic == MH_MAGIC || magic == MH_CIGAM) {
        is64bit = false;
        size_of_mach_header = 28;
    } else if (magic == MH_MAGIC_64 || magic == MH_CIGAM_64) {
        is64bit = true;
        size_of_mach_header = 32;
    }

    var BUFSIZE = 4096;
    var buffer = malloc(BUFSIZE);

    read(foldmodule, buffer, BUFSIZE);

    var fileoffset = 0;
    var filesize = 0;
    magic = getU32(buffer);
    if (magic == FAT_CIGAM || magic == FAT_MAGIC) {
        var off = 4;
        var archs = swap32(getU32(buffer.add(off)));
        for (var i = 0; i < archs; i++) {
            var cputype = swap32(getU32(buffer.add(off + 4)));
            var cpusubtype = swap32(getU32(buffer.add(off + 8)));
            if (cur_cpu_type == cputype && cur_cpu_subtype == cpusubtype) {
                fileoffset = swap32(getU32(buffer.add(off + 12)));
                filesize = swap32(getU32(buffer.add(off + 16)));
                break;
            }
            off += 20;
        }

        if (fileoffset == 0 || filesize == 0)
            return;

        lseek(fmodule, 0, SEEK_SET);
        lseek(foldmodule, fileoffset, SEEK_SET);
        for (var i = 0; i < parseInt(filesize / BUFSIZE); i++) {
            read(foldmodule, buffer, BUFSIZE);
            write(fmodule, buffer, BUFSIZE);
        }
        if (filesize % BUFSIZE) {
            read(foldmodule, buffer, filesize % BUFSIZE);
            write(fmodule, buffer, filesize % BUFSIZE);
        }
    } else {
        var readLen = 0;
        lseek(foldmodule, 0, SEEK_SET);
        lseek(fmodule, 0, SEEK_SET);
        while (readLen = read(foldmodule, buffer, BUFSIZE)) {
            write(fmodule, buffer, readLen);
        }
    }

    var ncmds = getU32(modbase.add(16));
    var off = size_of_mach_header;
    var offset_cryptid = -1;
    var crypt_off = 0;
    var crypt_size = 0;
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
        var tpbuf = malloc(8);
        putU64(tpbuf, 0);
        lseek(fmodule, offset_cryptid, SEEK_SET);
        write(fmodule, tpbuf, 4);
        lseek(fmodule, crypt_off, SEEK_SET);
        write(fmodule, modbase.add(crypt_off), crypt_size);
    }

    close(fmodule);
    close(foldmodule);
    return newmodpath
}

var SKIP_SUFFIXES = [".bundle", ".momd", ".strings", ".appex", ".app", ".lproj", ".storyboardc"];

function hasSuffix(str, suffix) {
    return str.length >= suffix.length && str.indexOf(suffix, str.length - suffix.length) !== -1;
}

function isLoaded(path) {
    for (var j = 0; j < modules.length; j++) {
        if (modules[j].path.indexOf(path) != -1) {
            return true;
        }
    }
    return false;
}

function tryDlopen(path, label) {
    if (isLoaded(label)) {
        console.log("[frida-ios-dump]: " + label + " has been loaded.");
        return;
    }
    if (dlopen(allocStr(path), 9).isNull()) {
        console.log("[frida-ios-dump]: Load " + label + " failed.");
    } else {
        console.log("[frida-ios-dump]: Load " + label + " success.");
    }
}

function loadAllDynamicLibrary(app_path) {
    var entries = listDir(app_path);
    for (var i = 0; i < entries.length; i++) {
        var file_name = entries[i].name;
        var file_path = app_path + "/" + file_name;

        if (hasSuffix(file_name, ".framework")) {
            // Framework binary conventionally shares the bundle's base name.
            var binary = file_path + "/" + file_name.substring(0, file_name.length - ".framework".length);
            if (exists(binary)) {
                tryDlopen(binary, file_name);
            } else {
                console.log("[frida-ios-dump]: Cannot locate binary of " + file_name);
            }
            continue;
        }

        var skip = false;
        for (var s = 0; s < SKIP_SUFFIXES.length; s++) {
            if (hasSuffix(file_name, SKIP_SUFFIXES[s])) {
                skip = true;
                break;
            }
        }
        if (skip) {
            continue;
        }

        if (entries[i].type == DT_DIR) {
            loadAllDynamicLibrary(file_path);
        } else if (hasSuffix(file_name, ".dylib")) {
            tryDlopen(file_path, file_name);
        }
    }
}

function handleMessage(message) {
    modules = getAllAppModules();
    var app_path = getAppPath();
    loadAllDynamicLibrary(app_path);
    // start dump
    modules = getAllAppModules();
    for (var i = 0; i < modules.length; i++) {
        console.log("start dump " + modules[i].path);
        var result = dumpModule(modules[i].path);
        send({ dump: result, path: modules[i].path });
    }
    send({ app: app_path });
    send({ done: "ok" });
    recv(handleMessage);
}

recv(handleMessage);
