# 本地部署 Web 版（单机 / 单人）

这份文档只讲一件事：把 Web 版装在自己的电脑上，让它在每天早上 **07:25–07:35** 自动跑一次预约，其余时间不占内存。

不需要域名、Caddy、HTTPS 或公网服务器；服务只监听 `127.0.0.1`。如果要给别人用、走公网，请改看 [Web 版服务器部署与运维](web-deployment.md)。

> 桌面版（`JLU Booking.app` / `JLU Booking.exe`）没有“自动预约”模块，所以定时必须用这里的 Web 版。

## 快速部署（不用先克隆）

macOS / Linux：

```bash
curl -fsSL https://raw.githubusercontent.com/xiaoyoumi9924/jlu-booking/main/scripts/quick-start.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/xiaoyoumi9924/jlu-booking/main/scripts/quick-start.ps1 | iex
```

脚本会克隆到 `~/.jlu-booking`（可用 `JLU_BOOKING_HOME` 改），第一次自动部署，之后同一条命令直接打开。需要先装好 Git 和 Python 3.10 或更高版本。

## 已经克隆了仓库

在仓库根目录运行：

```bash
./jlu
```

Windows 用 `deploy.bat`；macOS 也可以双击 `deploy.command`。

第一次运行会自动完成部署，之后同一条 `./jlu` 就只是“启动并打开页面”：

- 没安装 → 自动安装；
- Web / 调度器没在跑 → 自动启动；
- 已经跑着 → 直接打开 `http://127.0.0.1:8000/`。

它会依次完成：

1. 创建独立环境 `.venv-web`，安装 `.[web]` 依赖；
2. 在 `runtime/web/secrets/` 生成两个加密密钥（`0600`）；
3. 写入私有配置 `runtime/web/web.env`；
4. 安装每天 07:25 的系统定时任务；
5. 启动 Web 与调度器，并打开 `http://127.0.0.1:8000/`。

首次安装需要联网下载依赖，通常几分钟；之后 `./jlu` 只做启动，几乎立刻打开。

> 想重新检查/修复安装（比如换了网络、移动了目录）用 `./jlu up`。
> 不想让它常驻，配置完执行 `./jlu stop`；之后每天 07:25 仍会由系统定时器自动启动。

## 首次使用

本机版是**单用户模式，不需要登录或注册**，打开就是“本机用户”。

1. 打开 `http://127.0.0.1:8000/`，按页面提示粘贴场馆网址，完成 Token 绑定。
2. 到“个人中心”验证同行人学工号。
3. 进入“自动预约”，打开每日开关，选择场馆、项目、时间和同行人，保存配置。

登录、注册和管理后台都只属于公网多用户部署，本机版不会出现。

## 它每天怎么跑

| 时间 | 动作 |
| --- | --- |
| 07:25 | 系统定时器启动调度器 |
| 07:27 | 每日任务开始创建并执行（07:29:57 前完成启动） |
| 07:35 | 预约核心停止发起新的学校请求 |
| 07:35 之后 | 最多再等 5 分钟，让运行中的任务收尾，然后退出 |

- 电脑必须开机且没有睡眠。macOS 的 launchd 会在唤醒后补跑错过的任务，但过了 07:35 就只会记录“已错过窗口”并退出。
- 需要更稳，可加一条定时唤醒（会改系统电源计划）：

  ```bash
  sudo pmset repeat wakeorpoweron MTWRFSU 07:20:00   # 每天 07:20 唤醒
  sudo pmset repeat cancel                            # 取消
  ```

- 如果你手动执行了 `start`，Web 和调度器会一直运行；此时 07:25 的窗口检测到已有调度器会直接跳过，不会重复启动。

## 常用命令

```bash
./jlu                       # 部署（首次）= 启动 + 打开页面（之后）
./jlu status                # 运行状态、最近任务
./jlu logs                  # Web 日志（--all 看全部，--follow 跟踪）
./jlu stop                  # 停止 Web 与调度器
./jlu restart               # 重启
./jlu backup                # 立即备份一次数据库
./jlu autostart status      # 查看每日自动运行
./jlu autostart on|off      # 开启/关闭
./jlu autostart --dry-run   # 只打印要安装的配置
./jlu uninstall             # 停止并移除自动运行
./jlu up                    # 重新检查/修复安装并启动
```

等价的长写法是 `python3 tools/deploy_local.py open|status|stop|up|...`。

- `./jlu install` 只安装不启动；`./jlu up` = 安装 + 自动运行 + 启动。
- 换端口：`./jlu install --port 8100`；换时间：`./jlu install --start 06:50 --end 07:05`。
- 国内网络慢：`./jlu install --index-url https://pypi.tuna.tsinghua.edu.cn/simple`。
- 卸载时加 `--purge` 会删除数据，加 `--remove-venv` 会删除 `.venv-web`。

## 文件位置

所有本地状态都在 `runtime/web/`（已被 `.gitignore` 忽略）：

```text
runtime/web/
├── web.env          # 路径与端口配置（0600）
├── secrets/         # token.key / blind.key（0600，丢失后无法解密 Token）
├── web.sqlite3      # 用户、任务、审计
├── backups/         # 每日数据库备份（保留最近 14 份）
├── logs/            # web.log / scheduler.log / window.log / launchd.log
└── run/             # pid 与调度器锁
```

密钥和数据库要一起备份；只有数据库、没有同一套密钥时，用户 Token 无法恢复。

## 常见问题

**端口被占用**：换端口重新 `install --port 8100`，或先停掉占用 8000 的进程。

**登录成功又跳回登录页**：确认 `runtime/web/web.env` 里是 `JLU_BOOKING_WEB_COOKIE_SECURE=false`。本地是 http，不能开 Secure Cookie。

**启动报密钥权限不安全**：`secrets/` 目录要 `0700`、两个 `.key` 要 `0600`；重新运行 `install` 会自动修复。

**07:35 时任务还在提交**：脚本会等最多 5 分钟再退出；如果仍然中断，任务会显示“结果待核对”，请到学校系统确认，不要重复提交。

**换电脑 / 移动目录**：数据目录跟仓库走，路径会随 `install` 自动更新；只移动仓库时重新运行一次 `install` 即可。
