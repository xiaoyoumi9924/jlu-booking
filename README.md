<div align="center">
  <img src="assets/JLU_LOGO.png" alt="吉林大学校徽" width="92">
  <h1>JLU Booking</h1>
  <p>吉林大学体育场馆查询与自动预约桌面工具</p>
  <p>
    <a href="https://github.com/xiaoyoumi9924/jlu-booking/releases/latest"><img src="https://img.shields.io/github/v/release/xiaoyoumi9924/jlu-booking?display_name=tag&label=下载" alt="Release"></a>
    <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-555" alt="Windows macOS Linux">
    <img src="https://img.shields.io/badge/license-MIT-2EA44F" alt="MIT License">
  </p>
</div>

## 立即下载

| 系统 | 下载 | 解压后打开 |
| --- | --- | --- |
| Windows | [下载 Windows 版](https://github.com/xiaoyoumi9924/jlu-booking/releases/latest/download/jlu-booking-Windows.zip) | `JLU Booking.exe` |
| macOS | [下载 macOS 版](https://github.com/xiaoyoumi9924/jlu-booking/releases/latest/download/jlu-booking-macOS.zip) | `JLU Booking.app` |
| Linux | [下载 Linux 版](https://github.com/xiaoyoumi9924/jlu-booking/releases/latest/download/jlu-booking-Linux.tar.gz) | `JLU Booking` |

也可以进入 [Releases 页面](https://github.com/xiaoyoumi9924/jlu-booking/releases/latest) 查看最新版本和更新说明。

> 点击 GitHub 绿色“代码”按钮下载到的 `jlu-booking-main.zip` 是 **main 分支的源码包**，名称正常，但它不是打包软件。普通用户请使用上表中的 Releases 下载链接。

## 使用步骤

### 1. 打开程序

Windows：双击 `JLU Booking.exe`<br>
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

### 2. 第一次粘贴 Token

在企业微信中登录学校体育场馆系统，然后在选择体育馆那个页面停下，点击右上方三个点，然后在浏览器中打开，复制一下网址，其实token就在网址中能体现出来。

每位用户第一次使用时需要输入自己的 Token，并在“自动预约”里填写同行人学工号。验证成功并保存配置后，程序会在当前电脑的用户配置目录中记住这两项，下次打开自动读取。它们不会写入仓库或打包进 `.exe`、`.app` 和 Linux 软件包；同一台电脑运行不同副本时可能复用此前保存的本机配置。


### 3. 配置并启动

先在左侧中选择场馆，再点击左侧选项卡中的 **自动预约**：

1. 选择对应项目、日期、首选场地和时间优先级。
2. 填写同行人学工号；
3. 需要真实提交时再选择“真实预约”。
4. 点击 **保存并启动**；



### 4. 每天定时运行（可选）

先在软件中保存一下预约配置，如果想要电脑每天早上 **07:28 自动启动预约任务**，可以按照自己的操作系统查看对应教程：

- [Windows 自动运行教程](docs/automation-windows.md)
- [macOS 自动运行教程](docs/automation-macos.md)
- [Linux 自动运行教程](docs/automation-linux.md)

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

## License

[MIT](LICENSE)
