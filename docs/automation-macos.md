# macOS：每天 07:28 自动运行

这份教程使用 macOS 自带的 `launchd`，每天 07:28 启动一次 JLU Booking 自动任务。配置文件放在当前用户的 `~/Library/LaunchAgents` 中，不需要 `sudo`，也不要把 Token 写入 `.plist`。

> 自动任务可能提交真实预约。第一次测试务必在 GUI 中选择“仅扫描”，确认一切正常后，再改成“真实预约”并点击“保存配置”。

## 1. 开始前准备

1. 根据自己的安装方式，把程序放在不会移动的位置：
   - Release 版：建议放到 `/Applications/JLU Booking.app`
   - 源码版：例如 `/Users/你的用户名/jlu-booking`
2. 手动打开一次 JLU Booking，完成 macOS 首次打开检查。
3. 在 GUI 中配置 Token、场馆、项目、日期、同行人、场地、时间和“仅扫描”模式。
4. 点击“保存配置”。不需要点击“保存并启动”。

Release 版和源码版不要混着设置：用哪个版本的 GUI 保存配置，就在下面选择同一个版本的模板。

## 2. 先确认命令与路径

打开“终端”，根据安装方式选择一组命令。

### Release 版

```bash
"/Applications/JLU Booking.app/Contents/MacOS/JLU Booking" --auto-worker --show-config
"/Applications/JLU Booking.app/Contents/MacOS/JLU Booking" --auto-worker --show-paths
```

### 源码版

下面假设项目位于 `~/jlu-booking`：

```bash
cd "$HOME/jlu-booking"
"$HOME/jlu-booking/.venv/bin/python" -m jlu_booking.auto --show-config
"$HOME/jlu-booking/.venv/bin/python" -m jlu_booking.auto --show-paths
```

`--show-config` 只显示脱敏后的配置，不会提交预约。请确认场馆、项目、日期、首选场地、同行人尾号和运行模式正确。`--show-paths` 会显示配置、Token、日志和状态文件的真实位置。

源码版若没有 `.venv/bin/python`，请先双击项目根目录的 `start.command`。

## 3. 创建 LaunchAgent 文件

先准备目录：

```bash
mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$HOME/Library/Logs/JLUBooking"
```

查看自己的用户名：

```bash
whoami
```

然后创建配置文件：

```bash
nano "$HOME/Library/LaunchAgents/io.github.xiaoyoumi9924.jlu-booking.daily.plist"
```

根据安装方式，只粘贴下面一个完整模板。模板中的 `YOUR_USERNAME` 必须全部替换成刚才 `whoami` 显示的用户名。

### Release 版模板

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.github.xiaoyoumi9924.jlu-booking.daily</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Applications/JLU Booking.app/Contents/MacOS/JLU Booking</string>
        <string>--auto-worker</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>7</integer>
        <key>Minute</key>
        <integer>28</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/Library/Logs/JLUBooking/scheduler.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/Library/Logs/JLUBooking/scheduler-error.log</string>
</dict>
</plist>
```

### 源码版模板

下面仍假设源码位于 `/Users/YOUR_USERNAME/jlu-booking`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.github.xiaoyoumi9924.jlu-booking.daily</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOUR_USERNAME/jlu-booking/.venv/bin/python</string>
        <string>-m</string>
        <string>jlu_booking.auto</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USERNAME/jlu-booking</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>7</integer>
        <key>Minute</key>
        <integer>28</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/Library/Logs/JLUBooking/scheduler.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/Library/Logs/JLUBooking/scheduler-error.log</string>
</dict>
</plist>
```

在 `nano` 中按 `Control + O` 保存，按回车确认文件名，再按 `Control + X` 退出。

## 4. 检查并加载任务

先检查 XML 格式：

```bash
plutil -lint "$HOME/Library/LaunchAgents/io.github.xiaoyoumi9924.jlu-booking.daily.plist"
```

看到 `OK` 后加载任务：

```bash
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.github.xiaoyoumi9924.jlu-booking.daily.plist"
```

查看任务是否已注册：

```bash
launchctl print "gui/$(id -u)/io.github.xiaoyoumi9924.jlu-booking.daily"
```

如果 `bootstrap` 提示服务已经存在，说明旧配置仍在运行。先执行：

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.github.xiaoyoumi9924.jlu-booking.daily.plist"
```

然后重新执行 `bootstrap`。

## 5. 安全地手动测试

1. 先回到 GUI，确认运行模式为“仅扫描”，点击“保存配置”。
2. 手动触发 LaunchAgent：

```bash
launchctl kickstart -k "gui/$(id -u)/io.github.xiaoyoumi9924.jlu-booking.daily"
```

3. 查看调度输出：

```bash
tail -f "$HOME/Library/Logs/JLUBooking/scheduler.log"
```

按 `Control + C` 只是退出日志查看，不会停止自动任务。需要停止测试任务时执行：

```bash
launchctl kill SIGTERM "gui/$(id -u)/io.github.xiaoyoumi9924.jlu-booking.daily"
```

还应使用第 2 节的 `--show-paths` 找到 JLU Booking 自己的事件日志和请求耗时日志，确认它们已经更新。

测试正常后，如需真实预约，在 GUI 选择“真实预约”并点击“保存配置”即可。LaunchAgent 下一次启动时会读取新设置。

## 6. 日常使用与排错

- 当前用户需要已经登录 macOS；锁定屏幕通常不影响用户级 LaunchAgent，尚未登录时不会运行。
- `StartCalendarInterval` 不会为了任务主动唤醒已经睡眠的 Mac。Apple 文档说明，错过的日历任务通常会在下次唤醒时合并执行一次，因此不保证恰好在 07:28 开始。需要准点运行时，应让 Mac 保持唤醒并接通电源。
- 计划任务无法交互式询问 Token。Token 过期后，请重新打开 GUI 输入有效 Token。
- 不要在 07:28 前后同时点击 GUI 的“保存并启动”，避免出现两个自动任务。
- 如果希望完整保留 07:28:00 开始的预热时间，可把模板中的分钟改为 `27`；程序会自行等待到 07:28。
- `scheduler-error.log` 有内容时，优先检查模板中程序路径、用户名和源码目录是否完全正确。

## 7. 修改、暂停或删除

修改 `.plist` 后，先卸载再重新加载：

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.github.xiaoyoumi9924.jlu-booking.daily.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.github.xiaoyoumi9924.jlu-booking.daily.plist"
```

临时停用只执行 `bootout`，保留文件即可。彻底删除时，先执行 `bootout`，再在访达中把该 `.plist` 移到废纸篓。删除 LaunchAgent 不会删除 JLU Booking 的配置、Token 或日志。

## 官方参考

- [Apple：Creating Launch Daemons and Agents](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)
- [Apple：Scheduling Timed Jobs](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html)
- macOS 本机手册：终端运行 `man launchctl` 和 `man launchd.plist`
