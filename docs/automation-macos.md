# macOS：记住配置并定时运行

JLU Booking 会把已验证的 Token 与同行人学工号保存在当前 macOS 用户的配置目录中。`launchd` 使用同一个用户运行时可以直接复用。

## 推荐方式

1. 第一次打开 JLU Booking，粘贴 Token 并通过只读验证。
2. 进入“自动预约”，填写同行人学工号。
3. 选择“仅扫描”或“真实预约”，点击“保存配置”。
4. 以后重新打开程序会自动读取 Token 和同行人学工号。
5. 程序如果在 07:28 前启动，会自动等待到 07:28。

Token 文件和预约配置只属于当前用户，不会进入发布包或 Git 仓库。

## 可选：让日历或快捷指令只负责提醒

可让 LaunchAgent 在每天 07:28 以保存配置的同一用户运行自动任务。不要把 Token 或学号写进 LaunchAgent、快捷指令、Shell 脚本或截图。

## 高级命令行使用

命令行任务会读取本机保存值，也可用 `JLU_BOOKING_TOKEN`、`JLU_BOOKING_COMPANION` 或 `--companion` 临时覆盖。

查看、修改或清除本机 Token：

```bash
jlu-booking-token status
jlu-booking-token set
jlu-booking-token clear
```
