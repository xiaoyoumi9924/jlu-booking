# JLU Booking Linux 安装与使用指南

这份说明面向第一次在 Linux 上使用本项目的用户。Ubuntu、Debian、Linux Mint 可以直接按照第一套命令操作；Fedora、Arch Linux 的依赖命令在后面单独列出。

桌面 Linux 已安装 Python 3.10+、Tkinter 和 venv 时，可以先给仓库根目录的 `start.sh` 执行权限后双击，或在终端运行 `./start.sh`。它会自动创建项目虚拟环境、安装依赖并打开 GUI；下面的步骤用于系统依赖缺失、服务器部署或需要手动控制安装时。

## 1. 先确认自己位于正确的项目目录

打开终端，进入解压或克隆后的项目目录。例如：

```bash
cd ~/jlu-booking
```

有些 ZIP 解压后会多套一层同名目录。如果执行 `ls` 看不到 `pyproject.toml`，继续进入实际的项目目录：

```bash
ls
cd jlu-booking
ls
```

看到下面这些文件后再继续：

```text
pyproject.toml  README.md  jlu_booking  config
```

也可以直接检查：

```bash
test -f pyproject.toml && echo "当前目录正确" || echo "当前目录不正确，请继续查找 pyproject.toml"
```

后续所有命令默认都在这个目录执行。

## 2. 安装 Linux 系统依赖

Python 项目依赖可以安装进虚拟环境，但 `venv` 和 Tkinter 在很多 Linux 发行版中属于系统软件包，不能只靠 `pip install` 解决。

### Ubuntu、Debian、Linux Mint

需要 GUI 时运行：

```bash
sudo apt update
sudo apt install python3 python3-venv python3-tk
```

如果只在无桌面服务器上运行自动预约，不启动 GUI，可以使用：

```bash
sudo apt update
sudo apt install python3 python3-venv
```

可选安装 Noto 中文字体，让不同桌面环境中的中文显示更一致：

```bash
sudo apt install fonts-noto-cjk
```

### Fedora、Rocky Linux、AlmaLinux

```bash
sudo dnf install python3 python3-tkinter
```

可选安装中文字体：

```bash
sudo dnf install google-noto-sans-cjk-fonts
```

### Arch Linux、Manjaro

```bash
sudo pacman -S python tk
```

可选安装中文字体：

```bash
sudo pacman -S noto-fonts-cjk
```

输入 `sudo` 密码时，终端不会显示星号或其他字符。正常输入密码并按 Enter 即可。

## 3. 检查 Python 和 Tkinter

Linux 通常提供的是 `python3`，没有全局 `python` 命令并不代表 Python 未安装：

```bash
python3 --version
```

版本需要是 Python 3.10 或更高。如果低于 3.10，请通过当前发行版的软件包管理器安装受支持的 Python 版本，然后使用对应的 `python3` 创建虚拟环境。

需要使用 GUI 时，继续检查 Tkinter：

```bash
python3 -m tkinter
```

成功时会弹出一个 Tk 测试窗口。关闭测试窗口即可继续。如果出现 `No module named tkinter`，说明第 2 步的 Tkinter 系统包尚未安装成功。

## 4. 创建并激活虚拟环境

创建虚拟环境时使用系统提供的 `python3`：

```bash
python3 -m venv .venv
```

根据 Shell 激活虚拟环境：

| Shell | 激活命令 |
| --- | --- |
| Bash / Zsh | `source .venv/bin/activate` |
| Fish | `source .venv/bin/activate.fish` |
| Csh / Tcsh | `source .venv/bin/activate.csh` |

成功后，命令提示符前通常会出现 `(.venv)`。现在检查解释器路径：

```bash
which python
python --version
```

`which python` 应指向当前项目的 `.venv/bin/python`。从这一步开始，文档中的 `python` 和 `pip` 都指虚拟环境里的版本。

每次关闭并重新打开终端后，都需要重新执行：

```bash
cd ~/jlu-booking
source .venv/bin/activate
```

如果你的项目实际多套了一层目录，应把第一行改成真实路径。

## 5. 安装项目并检查命令

确认终端开头有 `(.venv)`，然后运行：

```bash
python -m pip install -e .
```

安装过程需要访问 Python 软件包源。完成后检查：

```bash
jlu-booking-auto --help
```

看到中文参数说明就表示安装成功。还可以检查三个入口：

```bash
jlu-booking-auto --show-paths
jlu-booking-auto --show-config
python -m jlu_booking.auto --help
```

这些检查命令不需要 Token，也不会提交预约。

## 6. 启动 GUI

在有图形桌面的 Linux 中运行：

```bash
jlu-booking
```

如果短命令找不到，可以使用：

```bash
python -m jlu_booking
```

第一次查询时 GUI 会弹出隐藏输入框，请粘贴自己的 Token。程序会先执行一次只读查询验证，通过后才保存到当前 Linux 用户的配置目录；无效时会提示重新输入。以后重新打开 GUI 或运行自动任务都会直接复用。

如果你是通过纯 SSH 登录服务器，或者系统没有 GNOME、KDE 等图形桌面，请不要启动 GUI。这种环境只使用自动预约命令即可。

## 7. 在终端运行自动任务

建议先确认配置：

```bash
jlu-booking-auto --show-config
```

全新安装默认是“前卫体育馆 / 羽毛球 / 未配置同行人 / 仅扫描”。如果直接运行 `jlu-booking-auto`，程序会在联网前提示先打开 GUI 左侧的“自动预约”完成设置；无桌面 Linux 可以编辑配置文件。只有明确使用下面的 `--dry-run` 时，才允许在未填写同行人的情况下测试查询。

第一次测试使用仅扫描模式：

```bash
jlu-booking-auto --dry-run
```

如果 GUI 已经保存过 Token，自动任务会直接使用，不再询问。否则终端会出现下面的提示，此时粘贴 Token，再按 Enter：

```text
请输入 JLU_BOOKING_TOKEN（输入不会回显）：
```

输入过程中屏幕不会出现字符或星号，这是密码输入的正常行为。如果直接按 Enter 提交空内容，程序会提示没有找到 Token 并退出。非空 Token 会和 GUI 输入一样保存到当前用户目录，下一次运行不再询问。

按 `Ctrl+C` 可以停止扫描。确认配置和仅扫描模式正常后，才考虑运行：

```bash
jlu-booking-auto --real-booking
```

该命令可能产生真实预约，运行前请再次核对场馆、项目、日期、同行人和时间优先级。

## 8. 保存与修改 Token

查看当前保存状态和具体路径（不会显示 Token 内容）：

```bash
jlu-booking-token status
```

通过隐藏输入永久保存到当前 Linux 用户目录：

```bash
jlu-booking-token set
```

Token 失效或需要换账号时，再执行一次 `jlu-booking-token set` 即可覆盖。修改后应重启已经运行的 GUI 或自动任务。清除保存值使用：

```bash
jlu-booking-token clear
```

Token 的读取优先级为：`JLU_BOOKING_TOKEN` 环境变量 > 当前用户保存的 Token > 交互式隐藏输入。如果只想在当前终端临时覆盖，可以运行：

```bash
read -rsp "请输入 JLU_BOOKING_TOKEN：" JLU_BOOKING_TOKEN
echo
export JLU_BOOKING_TOKEN
```

这样 Token 不会直接出现在 Shell 历史记录中。使用结束后清除环境变量：

```bash
unset JLU_BOOKING_TOKEN
```

不要把真实 Token 写进项目、配置 JSON、README、Issue 或截图。保存文件为本机明文凭据，程序会尽量将目录权限设为 `0700`、文件权限设为 `0600`；不要共享或上传它。

## 9. 配置、日志和状态目录

查看当前 Linux 机器实际使用的目录：

```bash
jlu-booking-auto --show-paths
```

全新安装通常使用：

```text
配置：~/.config/jlu-booking/auto_booking.json
Token：~/.config/jlu-booking/token
状态：~/.local/state/jlu-booking/
```

如果项目中已经存在旧版 `config/auto_booking.json` 或 `runtime/`，程序会继续兼容旧路径，因此应以 `--show-paths` 的实际输出为准。

`--show-paths` 输出中的 `event_log` 记录阶段切换、目标锁定和预约结果；`request_timing_log` 是单独的接口耗时日志。耗时日志不保存 Token、学号或完整请求参数。

## 10. Linux 常见错误

### `python: command not found`

在虚拟环境外请使用：

```bash
python3 --version
python3 -m venv .venv
```

激活 `.venv` 后才使用 `python`。不需要为了本项目专门安装 `python-is-python3`。

### 创建 `.venv` 时提示文件不存在或 `ensurepip` 不可用

Ubuntu/Debian 先安装：

```bash
sudo apt update
sudo apt install python3-venv
```

如果上一次失败留下了不完整的 `.venv`，可以在确认当前目录正确后重新创建：

```bash
python3 -m venv --clear .venv
source .venv/bin/activate
```

### `No module named 'tkinter'`

Ubuntu/Debian：

```bash
sudo apt install python3-tk
```

Fedora 系：

```bash
sudo dnf install python3-tkinter
```

Arch 系：

```bash
sudo pacman -S tk
```

安装完成后重新运行 `python3 -m tkinter`。系统包安装后，已有虚拟环境通常不需要重建。

### `_tkinter.TclError: no display name` 或无法连接显示器

这表示当前 Linux 会话没有可用的图形桌面，常见于 SSH、服务器和容器。请直接使用：

```bash
jlu-booking-auto --show-config
jlu-booking-auto --dry-run
```

不要使用 `sudo jlu-booking`，因为它可能丢失当前用户的显示环境和配置目录。

### `jlu-booking` 或 `jlu-booking-auto: command not found`

先确认虚拟环境已激活：

```bash
which python
python -m pip show jlu-booking
```

然后尝试模块入口：

```bash
python -m jlu_booking
python -m jlu_booking.auto --help
```

如果 `pip show` 查不到项目，请回到包含 `pyproject.toml` 的目录重新执行：

```bash
python -m pip install -e .
```

### 粘贴命令后出现 `^[[200~`、乱码或奇怪字符

按 `Ctrl+C` 取消当前命令，重新复制代码块中命令本身，不要复制终端提示符、说明文字或结尾符号。如果所用远程终端仍会破坏粘贴内容，可以逐行手动输入。

### 自动任务提示“没有找到可用 Token”

先运行 `jlu-booking-token status`。若显示未保存，运行 `jlu-booking-token set` 后再启动自动任务；也可以重新运行自动任务，在隐藏输入提示中粘贴完整 Token。若是 cron 或 systemd，请确认它与保存 Token 使用的是同一个 Linux 用户。

### 修改 Token 后仍然使用旧值

先重启当前任务。若 `jlu-booking-token status` 显示环境变量优先，说明 Shell、cron 或 systemd 中的 `JLU_BOOKING_TOKEN` 正在覆盖保存文件；清除或更新该环境变量后再运行。

## 11. 一套可直接照抄的 Ubuntu/Debian 流程

假设当前已经进入包含 `pyproject.toml` 的项目目录：

```bash
sudo apt update
sudo apt install python3 python3-venv python3-tk
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
jlu-booking-auto --help
jlu-booking-auto --show-config
jlu-booking-token set
jlu-booking
```

其中 `sudo apt ...` 通常只需安装一次；以后重新打开终端，只需要进入项目目录、激活 `.venv`，然后启动需要的命令。

## 12. 参考资料

- [Python 官方 venv 文档](https://docs.python.org/3/library/venv.html)
- [Ubuntu `python3-venv` 软件包](https://packages.ubuntu.com/search?keywords=python3-venv)
- [Ubuntu `python3-tk` 软件包](https://packages.ubuntu.com/search?keywords=python3-tk)
- [Fedora `python3-tkinter` 软件包](https://packages.fedoraproject.org/pkgs/python3.13/python3-tkinter/)
- [Arch Linux `tk` 软件包](https://archlinux.org/packages/extra/x86_64/tk/)
