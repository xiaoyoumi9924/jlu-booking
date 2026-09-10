# Windows：记住配置并定时运行

JLU Booking 会把已验证的 Token 与同行人学工号保存在当前 Windows 用户的 AppData 配置目录中。任务计划程序使用同一个 Windows 用户运行时，可以直接复用，不必每天重新输入。

## 推荐方式

1. 第一次打开 JLU Booking，粘贴 Token 并通过只读验证。
2. 进入“自动预约”，填写同行人学工号。
3. 选择“仅扫描”或“真实预约”，点击“保存配置”。
4. 以后重新打开程序会自动读取 Token 和同行人学工号。
5. 程序如果在 07:28 前启动，会自动等待到 07:28。

不要把 Token 或学号写进任务参数。Token 文件和预约配置不会进入发布包或 Git 仓库。

## 让任务计划程序直接运行

可以创建每天 07:28 的“启动程序”任务，目标填写 `JLU Booking.exe` 的完整路径，并添加参数 `--auto-worker`。运行用户必须与保存配置时相同。

## 高级命令行使用

`JLU_BOOKING_TOKEN`、`JLU_BOOKING_COMPANION` 和 `--companion` 仍可临时覆盖本机保存值。不要把真实 Token 或学号写进任务参数、脚本、仓库或截图。

查看、修改或清除本机 Token：

```powershell
jlu-booking-token status
jlu-booking-token set
jlu-booking-token clear
```
