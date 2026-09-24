# Web 版服务器部署与运维

Web 版是桌面程序之外的可选部署方式。浏览器提供实时场地查询、手动预约和自动任务配置；独立调度器在服务器上运行原有 `jlu_booking.auto` 预约核心。生产环境只从已提交的 Git 修订部署；Token、同行人、数据库、密钥、备份和运行日志全部留在服务器数据目录，不进入仓库。

以下命令以 Debian/Ubuntu、专用域名 `booking.example.com` 和专用低权限用户 `jlu-booking` 为例。请把示例域名换成自己购买域名下的子域名。

## 1. 安装 Python、Git 和 Caddy

安装 Python 3.10 或更高版本、`python3-venv`、Git 和 Caddy。确认 Caddy 使用官方软件源提供的当前稳定版，并让防火墙只公开 80/443；Uvicorn 始终监听 `127.0.0.1:8000`。

## 2. 创建服务账号和私有目录

```bash
sudo useradd --system --home /var/lib/jlu-booking --shell /usr/sbin/nologin jlu-booking
sudo install -d -o jlu-booking -g jlu-booking -m 0700 /var/lib/jlu-booking
sudo install -d -o root -g jlu-booking -m 0750 /etc/jlu-booking /etc/jlu-booking/secrets
```

应用代码建议放在 `/opt/jlu-booking`。服务账号只能写 `/var/lib/jlu-booking`；密钥目录只允许 root 和服务组读取。

## 3. 从已提交版本安装

在 Mac 本地完成开发、测试和提交，推送 GitHub 后，服务器检出明确的分支、标签或提交。先确认工作区干净：

```bash
cd /opt/jlu-booking
git status --short
git rev-parse HEAD
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install '.[web]'
```

不要从未提交的 worktree、聊天附件或临时复制目录部署。

## 4. 生成密钥并创建首个管理员

```bash
sudo /opt/jlu-booking/.venv/bin/jlu-booking-web generate-keys --directory /etc/jlu-booking/secrets
sudo chmod 0600 /etc/jlu-booking/secrets/*.key
sudo chown jlu-booking:jlu-booking /etc/jlu-booking/secrets/*.key
sudo install -m 0640 -o root -g jlu-booking deploy/jlu-booking-web.env.example /etc/jlu-booking/web.env
sudo -u jlu-booking env $(cat /etc/jlu-booking/web.env | xargs) /opt/jlu-booking/.venv/bin/jlu-booking-web create-admin owner
```

管理员密码通过终端隐藏输入两次。环境文件只保存路径和布尔设置；Fernet 密钥和盲索引密钥分别保存在 `0600` 文件中。

## 5. 配置域名

在域名提供商控制台创建 `booking` 子域名的 A 记录，指向服务器公网 IPv4；如使用 IPv6，再添加 AAAA 记录。等待 DNS 生效后，用 `dig booking.example.com` 检查解析结果。

## 6. 安装并启动 systemd 服务

```bash
sudo install -m 0644 deploy/jlu-booking-web.service /etc/systemd/system/
sudo install -m 0644 deploy/jlu-booking-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jlu-booking-web.service
sudo systemctl enable --now jlu-booking-scheduler.service
```

Web 与调度器必须是两个独立服务。调度器启动预约子进程；Web 请求进程不直接启动、停止或等待自动预约核心。网页版手动预约由 Web 进程执行学校查询、预检和一次明确确认后的提交，仍需保持单个 Web 工作进程，保证启动时将中断的提交标记为“结果不明”。

## 7. 安装 Caddy 站点并验证 HTTPS

复制 `deploy/Caddyfile.example` 中的站点块到 Caddy 配置，替换示例域名，验证并重载：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

访问 HTTPS 页面，检查证书、注册、登录、退出、CSRF 拒绝和移动端布局。普通用户登录后进入场地查询工作台；管理员登录后进入独立后台，可查看用户、任务和审计记录。公网只经过 Caddy；不要让 8000 端口监听公网地址。

实时场地查询使用当前用户自己的 Token。手动预约必须经过“查询 → 预检 → 确认真实预约 → 结果页”，确认按钮会向学校系统提交真实预约。成功或结果不明的请求不会因刷新页面重复提交；结果不明时请到学校系统核对，不要重新点击提交。同一账号的手动提交与运行中的自动任务互斥，手动预约成功或结果不明会取消同目标日期尚未启动的自动任务。

每日自动预约按用户开启时间先到先得，每个执行日最多创建 10 个自动任务。关闭每日开关会取消尚未启动的每日任务；已经运行或已经向学校提交的任务不会撤销。一次性任务仍可单独创建。

## 8. 只做安全的演练

自动化测试和部署验收不得调用真实预约接口，也不得产生真实预约。先运行完整离线测试：

```bash
.venv/bin/python -m pytest
.venv/bin/python tools/privacy_check.py
.venv/bin/python -m compileall -q jlu_booking
```

网页中创建演练任务时保持“仅扫描”模式，并使用专门的本地假验证器或测试数据库。验收手动预约流程时必须注入假的学校查询、预检和提交函数；不要在生产网页点击“确认真实预约”做测试，也不要把真实 Token 写进命令、截图或工单。

本版本的本地 HTTP 烟雾检查命令与结果：

```text
python -m pytest tests/web/test_web_smoke.py -v
4 passed
```

该检查使用临时 SQLite、禁用 localhost 的 Secure Cookie、假 Token/同行人验证器、假学校查询/预检/提交函数和假工作进程；测试会在任何 `requests.Session.request` 调用发生时立即失败。它验证注册、登录、管理员入口、每日开关、手动预约完整流程、静态资源及十用户隔离，不启动真实调度命令，也不访问学校接口。

## 9. 日常检查和备份

```bash
sudo systemctl status jlu-booking-web jlu-booking-scheduler
sudo journalctl -u jlu-booking-web -u jlu-booking-scheduler --since today
sudo -u jlu-booking env $(cat /etc/jlu-booking/web.env | xargs) /opt/jlu-booking/.venv/bin/jlu-booking-web backup
df -h /var/lib/jlu-booking
du -sh /var/lib/jlu-booking/*
```

每天在低峰执行 `jlu-booking-web backup`。程序用 SQLite 在线备份 API 生成私有快照并保留最近 14 份。密钥必须单独加密备份；只有数据库而没有同一套密钥时，无法恢复用户 Token。

## 10. 更新

1. 在 Mac 本地完成修改、测试、提交和推送。
2. 服务器停止 Web，随后停止调度器；确认没有处于预约窗口的任务。
3. 执行一次数据库和密钥备份。
4. `git fetch` 后检出明确的已提交修订，确认 `git status --short` 为空。
5. 重新执行 `.venv/bin/python -m pip install '.[web]'` 和离线测试。
6. 先启动 `jlu-booking-web` 并检查页面，再启动 `jlu-booking-scheduler`。

## 回滚

停止 Web 和调度器，检出上一份已知正常的提交。选择与密钥备份时间相匹配的 SQLite 快照，先把当前数据库留作故障证据，再原子替换 `/var/lib/jlu-booking/web.sqlite3`；同时恢复匹配的 `token.key` 和 `blind.key`，设置所有者及 `0600` 权限。先启动 Web 验证登录和数据，再启动调度器。不要把不匹配的旧密钥和新数据库混用，也不要在 07:27–07:33 预约窗口内进行回滚演练。
