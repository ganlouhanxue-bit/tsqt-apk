[app]
# 应用名(手机桌面显示)
title = 天书奇谈登录器

# 包名: 小写字母数字下划线; domain + name 组合成唯一应用ID
package.name = tsqt
package.domain = org.example

# 源码目录(相对 buildozer.spec)
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,ttf

# 版本
version = 0.1.0

# 依赖: 只用 Kivy + 标准库(协议层零第三方依赖)
requirements = python3,kivy

# 手机方向
orientation = portrait

# 不显示安卓默认标题栏(界面自绘)
fullscreen = 0

# 权限: 联网(登录/进游戏都要)
android.permissions = INTERNET

# 安卓 API 级别
android.api = 33
android.minapi = 21
android.archs = arm64-v8a, armeabi-v7a

# 构建时自动接受 SDK 许可(GitHub Actions 上必须)
android.accept_sdk_license = True

# 启动页背景色(深色, 避免白屏闪烁)
presplash.color = #20242E

# 应用图标: 没准备就留空用默认
# icon.filename = %(source.dir)s/icon.png

[buildozer]
# 日志级别: 2=debug 1=info 0=error
log_level = 2

# 警告: 在 root 用户下构建会提示, 无害
warn_on_root = 1
