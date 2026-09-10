# JLU Booking 跨平台使用说明

本文是进阶说明，覆盖源码安装、Token、GUI、命令行、自动预约、配置路径和常见问题。只想直接使用的同学优先从 GitHub Releases 下载桌面版，打开程序后按首次使用向导操作即可。

## 1 安装

### 1.1 桌面安装包（推荐）

从项目 Releases 下载与你系统对应的产物，解压后双击 JLU Booking。桌面安装包已经包含 Python 和依赖，不需要执行下面的虚拟环境命令。

如果使用源码 ZIP，在安装 Python 3.10+ 后可以直接双击仓库根目录的 `start.bat`（Windows）、`start.command`（macOS）或 `start.sh`（Linux）。首次运行会自动创建 `.venv` 并安装本项目，后续直接打开 GUI。

### 1.2 桌面应用无法运行时使用 PyCharm

如果下载的 `.exe`、`.app` 或 Linux 可执行文件无法在当前电脑运行，可以把 PyCharm 作为备用启动方式：

1. 下载并解压项目的[源码 ZIP](https://github.com/xiaoyoumi9924/jlu-booking/archive/refs/heads/main.zip)。
2. 安装 Python 3.10 或更高版本。
3. 在 PyCharm 中选择 **Open**，打开解压后的整个项目文件夹，不要只打开某一个文件。
4. 根据 PyCharm 提示选择一个 Python 3.10+ 解释器。
5. 在项目根目录找到 `start.py`，右键选择 **Run 'start'**。
6. 第一次运行会自动创建项目专用的 `.venv` 并安装依赖；看到“正在打开 JLU Booking”后，GUI 会自动出现。

PyCharm 中只需要运行 `start.py`。截图中常见的 `start.bat`、`start.command` 和 `start.sh` 是各操作系统的双击启动器，不是需要在 PyCharm 中运行的 Python 文件。首次准备需要联网下载依赖；Linux 如果提示缺少 Tkinter，请先按照 [Linux 安装与使用指南](linux.md) 安装图形组件。

### 1.3 手动源码安装的环境要求

- Python 3.10 或更高版本
- 能访问吉林大学体育场馆系统的网络环境
- Tkinter 图形库；Windows 和 macOS 的 Python 官方安装包通常自带，部分 Linux 发行版需要单独安装

Linux 用户请注意：系统在虚拟环境外可能只有 `python3` 命令。Ubuntu/Debian 还经常把 `venv` 和 Tkinter 拆分为单独的软件包。完整的发行版命令和排错方法见 [Linux 安装与使用指南](linux.md)。

### 1.4 创建虚拟环境

Windows 或 macOS 在项目目录运行：

```bash
python -m venv .venv
```

Linux 在项目目录运行：

```bash
python3 -m venv .venv
```

如果 Ubuntu/Debian 创建失败，先安装系统依赖：

```bash
sudo apt update
sudo apt install python3 python3-venv python3-tk
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

Windows CMD：

```bat
.venv\Scripts\activate.bat
```

macOS 或 Linux：

```bash
source .venv/bin/activate
```

安装项目：

```bash
python -m pip install -e .
```

## 2 Token

Token 来自你自己的吉林大学体育场馆登录会话。登录系统后，可在浏览器开发者工具的 Network 面板中查看发送到 `easyserpClient` 的请求。GUI 既可以接收单独的 `token` 值，也可以接收包含 `token=...` 的完整请求地址；后一种方式会在本机提取 Token，整段网址不会被保存。

不要把 Token 发到聊天、Issue、截图或代码提交中，也不要粘贴进自动预约 JSON。GUI 第一次接收 Token 后，会先执行一次不会提交预约的只读场地查询；只有学校系统接受后才保存到项目目录之外的当前用户配置目录。无效或过期时会提示重新输入，通过时不弹出额外成功窗口。以后 GUI 和 `jlu-booking-auto` 会自动复用，因此非交互计划任务也不需要重复输入。

### 2.1 查看、保存、修改和清除

查看保存状态和文件位置，不显示 Token 内容：

```bash
jlu-booking-token status
```

不打开 GUI，直接隐藏输入并保存：

```bash
jlu-booking-token set
```

Token 失效或需要更换账号时，再运行一次 `jlu-booking-token set`，新值会覆盖旧值。清除保存值：

```bash
jlu-booking-token clear
```

如果安装后的短命令不可用，可以在项目目录运行：

```bash
python -m jlu_booking.token_cli status
python -m jlu_booking.token_cli set
python -m jlu_booking.token_cli clear
```

### 2.2 临时环境变量

Token 读取优先级为：`JLU_BOOKING_TOKEN` 环境变量 > 当前用户保存的 Token > 交互式隐藏输入。环境变量只临时覆盖保存值，不会自动写入文件。

当前终端临时设置方式如下。

macOS 或 Linux：

```bash
export JLU_BOOKING_TOKEN="你的 Token"
```

Windows PowerShell：

```powershell
$env:JLU_BOOKING_TOKEN = "你的 Token"
```

Windows CMD：

```bat
set JLU_BOOKING_TOKEN=你的 Token
```

终端关闭后，这些临时变量通常会失效。Token 文件是本机明文凭据，不会进入仓库；类 Unix 系统会尽量限制为 `0600`，Windows 放在当前用户配置目录并沿用该目录的访问控制。不要共享、上传或手动复制该文件。

## 3 图形界面

启动安装后的命令：

```bash
jlu-booking
```

或者在项目目录运行：

```bash
python -m jlu_booking
```

主界面可以切换场馆、运动项目和日期，查询可预约时段。真实手动预约需要依次完成同行人验证、可预约检查和最终确认，避免误触直接提交。

首次打开且没有已保存 Token 时，GUI 会显示使用向导。粘贴单独 Token 或完整请求地址后，程序先向学校系统执行只读验证；验证成功才保存，下次打开自动读取。Token 过期时会清除旧值并要求重新输入。

左侧的“场地查询”和“自动预约”是两个并列功能选项，当前功能会显示明确的选中状态；点击后只切换右侧内容，不会打开新窗口。左侧场馆也是全局选择：切换场馆后，查询页和自动预约页都会同步到该场馆，并刷新对应的运动项目。

右侧自动预约页面可以配置：

- 场馆和运动项目
- 今天或明天
- 同行人学工号
- 首选场地编号
- 时间段优先级
- 运行模式：“仅扫描”或“真实预约”两个并列选项

同行人学工号为 GUI 自动预约的必填项。点击“保存配置”或“保存并启动”后，程序会先使用已经验证的 Token 向学校系统验证同行人；验证成功后写入当前用户的预约配置，下次打开自动填入。同行人无效时会弹出原因并要求重新填写。

“仅扫描”和“真实预约”采用与运动项目相同的两个并列选择按钮，当前模式会显示勾选；选择真实预约时，页面会保留红色风险说明。“保存配置”会记住全部预约设置；“保存并启动”还会立即启动本次自动任务。启动后会自动弹出独立的大日志窗口。

保存的设置只影响下一次启动的自动任务，不会修改已经运行的进程。如需切换目标，请先停止当前任务，再保存并启动。

## 4 自动预约配置与命令行

日常使用推荐直接在 GUI 中配置、启动和停止。下面的命令行入口保留给定时任务、无桌面服务器和高级用户。

第一次执行以下命令时会生成默认配置，并以中文摘要显示当前内容：

```bash
jlu-booking-auto --show-config
```

初始配置为“前卫体育馆 / 羽毛球 / 未配置同行人 / 仅扫描”。在 GUI 验证并保存同行人后，命令行任务会自动读取；也可用本次命令的 `--companion` 或 `JLU_BOOKING_COMPANION` 临时覆盖。

如果需要 JSON 结构，可以执行 `jlu-booking-auto --show-config-json`。如果本次进程临时提供了学号，显示时仍会脱敏。

配置文件的结构等价于仓库中的 `config/auto_booking.example.json`：

```json
{
  "venue": "前卫体育馆",
  "sport": "羽毛球",
  "target_day": "今天",
  "preferred_court_number": 3,
  "real_booking_enabled": false,
  "time_priority": [
    ["17:30", "19:30"],
    ["15:30", "17:30"],
    ["19:30", "21:30"],
    ["10:00", "12:00"]
  ]
}
```

| 字段 | 含义 |
| --- | --- |
| `venue` | 预约场馆 |
| `sport` | 场馆支持的运动项目 |
| `target_day` | `今天` 或 `明天` |
| `preferred_court_number` | 同一时间段内优先选择的场地编号 |
| `real_booking_enabled` | `false` 仅扫描，`true` 允许真实提交 |
| `time_priority` | 从前到后的时间段优先级 |

### 4.1 命令行临时覆盖

命令行参数只影响本次运行，不会改写配置：

```bash
jlu-booking-auto \
  --venue 宋治平体育馆 \
  --sport 网球 \
  --day 明天 \
  --court 2 \
  --time 17:30-19:30 \
  --dry-run
```

Windows PowerShell 可以把命令写在一行，或使用反引号续行。

常用参数：

```text
--config PATH       使用指定配置文件
--show-config       用中文摘要显示生效配置
--show-config-json  用 JSON 显示生效配置
--show-paths        显示配置、日志、状态路径和凭据存储状态
--dry-run           本次仅扫描
--real-booking      本次允许真实提交
--time HH:MM-HH:MM  添加时间优先级，可重复传入
```

查看全部参数：

```bash
jlu-booking-auto --help
```

## 5 计划任务

按操作系统选择可以直接照做的教程：

- [Windows：使用任务计划程序每天 07:28 自动运行](automation-windows.md)
- [macOS：使用 launchd 每天 07:28 自动运行](automation-macos.md)
- [Linux：使用 cron 每天 07:28 自动运行](automation-linux.md)

程序本身不绑定任何操作系统调度器。需要定时启动时，在 Windows 任务计划程序、cron 或你使用的其他调度服务中执行：

```text
<虚拟环境中的 Python> -m jlu_booking.auto
```

工作目录可以是任意目录。计划任务会读取当前操作系统用户保存的 Token 与同行人配置，因此必须使用与 GUI 保存时相同的用户账号；也可以通过环境变量临时覆盖。

推荐每天在 `07:28` 前启动。程序在该时间之前会等待，随后按以下阶段工作：

| 时间 | 行为 |
| --- | --- |
| 07:28:00–07:29:50 | 预热阶段：每次响应后等待 0.3 秒再查询，只刷新候选目标，不提交预约 |
| 07:29:50–07:33:30 | 核心抢票阶段：没有目标时持续查询，有目标时锁定并尝试预约，响应后等待 0.1 秒 |
| 07:33:30–07:36:00 | 收尾阶段：逻辑与核心阶段相同，响应后等待 0.3 秒 |
| 07:36:00–22:30:00 | 全天捡漏：解除早上的目标锁定，每次响应后等待 10 秒再重新查询 |

核心和收尾阶段使用 `SEARCH / LOCKED / STOP` 状态：尚未开放时保持已锁定目标并直接重试，不重复查询；场地被预约或状态失效时才解锁并重新查询。任何阶段预约成功后都会立即结束。

自动任务在一次运行期间共用一个 HTTP Session，使查询、`canBook` 和 `freeBuyPlace` 尽可能复用已建立的连接；仍然只会串行发送一个请求，不会并发堆积。实际周期是“请求耗时 + 表中等待间隔”。限流时会自动退避；如果最终提交已发出但响应不确定，程序会停止重试并提示先手动核对，避免重复提交。操作系统睡眠或关机会中断进程，项目不会尝试修改电源设置。

## 6 配置、日志和状态位置

新安装默认使用操作系统标准用户目录：

- Windows：用户的 AppData 目录
- macOS：用户的 Application Support 目录
- Linux：XDG 配置和状态目录

查看当前机器的确切路径：

```bash
jlu-booking-auto --show-paths
```

为兼容旧版，如果项目目录中已经存在 `config/auto_booking.json` 或 `runtime/`，程序会继续使用它们。

可以显式覆盖：

```text
JLU_BOOKING_CONFIG_FILE=/absolute/path/auto_booking.json
JLU_BOOKING_RUNTIME_DIR=/absolute/path/runtime
```

输出中的 `token_file` 是本机 Token 文件；`config_file` 包含预约设置和同行人学号。`event_log` 与 `request_timing_log` 不保存 Token、学号或请求参数，成功状态也只记录预约目标。

请求耗时日志格式示例：

```text
2026-09-10T07:29:48.047+08:00 | #00023 | query         |    1640 ms | success
2026-09-10T07:29:51.323+08:00 | #00023 | canBook       |    3276 ms | error:not_open
2026-09-10T07:32:01.517+08:00 | #00041 | freeBuyPlace  |    1517 ms | success
```

## 7 常见问题

### GUI 无法启动并提示 Tkinter

Windows 和 macOS 建议使用 python.org 的官方 Python。Ubuntu/Debian 可以安装 `python3-tk`，其他 Linux 发行版请安装对应的 Tk 软件包。

安装 Tkinter 后可以这样检查；成功时会弹出一个 Tk 测试窗口：

```bash
python3 -m tkinter
```

如果当前是 SSH 或没有桌面环境的服务器，不应启动 GUI；请直接使用 `jlu-booking-auto`。

### Windows 上 Logo 没有显示或界面字体异常

新版本内置了 Logo 兜底，并会自动选择当前系统可用的中文字体。更新代码后请在项目目录重新执行 `python -m pip install -e .`，再关闭并重新打开 GUI。如果需要临时使用自己的 Logo，可以设置 `JLU_BOOKING_LOGO`，值为本机 PNG 图片的完整路径。

### 提示缺少 Token

先运行 `jlu-booking-token status` 检查保存状态。未保存时可在 GUI 中验证保存，或运行 `jlu-booking-token set`。计划任务必须与保存 Token 的 GUI 使用同一个系统用户。

### 已修改 Token，但程序仍使用旧值

关闭当前 GUI 或自动任务后，运行 `jlu-booking-token set` 更新保存值，再重新打开程序。如果当前终端仍设置了 `JLU_BOOKING_TOKEN`，它会优先覆盖本机保存值。

### 一直显示没有可预约场次

这通常表示当前场馆、项目和日期没有空闲场次。先在 GUI 中确认选择是否正确，并检查 Token 是否仍有效。

### 修改配置后正在运行的任务没有变化

配置只在任务启动时读取一次。停止当前任务并重新启动，才能载入新设置。

### 已取消预约但程序仍直接退出

程序无法自动得知你在学校系统中的取消操作。确认无误后，删除同一日期、场馆和项目对应的本地成功状态文件，再重新运行。

### 已经预约过，但自动任务仍发现空场

学校查询接口仍可能返回其他可预约场次。程序提交时如果服务器明确提示当天预约次数已达上限或剩余次数为 0，会把它识别为终止状态并立即退出，不会每隔数秒重复提交。该情况只表示账号当天不能继续预约，不会伪造一条新的预约成功记录。

### 接口突然报错

学校接口、参数或开放时间可能发生变化。先查看日志并确认网页登录是否正常；提交 Issue 时必须删去 Token、学工号、姓名和其他身份信息。
