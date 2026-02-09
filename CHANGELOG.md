# Changelog

All notable changes to frida-ios-dump are documented in this file.

## [Unreleased] – Frida 17 compatibility and robustness

This release adds compatibility with **Frida 17.x** (including the QJS runtime where several JavaScript APIs changed) and improves reliability on devices where spawn is slow or times out.

### Added

- **`-a` / `--attach`**: Attach only to an already running app (no spawn). Use when `device.spawn()` times out (e.g. on older devices). Open the app on the device, then run e.g. `python dump.py -a com.example.app`.
- **`-v` / `--verbose`**: Print device script `console.log` output on the host for debugging.
- **Configurable timeouts**: `SCP_SOCKET_TIMEOUT` (default 300s) and `DUMP_WAIT_TIMEOUT` (default 600s) to avoid hanging on large apps or slow SCP.
- **Clear error handling**: When attach/spawn or script load times out, a short tip is printed (e.g. suggest opening the app and using `-a`). If the dump/SCP wait times out, a message explains what to check (app in foreground, SSH, frida-server).
- **Device log forwarding**: Script `console.log` messages from `dump.js` are printed on the host with a `[device]` prefix so progress and errors are visible without `-v`.

### Changed

- **dump.py**
  - `on_message`: Handle Frida log messages (`type: 'log'`) and string payloads; forward them to the host. Progress bar is created/freed per message with correct `finally` cleanup.
  - `open_target_app`: New `attach_only` parameter. When set and the app is not running, print a clear message instead of attempting spawn. On timeout exceptions, print a tip.
  - `start_dump`: Call `finished.clear()` before each run. Wait with `finished.wait(timeout=DUMP_WAIT_TIMEOUT)`; on timeout, print message and detach. Wrap script load/post in try/except and suggest opening the app on timeout.
  - Skip SCP when `payload['dump']` is missing or empty.
  - When attach fails (session is `None`), print a short message and set exit code to 1.
  - All user-facing strings kept in English for the PR (tips and errors).

- **dump.js**
  - **Frida 17 / QJS compatibility:**
    - **Module.ensureInitialized**: Replaced `Module.ensureInitialized('Foundation')` with `Process.findModuleByName('Foundation')` then `foundation.ensureInitialized()` (instance method in current API).
    - **Module.findExportByName**: Replaced `Module.findExportByName(null, name)` with `Module.findGlobalExportByName(name)`.
    - **Process.enumerateModules**: Replaced deprecated `Process.enumerateModulesSync()` with `Process.enumerateModules()`.
    - **ObjC**: On Frida 17 iOS, `ObjC` may be undefined. Added `ObjC_available` and fallbacks: app path from `Process.mainModule.path` when ObjC is missing; `getDocumentDir()` uses `Process.getHomeDir() + '/Documents'`; `loadAllDynamicLibrary` no-ops when ObjC is unavailable.
    - **Memory / NativePointer**: Replaced `Memory.readU8(addr)` / `Memory.writeU8(addr, n)` (and U16/U32/U64/pointer) with `addr.readU8()` / `addr.writeU8(n)` etc., and `nptr.readPointer()` / `addr.writePointer(ptr(0))` where applicable, for compatibility with the current runtime.
    - **NSFileManager error and BOOL pointers**: Use `ptr(0)` instead of `NULL`; use `Memory.writePointer(ptr, ptr(0))` or the pointer’s `writePointer`; read directory flag with `isDirPtr.readU8()` instead of `Memory.readPointer()` (BOOL is not a pointer).
  - **Robustness:** Only `send({ dump: result, path: ... })` when `dumpModule()` returns a non-null result (avoids sending invalid payloads).
  - **Debug:** Extra `console.log` steps in `handleMessage` (handleMessage started, getAllAppModules done, app_path, loadAllDynamicLibrary done, module count, sending app path/done) so device-side progress is visible on the host.

### Fixed

- Script no longer assumes `Module.ensureInitialized` is a static method or that `Module.findExportByName(null, name)` exists.
- Script no longer relies on `Process.enumerateModulesSync()` (removed in current Frida).
- Script works when `ObjC` is undefined (Frida 17 iOS regression): app path and Documents path are derived without ObjC when needed.
- Script works when `Memory.readU32`/`Memory.writeU32` etc. are not available: uses `NativePointer` read/write methods instead.
- Dump no longer hangs indefinitely: wait has a timeout and prints what to check on timeout.
- SCP and dump wait use longer default timeouts to reduce false timeouts on large apps or slow links.

### Requirements

- Frida 17.x (tested with 17.5.2) on host and frida-server on the device. Older Frida versions may still work; the new code paths use APIs that exist in Frida 17.
- For spawn-related timeouts, use `-a` and run the target app on the device before executing the dump.
