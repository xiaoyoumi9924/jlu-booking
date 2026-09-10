# Linux：每天 07:28 自动运行

这份教程使用 Linux 常见的 `cron` 用户任务，每天 07:28 启动一次 JLU Booking。任务不需要图形桌面，也不要使用 `sudo`，否则会读取到 root 用户的另一套配置和 Token。

> 自动任务可能提交真实预约。第一次测试务必使用“仅扫描”，确认一切正常后，再改成“真实预约”。

## 1. 开始前准备

1. 把程序放在不会移动的位置，例如：
   - 源码版：`/home/你的用户名/jlu-booking`
   - Release 版：`/home/你的用户名/Applications/JLU Booking`
2. 有桌面环境时，可以先打开 GUI 配置 Token、场馆、项目、日期、同行人、场地、时间和“仅扫描”模式，然后点击“保存配置”。
3. 没有桌面环境时，请按照 [Linux 安装与使用指南](linux.md) 使用命令行准备配置和 Token。
4. Release 版和源码版不要混着设置；使用哪个版本保存配置，就定时运行同一个版本。

源码版如果还没有 `.venv/bin/python`，先在项目目录运行一次 `./start.sh`，或按照 Linux 指南手动建立虚拟环境。

## 2. 先确认命令与配置

根据安装方式选择一组命令。

### 源码版

下面假设项目位于 `~/jlu-booking`：

```bash
cd "$HOME/jlu-booking"
"$HOME/jlu-booking/.venv/bin/python" -m jlu_booking.auto --show-config
"$HOME/jlu-booking/.venv/bin/python" -m jlu_booking.auto --show-paths
```

### Release 版

下面假设解压后的可执行文件为 `~/Applications/JLU Booking/JLU Booking`：

```bash
"$HOME/Applications/JLU Booking/JLU Booking" --auto-worker --show-config
"$HOME/Applications/JLU Booking/JLU Booking" --auto-worker --show-paths
```

`--show-config` 只显示脱敏后的配置，不会提交预约。请确认场馆、项目、日期、首选场地、同行人尾号和运行模式正确。`--show-paths` 会显示配置、Token、日志和状态文件的真实位置。

如果 Release 可执行文件没有运行权限，可以执行：

```bash
chmod u+x "$HOME/Applications/JLU Booking/JLU Booking"
```

## 3. 确认 cron 可用

```bash
command -v crontab
```

如果没有输出，需要先安装并启用发行版提供的 cron 服务。例如 Ubuntu/Debian 通常使用 `cron`，Fedora/RHEL 系通常使用 `cronie`。安装方法可查阅所用发行版的官方文档。

再确认系统时间和时区正确：

```bash
date
```

cron 按系统本地时间触发；如果系统不是中国标准时间，`07:28` 代表的是该机器当前时区的早上 7:28。

## 4. 添加每天 07:28 的任务

先创建一个只允许当前用户访问的调度日志目录：

```bash
mkdir -p "$HOME/.local/state/jlu-booking"
chmod 700 "$HOME/.local/state/jlu-booking"
```

编辑当前用户的任务表：

```bash
crontab -e
```

第一次使用时，系统可能让你选择编辑器；不熟悉时可选择 `nano`。根据安装方式，在文件末尾只添加下面一行。

### 源码版任务

```cron
28 7 * * * cd "$HOME/jlu-booking" && "$HOME/jlu-booking/.venv/bin/python" -m jlu_booking.auto >> "$HOME/.local/state/jlu-booking/scheduler.log" 2>&1
```

### Release 版任务

```cron
28 7 * * * "$HOME/Applications/JLU Booking/JLU Booking" --auto-worker >> "$HOME/.local/state/jlu-booking/scheduler.log" 2>&1
```

这五个时间字段依次表示“分钟、小时、日期、月份、星期”；`28 7 * * *` 即每天 07:28。保存退出后，无需重启 cron。

检查任务是否保存成功：

```bash
crontab -l
```

输出中应该能看到刚添加的 JLU Booking 命令。不要使用 `sudo crontab -e`，否则任务会以 root 身份运行，无法正常复用当前用户在 GUI 中保存的 Token。

## 5. 安全地手动测试

先确保配置为“仅扫描”，然后在终端手动运行定时任务里的程序部分。

### 源码版

```bash
cd "$HOME/jlu-booking"
"$HOME/jlu-booking/.venv/bin/python" -m jlu_booking.auto --dry-run
```

### Release 版

```bash
"$HOME/Applications/JLU Booking/JLU Booking" --auto-worker --dry-run
```

看到配置摘要、同行人处理和当前扫描阶段后，可以按 `Control + C` 停止。然后检查：

```bash
tail -n 100 "$HOME/.local/state/jlu-booking/scheduler.log"
```

首次手动测试不会经过 cron，所以调度日志可能还是空的；JLU Booking 自己的事件日志和请求耗时日志应以 `--show-paths` 输出的位置为准。

确认正常后，如需真实预约，再在 GUI 选择“真实预约”并保存；无桌面用户可更新配置中的 `real_booking_enabled`，或在 cron 命令中明确增加 `--real-booking`。后者会强制开启真实提交，请谨慎使用。

## 6. 日常使用与排错

- Linux 服务器通常适合 cron；笔记本关机或睡眠时，普通 cron 会错过 07:28 的任务，不会自动补跑。
- cron 的环境变量比交互式终端少，所以教程始终使用 Python 或程序的绝对路径。
- 计划任务无法交互式询问 Token。Token 过期后，请用 GUI 或 `jlu-booking-token set` 更新。
- 不要在 07:28 前后同时从 GUI、SSH 或其他计划任务再次启动，避免两个进程同时运行。
- 如果希望完整保留 07:28:00 开始的预热时间，可把 cron 行开头改成 `27 7 * * *`；程序会自行等待到 07:28。
- cron 已触发但没有结果时，依次检查 `crontab -l`、`scheduler.log` 和 `jlu-booking-auto --show-paths` 给出的业务日志。

## 7. 修改、暂停或删除

再次运行：

```bash
crontab -e
```

- 修改时间：编辑行首的分钟和小时。
- 临时暂停：在该行最前面加 `#`。
- 恢复：删除行首的 `#`。
- 删除：删除整行并保存。

删除 cron 行不会删除 JLU Booking 的配置、Token、日志或预约成功状态。

## 官方参考

- [Cronie 上游：crontab(5) 格式与时间字段](https://github.com/cronie-crond/cronie/blob/master/man/crontab.5)
- [Debian Manpages：crontab(1)](https://manpages.debian.org/bookworm/cron/crontab.1.en.html)
