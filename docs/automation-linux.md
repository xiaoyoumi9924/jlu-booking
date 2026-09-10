# Linux：记住配置并定时运行

JLU Booking 会把已验证的 Token 与同行人学工号保存在当前 Linux 用户的配置目录中。`cron` 或 `systemd` 使用同一个用户运行时可以直接复用。

## 推荐方式

有桌面环境时，第一次在 GUI 输入 Token 和同行人学工号并点击“保存配置”。无桌面环境时可隐藏输入并保存 Token：

```bash
jlu-booking-token set
jlu-booking-auto --show-config
```

同行人学工号可在 GUI 保存，也可编辑 `jlu-booking-auto --show-paths` 显示的配置文件。

Token 文件和预约配置只属于当前用户，不会进入发布包或 Git 仓库。

## 无人值守运行

定时任务必须使用与保存配置时相同的 Linux 用户。不要把真实 Token 或学号直接写进 crontab、systemd unit、Shell 脚本、仓库或日志。

查看、修改或清除本机 Token：

```bash
jlu-booking-token status
jlu-booking-token set
jlu-booking-token clear
```
