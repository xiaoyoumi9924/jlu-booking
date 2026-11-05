<div align="center">
  <img src="assets/JLU_LOGO.png" alt="吉林大学校徽" width="92">
  <h1>JLU Booking</h1>
  <p>吉林大学体育场馆预约助手</p>
  <p>
    <a href="https://github.com/xiaoyoumi9924/jlu-booking/releases/latest"><img src="https://img.shields.io/github/v/release/xiaoyoumi9924/jlu-booking?display_name=tag&label=下载" alt="Release"></a>
    <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-555" alt="Windows macOS Linux">
    <img src="https://img.shields.io/badge/license-MIT-2EA44F" alt="MIT License">
  </p>
</div>

## 立即下载

| 系统 | 下载 |
| --- | --- |
| Windows | [下载 Windows 版](https://github.com/xiaoyoumi9924/jlu-booking/releases/latest/download/jlu-booking-Windows.zip) |
| macOS | [下载 macOS 版](https://github.com/xiaoyoumi9924/jlu-booking/releases/latest/download/jlu-booking-macOS.zip) |
| Linux | [下载 Linux 版](https://github.com/xiaoyoumi9924/jlu-booking/releases/latest/download/jlu-booking-Linux.tar.gz) |


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

### 2. 第一次粘贴场馆系统网址

在企业微信中登录学校体育场馆系统，在“选择体育馆”页面停下，点击右上角三个点，在浏览器中打开，然后复制地址栏的完整网址并粘贴进软件。网址格式为 `https://ss.jlu.edu.cn/easyserpApp/index.html#/branch?clubId=26&userId=你的身份值`，身份值就是你的token。


### 3. 配置并启动

点击左侧的 **启动预约**：

1. 在页面中选择场馆、项目和今天或明天，再设置首选场地与时间优先级。
2. 确认同行人学工号；需要真实提交时再选择“真实预约”。
3. 点击 **立即启动预约**。任务会立即查询并持续尝试，直到成功、遇到明确异常或手动停止；运行进度显示在任务详情页。

桌面即时任务按所选时间段与场地优先级尝试；“仅扫描”不会提交预约。“真实预约”会向学校系统提交，请确认模式后再启动。每天 07:27 的预热、高速和 07:33 截止规则只适用于下方的本地定时运行方式。详细规则见 [完整使用说明](docs/usage.md)。



### 4. 每天定时运行（可选）

桌面版不内置“自动预约”模块。如果想要电脑每天早上 **07:27 自动启动预约任务**，可以在本地部署 Web 版，并按照自己的操作系统查看对应教程：

- [Windows 自动运行教程](docs/automation-windows.md)
- [macOS 自动运行教程](docs/automation-macos.md)
- [Linux 自动运行教程](docs/automation-linux.md)

部署前请阅读 [Web 版服务器部署与运维](docs/web-deployment.md)。建议把文件交给 DeepSeek 或 ChatGPT，使用 AI 辅助完成部署。

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

## 网站（测试版）
目前仅支持15人，且为邀请制，需邀请码才能进行注册，注册前请联系管理员fxyf00@qq.com
[https://booking.meiyh9924.xyz/](https://booking.meiyh9924.xyz/)



## License

[MIT](LICENSE)
