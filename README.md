# frida-ios-dump
pull decrypted ipa from jailbreak device

### Usage

## 1. install [frida](http://www.frida.re/) on device and mac

## 2. iproxy 2222 22

## 3. ./dump.py 微信

```
➜  frida-ios-dump ./dump.py 微信
open target app......
start dump target app......
start dump /var/containers/Bundle/Application/6665AA28-68CC-4845-8610-7010E96061C6/WeChat.app/WeChat
WeChat                                        100%   68MB  11.4MB/s   00:05
start dump /private/var/containers/Bundle/Application/6665AA28-68CC-4845-8610-7010E96061C6/WeChat.app/Frameworks/WCDB.framework/WCDB
WCDB                                          100% 2555KB  11.0MB/s   00:00
start dump /private/var/containers/Bundle/Application/6665AA28-68CC-4845-8610-7010E96061C6/WeChat.app/Frameworks/MMCommon.framework/MMCommon
MMCommon                                      100%  979KB  10.6MB/s   00:00
start dump /private/var/containers/Bundle/Application/6665AA28-68CC-4845-8610-7010E96061C6/WeChat.app/Frameworks/MultiMedia.framework/MultiMedia
MultiMedia                                    100% 6801KB  11.1MB/s   00:00
start dump /private/var/containers/Bundle/Application/6665AA28-68CC-4845-8610-7010E96061C6/WeChat.app/Frameworks/mars.framework/mars
mars                                          100% 7462KB  11.1MB/s   00:00
AppIcon60x60@2x.png                           100% 2253   230.9KB/s   00:00
AppIcon60x60@3x.png                           100% 4334   834.8KB/s   00:00
AppIcon76x76@2x~ipad.png                      100% 2659   620.6KB/s   00:00
AppIcon76x76~ipad.png                         100% 1523   358.0KB/s   00:00
AppIcon83.5x83.5@2x~ipad.png                  100% 2725   568.9KB/s   00:00
Assets.car                                    100%   10MB  11.1MB/s   00:00
.......
AppIntentVocabulary.plist                     100%  197    52.9KB/s   00:00
AppIntentVocabulary.plist                     100%  167    43.9KB/s   00:00
AppIntentVocabulary.plist                     100%  187    50.2KB/s   00:00
InfoPlist.strings                             100% 1720   416.4KB/s   00:00
TipsPressTalk@2x.png                          100%   14KB   2.2MB/s   00:00
mm.strings                                    100%  404KB  10.2MB/s   00:00
network_setting.html                          100% 1695   450.4KB/s   00:00
InfoPlist.strings                             100% 1822   454.1KB/s   00:00
mm.strings                                    100%  409KB  10.2MB/s   00:00
network_setting.html                          100% 1819   477.5KB/s   00:00
InfoPlist.strings                             100% 1814   466.8KB/s   00:00
mm.strings                                    100%  409KB  10.3MB/s   00:00
network_setting.html                          100% 1819   404.9KB/s   00:00
```

congratulations!!! You've got a decrypted ipa file.

Drag to [MonkeyDev](https://github.com/AloneMonkey/MonkeyDev), Happy hacking!