# frida-ios-dump
Pull a decrypted IPA from a jailbroken device

## AI automation

Give your AI tool the link to [SKILL.md](SKILL.md) and ask it to follow the instructions to set up the environment and export your chosen app. The file covers repository download, dependencies, direct USB SSH, SSH aliases, optional key setup, and IPA validation. A jailbroken device with Frida and SSH running is required.


## Usage

 1. Install [Frida](https://frida.re/) on the jailbroken device. Use the same Frida version on the device and host (current target: 17.23.3).
 2. Set up Python 3.10+ and the Objective-C bridge dependency (requires Node.js/npm):

    ```sh
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade -r requirements.txt
    npm install
    ```

    Alternatively, `pnpm install --frozen-lockfile` installs the bridge using the included lockfile.

    On Windows (PowerShell), use:

    ```powershell
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    npm install
    .\.venv\Scripts\python.exe dump.py -l
    ```

    IPA packaging uses Python's standard library; no external `zip`, `chmod`, or `tar` command is required. On Windows, device symlinks and Unix permissions are recorded and written directly into the IPA, without creating local symlinks or making downloaded files read-only. The full Windows/device workflow has not yet been tested on a Windows host.

 3. Connect the unlocked device over USB and ensure its SSH service is running. The script connects directly to device SSH port 22 through Frida's USB channel. No local port forwarding or `iproxy` is required. `-p` selects the device SSH port, for example `-p 44` for a service listening on port 44. A localhost hostname or alias uses USB regardless of its port; a remote hostname/IP uses TCP. Use `--ssh-transport usb` or `--ssh-transport tcp` to select the transport explicitly.
 4. Run `python dump.py 'Display name'` or `python dump.py Bundle.identifier`. Use `python dump.py -l` to list installed apps.

The agent explicitly imports `frida-objc-bridge`, and `dump.py` bundles it with `frida.Compiler` before injection. This is required because Frida 17 no longer includes the Objective-C bridge in scripts loaded through the Python API. No separate agent build command is needed. See the official [bridge documentation](https://frida.re/docs/bridges/) and [Frida 17 migration notes](https://frida.re/news/2025/05/17/frida-17-0-0-released/).

Dumped binaries are downloaded once, with up to four downloads running in parallel as the agent finishes each module. After all binary downloads complete, app directories are scanned and discovered resource files are immediately queued for up to four parallel downloads using the existing Paramiko SFTP dependency, skipping the exact paths of those binaries. During scanning, progress shows downloaded bytes and scanned entries; once the total size is known, it shows the overall percentage without resetting downloaded bytes. Directory structure, permissions and symbolic links are preserved before the decrypted binaries are placed into the IPA.

For SSH/SCP make sure you have your public key added to the target device's ~/.ssh/authorized_keys file.

The default SSH credentials are `root` / `alpine`. For a different account, use `./dump.py -u mobile -P 'YOUR_SSH_PASSWORD' Bundle.identifier`, or supply a private key with `-K`. Keys are tried first; the configured default password or explicit `-P` remains available as a fallback if key authentication fails.

`-H` also accepts aliases from `~/.ssh/config`, reading `HostName`, `Port`, `User` and `IdentityFile`. Command-line options override these settings. For example:

```sshconfig
Host iphone
    HostName 127.0.0.1
    Port 22
    User mobile
    IdentityFile ~/.ssh/frida_ios_dump_ed25519
```

Then run `./dump.py -H iphone Bundle.identifier`. The USB transport is provided by `dump.py`; this alias alone does not establish a USB connection for the system `ssh` command.

If device SSH listens on port 44, change the alias to `Port 44` or pass `-p 44`. To connect to an existing local TCP forwarding service instead, pass `--ssh-transport tcp`.

### Device selection and remote connections

USB remains the default. When multiple devices are connected, use `frida-ls-devices` to find the device ID, then select it explicitly:

```sh
python dump.py --device-id DEVICE_ID -l
python dump.py --device-id DEVICE_ID -H iphone Bundle.identifier
```

To connect without USB, the device's frida-server and SSH service must be reachable over the network. `--remote` selects the Frida endpoint (`HOST[:PORT]`, default port 27042); it is independent of the SSH endpoint:

```sh
python dump.py --remote 192.168.1.50:27042 -l
python dump.py --remote 192.168.1.50:27042 -u mobile Bundle.identifier
python dump.py --remote 192.168.1.50:27042 -H iphone-network Bundle.identifier
```

In remote mode, SSH defaults to TCP on the same host, port 22. Use `-H` for a different SSH host or alias and `-p` for its SSH port. For IPv6, use `--remote '[2001:db8::1]:27042'`. `--remote` and `--device-id` are mutually exclusive. A failed explicit connection never falls back to another device. Remote mode cannot be combined with `--ssh-transport usb`.

If frida-server only listens on the device's loopback interface, use an SSH tunnel (where `iphone-network` is your SSH alias for the device's network address):

```sh
ssh -N -L 127.0.0.1:27043:127.0.0.1:27042 iphone-network
# In another terminal:
python dump.py --remote 127.0.0.1:27043 -H iphone-network Bundle.identifier
```

Remote mode omits agent source maps to reduce the script upload size. USB mode retains source maps for debugging.

### Diagnostics and timeouts

```sh
python dump.py -v --agent-timeout 300 --transfer-timeout 180 -H iphone Bundle.identifier
```

`-v` / `--verbose` adds host stage diagnostics for device selection, SSH connection, app enumeration, spawn/attach, agent compilation/loading, downloads and packaging. Existing agent console output remains visible without `-v`. Credentials and private key contents are not included in the added diagnostics.

`--agent-timeout` is the wait for the next queued agent message (default 120 seconds). `--transfer-timeout` is the SCP/SFTP I/O timeout (default 60 seconds); it also bounds SFTP channel creation and initialization, and applies to resource scanning, file transfers and device temporary-file cleanup. Both accept positive seconds. These are not total export limits and do not change Frida's spawn/attach or agent compilation/loading timeouts. The last verbose stage helps identify where startup stopped.

```
./dump.py Aftenposten
Start the target app Aftenposten
Dumping Aftenposten to /var/folders/wn/9v1hs8ds6nv_xj7g95zxyl140000gn/T
start dump /var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/AftenpostenApp
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/AFNetworking.framework/AFNetworking
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/ATInternet_iOS_ObjC_SDK.framework/ATInternet_iOS_ObjC_SDK
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/SPTEventCollector.framework/SPTEventCollector
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/SPiDSDK.framework/SPiDSDK
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/libswiftCore.dylib
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/libswiftCoreData.dylib
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/libswiftCoreGraphics.dylib
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/libswiftCoreImage.dylib
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/libswiftCoreLocation.dylib
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/libswiftDarwin.dylib
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/libswiftDispatch.dylib
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/libswiftFoundation.dylib
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/libswiftObjectiveC.dylib
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/libswiftQuartzCore.dylib
start dump /private/var/containers/Bundle/Application/66423A80-0AFE-471C-BC9B-B571107D3C27/AftenpostenApp.app/Frameworks/libswiftUIKit.dylib
Generating Aftenposten.ipa

Done.
```

Congratulations!!! You've got a decrypted IPA file.

Drag to [MonkeyDev](https://github.com/AloneMonkey/MonkeyDev), Happy hacking!

## Support

Python 3.10+ and Frida 17.23.3 / 17.x. Python 2 is no longer supported.

Validated with Frida 17.23.3 on a jailbroken iPhone X running iOS 16.7.16: app enumeration, agent injection, module dumping, direct USB SSH/SCP download using the `mobile` user and SSH alias, and IPA packaging. The exported IPA passed ZIP integrity checks, and the main executable's encryption flag changed from `cryptid=1` to `cryptid=0` with different bytes in the encrypted region. Keep the device unlocked during startup and dumping.

Run the host regression tests with `python -m unittest discover -s tests -v`.

Run the agent regression tests with Node.js:

```sh
node tests/test_agent.js
node tests/test_agent_io.js
node tests/test_agent_scan.js
node tests/test_agent_temp.js
```


## Troubleshooting

| Problem | What to check |
| --- | --- |
| Cannot start or attach to the app | Unlock the device, open the target app manually, then retry. |
| `ObjC is not defined` or missing bridge | Run `npm install` in the repository and use this version of `dump.py`. |
| Frida connection or protocol error | Check that frida-server is running and the host and device Frida versions match. |
| SSH authentication failed | Check the SSH username, password or private key, and any settings in your SSH alias. |

If the problem persists, open an issue with the full error output, iOS version, and host/device Frida versions. Remove passwords and other private information from the logs.

## 认识更多同行 · 新识 HiNexus

除了分享代码，也欢迎来我做的社群网站 [新识 HiNexus](https://hinexus.me) 认识更多有趣的人。

免费创建你的职业个人主页，展示经历、专业方向、能提供的帮助和正在寻找的合作，让志同道合的人更容易找到你。无论你关注移动安全、逆向工程、独立开发还是 AI，都欢迎来介绍自己、交流想法、寻找合作伙伴。

**[来新识，认识一下 →](https://hinexus.me)**

## 关注公众号 · 添加个人微信

欢迎扫码关注我的微信公众号，或添加个人微信，一起交流技术与实践。

| 个人微信 · AloneMonkey | 微信公众号 |
| :---: | :---: |
| <img src="assets/wechat-personal.jpg" alt="AloneMonkey 个人微信二维码" width="280" /> | <img src="assets/wechat-official-account.png" alt="微信公众号关注二维码" width="280" /> |
