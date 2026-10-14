---
name: frida-ios-dump
description: 自动安装和配置 AloneMonkey/frida-ios-dump，连接 USB 越狱 iPhone，枚举应用并导出解密 IPA。适用于用户要求搭建 iOS 砸壳环境、使用 SSH 别名或密钥连接设备、拉取指定应用的场景。
---

# frida-ios-dump 自动化工作流

你负责实际执行仓库获取、依赖安装、连接检查和指定应用导出，直到交付经过验证的 IPA，或说明具体阻碍。用户只要求安装时，完成安装和连接验证即可。此文件可独立通过链接读取，无需预先安装本仓库或其他 skill。

仓库：<https://github.com/AloneMonkey/frida-ios-dump>

## 1. 确定环境与任务

- 优先复用用户已提供的工作目录、设备、SSH 别名、账号、密钥和应用名称。只询问仍缺少的信息；可以先获取仓库、检查运行时和 SSH 配置，不要为可自动发现的信息中断工作。
- 主机需要 Python 3.10+、Node.js/npm（或 pnpm）、Git 和 `zip`。优先使用现有运行时和项目虚拟环境；缺少工具时用适合当前系统的包管理器补齐，不执行来历不明的远程安装脚本。
- 设备需要已越狱、运行 frida-server 和 SSH 服务，通过 USB 连接并信任主机。解锁、确认信任等物理操作需要用户完成；本工作流不负责越狱。
- 本仓库要求 Frida `>=17.23.3,<18`，主机与设备应使用相同版本。不要把“更新依赖”解释为无条件升级设备服务或切换 Frida 主版本。
- 导出前明确目标应用，优先使用枚举得到的 Bundle ID。未指定目标时先列出应用供用户选择。

## 2. 获取仓库

新目录中执行（目录可按用户需求调整）：

```sh
git clone https://github.com/AloneMonkey/frida-ios-dump.git
cd frida-ios-dump
```

用户提供了分支、标签或提交时使用该版本。通过某个分支或提交的链接读取本文件时，优先获取同一版本的仓库。没有 Git 时可下载对应版本的 GitHub 源码 ZIP 并用 Python `zipfile` 解压，不必增加 `tar` 依赖。

已有仓库时先检查 remote、当前分支和 `git status --short`。复用本地修改；仅在适合更新且工作区干净时快进拉取，不执行 `reset --hard`、`clean` 或覆盖用户文件。

确认该版本同时包含 `SKILL.md`、`requirements.txt`、`package.json`、`dump.py`、`dump.js`；`package.json` 应包含 `frida-objc-bridge`。缺少新版本文件时报告版本不符，不用旧版脚本冒充完成安装。

## 3. 先读取设备 Frida 版本

先确定设备实际运行的 frida-server 版本，再决定主机 Frida 的安装版本。不能先装主机最新版、再默认要求设备升级。

1. 优先通过已有可用的 SSH 连接查询。先确定运行服务的可执行文件路径，再执行该文件的 `--version`。可用 `command -v frida-server`、进程信息和服务配置定位；常见路径为 `/var/jb/usr/sbin/frida-server`、`/usr/sbin/frida-server`。有多个安装副本时，以运行服务使用的路径为准，不能只取 PATH 中的第一个文件。
2. 只有 USB 连接时，先参考第 5 节的账号和别名规则。USB 别名不能直接用于系统 `ssh`。优先复用主机已有 Frida，通过设备 USB SSH 通道执行查询；可以按 `dump.py` 中 `USBSSHForwarder` 的实现建立 Paramiko 连接，不需要 attach 应用或枚举应用。
3. 如果主机尚无 Frida，且没有其他可用 SSH 通道，可以先在项目虚拟环境安装用于 USB 查询的临时依赖：`frida>=17.23.3,<18` 和 `paramiko>=3`。此时安装的 Frida 仅用于引导查询，不代表已匹配设备。读取版本后必须按下一节重新锁定。不要为查询版本新增 2222 转发流程。
4. 如果查询失败，说明具体连接或认证问题；仍无法自动获取时，请用户提供设备终端执行实际 `frida-server --version` 的结果。不能把主机 `frida.__version__` 或应用枚举成功当作设备的准确版本。

将查询结果保存为 `DEVICE_FRIDA_VERSION`。它必须是版本号，不能将未校验的远端输出直接拼接进 shell 命令。与仓库 `requirements.txt` 的支持范围比较：满足范围则安装完全相同版本；不满足时明确说明当前仓库无法直接匹配，结合用户要求处理兼容性或设备升级，不擅自修改设备服务，也不绕过依赖约束声称兼容。

## 4. 按设备版本安装依赖

以下命令在仓库根目录执行。先确认 `python3` 版本满足要求；如不满足，选择已有的新版 Python 创建 `.venv`，已有可用虚拟环境则复用：

```sh
python3 -m venv .venv
```

将上一节读到并校验过的设备版本写入项目虚拟环境的约束文件。以下 `DEVICE_FRIDA_VERSION` 是待替换值，必须替换为实际版本号后执行：

```text
# .venv/frida-device-constraints.txt
frida==DEVICE_FRIDA_VERSION
```

在同一次依赖求解中安装 requirements 并应用约束，避免 `frida-tools` 或后续更新将 Frida 升到其他版本：

```sh
./.venv/bin/python -m pip install --upgrade -r requirements.txt -c .venv/frida-device-constraints.txt
./.venv/bin/python -m pip check
npm install
./.venv/bin/python dump.py --help
```

如果 pip 报冲突，先检查设备版本是否在支持范围内，再检查 `frida-tools` 的依赖要求；让 pip 在约束下选择兼容版本，不使用 `--no-deps` 强行安装。后续安装或更新 Python 依赖时继续使用这个约束文件。

已安装 pnpm 时可用 `pnpm install --frozen-lockfile` 替代 `npm install`。仓库没有 npm 的 lockfile，不使用 `npm ci`。后续命令均使用虚拟环境中的 Python，无需依赖跨工具调用保留 shell 激活状态。

Frida 17 的 Objective-C bridge 由 npm/pnpm 安装，`dump.py` 会自动编译 agent，不需要手动运行 `frida-compile`。

核对主机实际版本与 `DEVICE_FRIDA_VERSION` 完全一致后，再枚举应用：

```sh
./.venv/bin/python -c 'import frida; print(frida.__version__)'
./.venv/bin/python dump.py -l
```

枚举应用本身无需 SSH 凭据。记录设备与主机两端的版本及设备版本查询方式，便于后续排查。

## 5. 配置 SSH

### 直接通过 USB

默认连接设备 SSH 22 端口，账号密码为 `root` / `alpine`。不需要 `iproxy` 或本机 2222 转发。

```sh
./.venv/bin/python dump.py 'BUNDLE_ID'
```

账号不同时使用 `-u`，密钥使用 `-K`；密码使用 `-P`。不需要用户提供凭据时不要索取密码。确需密码时避免将其写入仓库、示例文档或最终报告；命令行 `-P` 会使密码可能出现在进程参数中，优先复用已有密钥。

### 复用 SSH 别名

先读取用户指定别名的配置。`-H` 支持 `~/.ssh/config` 中的 `HostName`、`Port`、`User`、`IdentityFile`；命令行选项覆盖配置。不能假定支持系统 OpenSSH 的全部选项，例如跳板机或任意 `ProxyCommand`。

下面是 USB 别名示例，仅在用户需要创建别名时合并到已有配置；用户名和密钥路径按实际情况选择，密钥不存在时省略 `IdentityFile`：

```sshconfig
Host iphone
    HostName 127.0.0.1
    Port 22
    User mobile
    IdentityFile ~/.ssh/frida_ios_dump_ed25519
```

```sh
./.venv/bin/python dump.py -H iphone 'BUNDLE_ID'
```

localhost 别名且端口为 22 时使用 USB。显式远程主机名/IP 使用 TCP；不指定主机时 `-p` 设置设备 SSH 端口。USB 通道由 `dump.py` 提供，上面的别名本身不能让系统 `ssh iphone` 或 `ssh-copy-id iphone` 自动走 USB。密钥认证失败时仍可回退到默认密码或显式 `-P`。

### 可选：用户要求免密登录时

复用已有密钥，或创建专用于该设备的密钥。通过已经认证的 SSH/SFTP 连接，将公钥去重追加到对应远端用户的 `~/.ssh/authorized_keys`，保留已有内容，目录权限 700、文件权限 600。私钥留在主机，不能上传。

USB 场景可读取本仓库 `dump.py` 的 `resolve_ssh_options` 和 `connect_ssh`，用 `frida.get_usb_device(timeout=10)` 获取设备，复用 `connect_ssh(..., use_usb=True)` 建立 Paramiko 连接，再执行配置或使用 SFTP。按当前函数签名调用，结束时关闭 SSH 客户端和返回的 USB forwarder。不为配置密钥引入本机端口转发依赖。

配置后用 `password=None`、`allow_agent=False`、`look_for_keys=False` 和指定私钥建立一次独立连接，确认成功来自该密钥，再报告免密可用。不要关闭设备密码登录或替换其他公钥。

## 6. 导出应用

从枚举结果确认 Bundle ID，确认设备保持解锁。选择不存在的输出路径，避免重复使用已有 ZIP 导致旧条目残留。例如：

```sh
./.venv/bin/python dump.py -H iphone -o './exported-app.ipa' 'BUNDLE_ID'
```

没有别名时省略 `-H iphone`，其他 SSH 参数按已验证的配置添加。

同一主机一次只运行一个导出进程：当前实现共用临时目录中的 `Payload`。脚本内部已经并行下载二进制和资源，不要为了加速再启动多个独立导出。

`start dump` 表示设备正在生成解密文件；随后才是传输。已下载的二进制按精确相对路径从资源传输中跳过，只传一次。资源目录边扫描边提交下载，扫描期间显示字节和条目数，总量确定后显示百分比，不额外运行一次全目录扫描或 tar 打包。

运行期间观察输出和进程状态，持续向用户说明当前阶段。需要中断时发送 Ctrl+C，等待进程退出后再重试，避免遗留并行传输。

## 7. 验证与交付

退出码为 0 后，确认输出文件存在且非空。用 Python 标准库检查 ZIP 完整性，并读取主应用信息，例如（替换路径）：

```sh
./.venv/bin/python - './exported-app.ipa' <<'PY'
import plistlib
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    bad = archive.testzip()
    if bad is not None:
        raise RuntimeError(f'Corrupt ZIP entry: {bad}')
    infos = [name for name in archive.namelist()
             if name.startswith('Payload/') and name.endswith('.app/Info.plist')
             and name.count('/') == 2]
    if len(infos) != 1:
        raise RuntimeError(f'Expected one main app, found {len(infos)}')
    info = plistlib.loads(archive.read(infos[0]))
    executable = infos[0].rsplit('/', 1)[0] + '/' + info['CFBundleExecutable']
    if archive.getinfo(executable).file_size == 0:
        raise RuntimeError('Empty executable')
    print(info.get('CFBundleIdentifier'), executable)
PY
```

需要确认砸壳结果时，解析主程序 Mach-O 的 `LC_ENCRYPTION_INFO` / `LC_ENCRYPTION_INFO_64`，检查 `cryptid=0`。原本无加密标志的文件不能仅凭此证明发生了解密；严格验证可与设备原文件的加密区域比较。将 ZIP 完整性和解密验证的结论分别说明，不把“命令成功”当作全部验证完成。

交付仓库路径、实际 Frida 版本、目标 Bundle ID、IPA 绝对路径、大小和完成的验证。仅安装任务则报告环境和连接验证结果。不要自动重签名或安装导出的 IPA。

## 常见阻碍

| 现象 | 处理 |
| --- | --- |
| 找不到 USB 设备 | 检查线缆、USB 连接、信任状态及设备 Frida 服务；需要用户确认信任时说明具体操作。 |
| 设备锁定或无法启动应用 | 请用户解锁并手动打开目标应用，保持亮屏后再试。 |
| SSH 认证失败 | 检查实际账号、别名和密钥；默认 root/alpine 并非所有越狱环境通用。请求缺少的认证信息，不反复猜密码。 |
| 缺少 ObjC / bridge | 在仓库根目录安装 npm 依赖，确认使用本版本 `dump.py` 和虚拟环境。 |
| 协议或版本不匹配 | 检查主机和设备实际 Frida 版本并对齐。 |
| 长时间 0 B 或异常脱离 | 记录最后日志与进程状态，排查设备锁定、SSH、应用退出和版本；处理原因后重试，避免无限重试或默认重启设备。 |

如果必须修改下载代码，保持 Frida 消息回调只入队，传输在回调之外执行，否则会阻塞 USB 事件循环。修改后运行 `./.venv/bin/python -m unittest discover -s tests -v`，再按用户任务验证真机。
