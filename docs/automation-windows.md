# Windows：每天 07:28 自动运行

这份教程使用 Windows 自带的“任务计划程序”，每天 07:28 启动一次 JLU Booking 自动任务。设置完成后不需要提前打开 GUI，也不要把 Token 写进任务参数。

> 自动任务可能提交真实预约。第一次测试务必在 GUI 中选择“仅扫描”，确认一切正常后，再改成“真实预约”并点击“保存配置”。

## 1. 开始前准备

1. 把程序放在一个不会移动的位置，例如：
   - Release 版：`C:\Users\你的用户名\Applications\JLU Booking\`
   - 源码版：`C:\Users\你的用户名\jlu-booking\`
2. 双击打开 JLU Booking，在 GUI 中完成 Token、场馆、项目、日期、同行人、场地、时间和运行模式设置。
3. 点击“保存配置”。不需要点击“保存并启动”。
4. 以后不要移动或重命名程序文件夹，否则计划任务中的路径会失效。

Release 版和源码版不要混着设置：用哪个版本的 GUI 保存配置，就定时运行同一个版本。

## 2. 先确认程序可以被命令行调用

打开 PowerShell，根据自己的安装方式选择一组命令。

### Release 版

把下面路径换成你电脑上 `JLU Booking.exe` 的真实位置：

```powershell
& "C:\Users\你的用户名\Applications\JLU Booking\JLU Booking.exe" --auto-worker --show-config
& "C:\Users\你的用户名\Applications\JLU Booking\JLU Booking.exe" --auto-worker --show-paths
```

### 源码版

```powershell
Set-Location "C:\Users\你的用户名\jlu-booking"
& ".\.venv\Scripts\python.exe" -m jlu_booking.auto --show-config
& ".\.venv\Scripts\python.exe" -m jlu_booking.auto --show-paths
```

`--show-config` 只显示脱敏后的配置，不会提交预约。请确认场馆、项目、日期、首选场地、同行人尾号和运行模式正确。`--show-paths` 会告诉你配置、Token、日志和状态文件的实际位置。

如果源码版没有 `.venv\Scripts\python.exe`，请先双击项目根目录中的 `start.bat`，让程序完成首次环境准备。

## 3. 创建每天 07:28 的任务

1. 按 `Win + R`。
2. 输入 `taskschd.msc`，按回车打开“任务计划程序”。
3. 在右侧点击“创建基本任务”。
4. 名称填写 `JLU Booking 每日自动预约`。
5. 触发器选择“每天”，开始时间填写 `07:28:00`，间隔为 1 天。
6. 操作选择“启动程序”。
7. 根据安装方式填写下面三个字段。

### Release 版填写方式

| 字段 | 内容示例 |
| --- | --- |
| 程序或脚本 | `C:\Users\你的用户名\Applications\JLU Booking\JLU Booking.exe` |
| 添加参数 | `--auto-worker` |
| 起始于 | `C:\Users\你的用户名\Applications\JLU Booking` |

### 源码版填写方式

| 字段 | 内容示例 |
| --- | --- |
| 程序或脚本 | `C:\Users\你的用户名\jlu-booking\.venv\Scripts\python.exe` |
| 添加参数 | `-m jlu_booking.auto` |
| 起始于 | `C:\Users\你的用户名\jlu-booking` |

“程序或脚本”应填写文件的完整路径，不要只填 `python`；“起始于”只填写文件夹路径，不要加参数。

8. 点击“完成”。
9. 在“任务计划程序库”找到刚创建的任务，双击打开“属性”。
10. 建议检查以下设置：
    - “常规”：运行用户必须与在 GUI 中保存 Token 的 Windows 用户相同。
    - “条件”：笔记本需要时可勾选“唤醒计算机运行此任务”；如果希望使用电池时也运行，请取消“只有在计算机使用交流电源时才启动”。
    - “设置”：勾选“如果错过计划开始时间，尽快运行任务”。
    - “设置”：如果任务已在运行，选择“不启动新实例”。

如果选择“只在用户登录时运行”，使用最简单，但注销后不会启动；如果选择“不管用户是否登录都要运行”，Windows 可能要求输入当前账户密码。

## 4. 安全地手动测试

1. 先回到 GUI，确认运行模式为“仅扫描”，点击“保存配置”。
2. 在任务计划程序中右键任务，点击“运行”。
3. 等待几十秒，然后查看“状态”和“上次运行结果”。
4. 使用第 2 节的 `--show-paths` 找到日志目录，确认事件日志和请求耗时日志已经更新。
5. 测试结束后，在任务计划程序中右键任务并选择“结束”。

确认测试正常后，如需真实预约，再到 GUI 选择“真实预约”并点击“保存配置”。计划任务下一次启动时会读取新设置，不需要重新创建。

## 5. 日常使用与排错

- 电脑关机时无法执行。睡眠状态下只有启用“唤醒计算机运行此任务”并且硬件允许时，才可能准时启动。
- 计划任务不会弹窗询问 Token。Token 过期后，请重新打开 GUI 输入有效 Token。
- 不要在 07:28 前后同时点击 GUI 的“保存并启动”，否则可能出现两个自动任务。
- 程序每天只启动一次；启动后会按照程序内置阶段运行，成功或达到停止条件后自行结束。
- 如果希望完整保留 07:28:00 开始的预热时间，可以把任务时间提前到 07:27，程序会自行等待到 07:28。
- `0x0` 通常表示任务正常结束；其他结果可结合任务“历史记录”和 JLU Booking 日志判断。

## 6. 修改、暂停或删除

- 修改时间：任务计划程序 → 任务属性 → “触发器” → 编辑。
- 临时暂停：右键任务 → “禁用”。恢复时选择“启用”。
- 删除：右键任务 → “删除”。删除计划任务不会删除 JLU Booking 的配置、Token 或日志。

## 官方参考

- [Microsoft Learn：schtasks create 与每日计划格式](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create)
- [Microsoft Learn：Windows 任务计划程序命令](https://learn.microsoft.com/en-us/windows/win32/taskschd/schtasks)
