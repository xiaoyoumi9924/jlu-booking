<div align="center">
  <img src="assets/JLU_LOGO.png" alt="吉林大学校徽" width="92">
  <h1>JLU Booking</h1>
  <p>吉林大学体育场馆查询与预约桌面工具 · 网页前端本机版</p>
  <p>
    <a href="https://github.com/xiaoyoumi9924/jlu-booking/releases/latest"><img src="https://img.shields.io/github/v/release/xiaoyoumi9924/jlu-booking?display_name=tag&label=下载" alt="Release"></a>
    <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-555" alt="Windows macOS Linux">
    <img src="https://img.shields.io/badge/license-MIT-2EA44F" alt="MIT License">
  </p>
</div>

## 立即下载

| 系统 | 下载 | 打开方式 |
| --- | --- | --- |
| Windows（推荐） | [下载 Windows 安装包](https://github.com/xiaoyoumi9924/jlu-booking/releases/latest/download/JLU-Booking-Setup.exe) | 双击安装，自动创建桌面快捷方式 |
| Windows 便携版 | [下载 Windows ZIP](https://github.com/xiaoyoumi9924/jlu-booking/releases/latest/download/jlu-booking-Windows.zip) | 解压全部文件后打开 `JLU Booking.exe` |
| macOS | [下载 macOS 版](https://github.com/xiaoyoumi9924/jlu-booking/releases/latest/download/jlu-booking-macOS.zip) | 解压后打开 `JLU Booking.app` |
| Linux | [下载 Linux 版](https://github.com/xiaoyoumi9924/jlu-booking/releases/latest/download/jlu-booking-Linux.tar.gz) | 解压后运行 `JLU Booking` |

Windows 推荐直接下载 **JLU-Booking-Setup.exe**：不需要解压，安装完成后桌面会出现 `JLU Booking` 快捷方式，以后双击快捷方式即可打开。只有使用便携版 ZIP 时才需要先解压整个文件夹，不能只把 `JLU Booking.exe` 单独拿出来运行。

也可以进入 [Releases 页面](https://github.com/xiaoyoumi9924/jlu-booking/releases/latest) 查看最新版本和更新说明。

## 使用步骤

> 桌面版采用与网页版一致的前端界面，左侧只有 **场地查询**、**启动预约**、**个人中心** 三项，不包含“自动预约”模块。

### 1. 打开程序

Windows：双击安装包，按提示完成安装；桌面上的 `JLU Booking` 快捷方式会自动创建，双击即可打开。<br>
macOS：双击 `JLU Booking.app`<br>
Linux：运行 `JLU Booking`

<details>
<summary><strong>应用无法打开？展开查看备用启动方式</strong></summary>

> 以下方法适用于应用无法直接打开的情况。请先下载并解压项目的[源码 ZIP](https://github.com/xiaoyoumi9924/jlu-booking/archive/refs/heads/main.zip)。

#### 方法一：在 PyCharm 中运行

1. 安装 Python 3.10 或更高版本。
2. 使用 PyCharm 打开解压后的整个项目文件夹。
3. 在 PyCharm 中选择一个 Python 3.10 或更高版本的解释器。
4. 找到项目根目录中的 `start.py`，右键选择 **Run 'start'**。

#### 方法二：使用系统启动文件

进入解压后的项目文件夹，根据操作系统选择对应的启动文件：

| 操作系统 | 启动文件 | 使用方式 |
| :--- | :--- | :--- |
| Windows | `start.bat` | 双击运行 |
| macOS | `start.command` | 双击运行；首次被拦截时右键选择“打开” |
| Linux | `start.sh` | 在终端中运行 `bash start.sh` |

</details>

#### Windows 下载后打不开？

1. 请优先使用 `JLU-Booking-Setup.exe` 安装包，不要直接双击 ZIP 压缩包里的程序。
2. 如果 Windows 提示“已保护你的电脑”，点击 **更多信息 → 仍要运行**。
3. 如果仍然没有窗口，打开 `%LOCALAPPDATA%\jlu-booking\local` 文件夹，把 `local.log` 和 `startup-error.log` 发给开发者，日志里会记录具体原因。


### 2. 第一次粘贴场馆系统网址

在企业微信中登录学校体育场馆系统，在“选择体育馆”页面停下，点击右上角三个点，在浏览器中打开，然后复制地址栏的完整网址并粘贴进软件。网址格式为 `https://ss.jlu.edu.cn/easyserpApp/index.html#/branch?clubId=26&userId=你的身份值`，软件会自动从 `userId` 提取预约身份，无需自己寻找 Token。

每位用户第一次使用时需要粘贴自己的网址，在“个人中心”点击“更新 Token”可以粘贴新网址切换身份，也可以管理同行人学工号。程序会先检查网址格式，再向学校系统验证身份，成功后只保存提取出的身份值和同行人信息，整段网址不会保存。下次打开自动读取。它们不会写入仓库或打包进 `.exe`、`.app` 和 Linux 软件包；同一台电脑运行不同副本时可能复用此前保存的本机配置。


### 3. 配置并启动

点击左侧的 **启动预约**：

1. 在页面中选择场馆、项目和今天或明天，再设置首选场地与时间优先级。
2. 确认同行人学工号；需要真实提交时再选择“真实预约”。
3. 点击 **立即启动预约**。任务会立即查询并持续尝试，直到成功、遇到明确异常或手动停止；运行进度显示在任务详情页。

桌面即时任务按所选时间段与场地优先级尝试；“仅扫描”不会提交预约。“真实预约”会向学校系统提交，请确认模式后再启动。每天 07:27 的预热、高速和 07:33 截止规则只适用于下方的本地定时运行方式。详细规则见 [完整使用说明](docs/usage.md)。

预约身份在保存或替换前会做只读联网验证；无效、过期、账号受限和网络异常都不会覆盖原有值。
命令行用户可运行 `jlu-booking-token verify` 验证当前 Token，运行
`jlu-booking-status` 查看最近一次自动任务结果。


### 4. 每天定时运行（Web 版 / 命令行，可选）

桌面版不内置“自动预约”模块。如果想要电脑每天早上 **07:27 自动启动预约任务**，可以使用 Web 版或命令行，并按照自己的操作系统查看对应教程：

- [Windows 自动运行教程](docs/automation-windows.md)
- [macOS 自动运行教程](docs/automation-macos.md)
- [Linux 自动运行教程](docs/automation-linux.md)

如果认为教程比较复杂，可以把文件交给 DeepSeek 或 ChatGPT，使用 AI 辅助完成部署。

## 目前支持：

| 场馆 | 项目 |
| --- | --- |
| 前卫体育馆 | 羽毛球、乒乓球、匹克球 |
| 宋治平体育馆 | 排球、乒乓球、网球 |

## 文档

- [完整使用说明](docs/usage.md)：源码安装、Token 管理、CLI、定时任务、配置路径和排错
- [Linux 安装与使用](docs/linux.md)
- [贡献指南](CONTRIBUTING.md)
- [安全说明](SECURITY.md)

## 可选 Web 版

项目同时提供面向小规模用户的可选服务器网页。桌面版现在复用同一套网页前端，作为本机单用户运行，只保留场地查询、启动预约和个人中心三项；Web 版额外提供登录注册、自动预约和管理后台。公开注册的用户分别绑定自己的 JLU Token 和同行人，登录后可实时查询场地、明确确认手动预约，或使用“启动预约”立即开始持续尝试（成功、明确异常或手动停止后结束）；“自动预约”则设置每天 07:27 启动的任务。管理员登录后进入独立管理后台。网页复用原有预约核心；命令行和本地定时方式继续保留。

Web 版需要独立后端、SQLite、HTTPS 反向代理和调度服务。部署前请阅读 [Web 版服务器部署与运维](docs/web-deployment.md)，并坚持“Mac 本地修改和测试 → 提交并推送 GitHub → 服务器只拉取已提交版本”的更新流程。

## License

[MIT](LICENSE)
