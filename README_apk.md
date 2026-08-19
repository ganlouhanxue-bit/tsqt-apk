# 安卓版构建说明（计划 B：Kivy APK）

项目结构：

```
android/
├── main.py                  # Kivy 界面层（安卓入口）
├── protocol.py              # 协议核心（从 FFFFF.py 原样复制，零第三方依赖）
├── buildozer.spec           # 打包配置
├── fonts/simhei.ttf         # 中文字体（安卓默认字体不含中文，必须带）
└── .github/workflows/build-apk.yml   # GitHub Actions 云构建
```

## 方法一：GitHub Actions 云构建（推荐，Windows 用户首选）

本机是 Windows，Buildozer 只支持 Linux，所以用 GitHub 免费云服务器构建，**不用装任何安卓工具**。

1. **建仓库**：github.com 新建一个仓库（Public 或 Private 都行）
2. **推送代码**：把 `android` 文件夹里的**内容**作为仓库根目录推送（`main.py`、`protocol.py`、`buildozer.spec`、`fonts/`、`.github/` 都在根目录）
3. **触发构建**：仓库页面 → **Actions** → 左侧 **Build APK** → **Run workflow** → 绿色按钮
4. **等 15~30 分钟**（首次构建要下载 Android SDK/NDK，之后增量构建快很多）
5. **下载 APK**：构建成功后 Actions 页面顶部会出现 **tsqt-apk** artifact，点开下载 `.apk` 文件
6. **装手机**：APK 传到手机 → 点开安装 → 提示「未知来源」时允许即可

> 构建失败的话，点进失败的 job 看红色日志；常见原因：网络下载 SDK 超时 → 重跑一次 workflow 即可。

## 方法二：本机 WSL2 构建（可选）

装了 WSL2 + Ubuntu 的话也可以本机构建：

```bash
# 在 WSL Ubuntu 里
sudo apt update
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip autoconf libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo5 cmake libffi-dev libssl-dev
pip install --user buildozer
cd /mnt/c/Users/Administrator/WorkBuddy/天书奇谈/android
python3 -m buildozer android debug
# APK 输出在 bin/
```

## 手机使用

- 打开 App：填区号/账号/密码 → 登录 → 选线路、选角色 → 进入游戏 → 状态变「在线中」，自动保活（心跳 10 秒 + 移动包 30 秒）
- 「退出游戏」任何状态下都能点：断开连接、清会话、回到离线中
- 进角色成功会自动把配置存到 App 私有目录（下次启动自动回填）

## 已知限制

- **锁屏/切后台可能被系统杀**：安卓后台限制，保活挂机需要「前台服务+常驻通知」，是后续迭代项；当前版本建议挂机时保持亮屏
- 密码明文存在 App 私有目录（`/data/data/org.example.tsqt/files/tsqt_config.json`），仅本机可读
- 仅竖屏；没做应用图标（用默认安卓图标）
