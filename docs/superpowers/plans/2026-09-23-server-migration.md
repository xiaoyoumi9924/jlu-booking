# JLU Booking Server Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the four legacy booking jobs, multi-user web application, static `gym` site, private runtime data, and existing administration commands from the current Alibaba Cloud ECS host to the owner's Ubuntu 24.04 server without live booking tests or duplicate scheduling.

**Architecture:** Rebuild operating-system and Python dependencies on the clean destination, then let the destination pull an allowlisted set of application paths over a temporary source-IP-restricted SSH key. Keep every destination scheduler disabled through staging and verification; freeze the source only during the final delta copy, then restore the exact timer state on the destination before DNS is changed.

**Tech Stack:** Ubuntu 22.04/24.04, OpenSSH, rsync, Python 3.12 virtual environments, SQLite backup API, systemd, Nginx, Git, Certbot after ICP approval

**Spec:** `docs/superpowers/specs/2026-09-23-server-migration-design.md`

## Global Constraints

- Do not call a real JLU reservation endpoint or manually start a booking service.
- Do not modify booking code, CORE timing, candidate ordering, error handling, or deadline behavior.
- Do not place passwords, tokens, encryption keys, or environment-file contents in commands, logs, Git, or this run record.
- Keep all destination booking timers and `jlu-booking-scheduler.service` disabled until the source equivalents are confirmed inactive.
- Keep `jlu-booking.timer` disabled after cutover; restore timers 2, 3, and 4 to enabled.
- Copy the web database through SQLite's backup API and validate it with `PRAGMA integrity_check`.
- Deploy only committed application revisions and preserve known untracked static-site backup data.
- Do not copy BT panel internals, `site_total.service`, the BT ACME cron job, SSH host keys, caches, shell history, or source host networking.
- Do not change DNS until local destination checks pass; do not request public TLS until ICP permits HTTP validation.
- Retain the source application files and data unchanged for at least 48 hours after cutover while its booking-related units remain stopped and disabled.
- In the normal post-cutover state, every source booking timer, booking scheduler, booking service, and booking web service remains stopped and disabled; only an explicit rollback may re-enable them.

## Review Focus

- **Conflicting destination UID/GID:** stop before copying; never allow restored private files to acquire an unrelated owner.
- **SQLite changed during transfer:** use online backup for staging and a final backup while web writes are stopped; integrity and expected non-secret counts must match.
- **Both schedulers active:** prove source inactivity before enabling any destination timer or scheduler, and prove destination inactivity before any rollback enablement.
- **Shortcut drift:** validate every command with `command -v`, `readlink -f`, and its expected user mapping without opening an editor or changing configuration.
- **DNS/ICP/TLS mismatch:** test Nginx locally with explicit `Host` headers, defer DNS and certificate issuance until the site is eligible, and keep an immediate DNS rollback path.

---

## File and system map

No product source file is changed by this migration. The execution creates or modifies these operational paths:

| Location | Responsibility |
|---|---|
| `docs/operations/2026-09-23-server-migration-record.md` | Non-secret execution evidence, checkpoints, and final state |
| `/root/jlu-migration-20260923/` on each host | Private temporary manifests, backups, and checksums; mode `0700` |
| `/root/.ssh/jlu-migration-20260923` on destination | Temporary pull-only migration key; removed after verification |
| `/home/jlu*/jlu-booking` | Four preserved legacy code checkouts with rebuilt `.venv` directories |
| `/home/jlu*/.config/jlu-booking` | Per-user private configuration and token |
| `/home/jlu*/.local/state/jlu-booking` | Per-user logs and success state |
| `/opt/jlu-booking` | Web application checkout with rebuilt `.venv` |
| `/var/lib/jlu-booking` | Web SQLite database, backups, and runtime state |
| `/etc/jlu-booking` | Web environment and encryption keys |
| `/var/www/jlu-gym-frontend` | Static `gym` site and preserved backup tree |
| `/etc/systemd/system/jlu-booking*` | Booking services and timers |
| `/usr/local/bin/{jlu-set-config,jlu-status,server-status}` | Administration commands |
| `/usr/local/bin/{wang-set,xia-set,hong-set,wangkeji-set}` | Stable command symlinks |
| `/etc/nginx/sites-available/` | Booking and `gym` virtual hosts, enabled only after local validation |

## Interfaces between tasks

- Tasks 1–2 produce source manifests, destination prerequisites, and the temporary SSH transport used by Tasks 3–6.
- Tasks 3–4 produce restored files and rebuilt runtimes while schedulers remain disabled.
- Task 5 produces the explicit local-verification gate required by Task 6.
- Task 6 produces the final consistent data copy and proof that the source scheduler is stopped.
- Task 7 enables destination services and optionally changes DNS only after Task 6 succeeds.
- Task 8 removes temporary access, records the rollback window, and commits the non-secret run record.

### Task 1: Record immutable preflight state

**Files:**
- Create: `docs/operations/2026-09-23-server-migration-record.md`
- Create on both hosts: `/root/jlu-migration-20260923/`

**Interfaces:**
- Produces: source UID/GID table, enabled-unit table, revision table, storage totals, Beijing-time safety check, and a destination-conflict decision.

- [ ] **Step 1: Create the non-secret run record locally**

Create the exact section structure below. Record only statuses, checksums, revisions, counts, and timestamps; never paste configuration or secret values.

```bash
mkdir -p docs/operations
cat > docs/operations/2026-09-23-server-migration-record.md <<'EOF'
# JLU Booking Server Migration Record

## Preflight

## Initial sync

## Offline verification

## Final cutover

## DNS and TLS

## Temporary-access removal

## Rollback deadline and source shutdown
EOF
```

- [ ] **Step 2: Confirm the execution window**

Run on both hosts:

```bash
TZ=Asia/Shanghai date --iso-8601=seconds
pgrep -af 'jlu-booking|jlu_booking' || true
```

Expected: execution is outside 07:27–07:33 Asia/Shanghai and there is no active legacy booking process. A web process may be active on the source during initial staging.

- [ ] **Step 3: Create private working directories**

Run on each host:

```bash
install -d -m 0700 /root/jlu-migration-20260923
```

- [ ] **Step 4: Capture source identity, service, revision, and filesystem metadata**

Run on the source and save outputs under the private working directory:

```bash
getent passwd jlu jlu2 jlu3 jlu4 jlu-booking > /root/jlu-migration-20260923/passwd.txt
getent group jlu jlu2 jlu3 jlu4 jlu-booking > /root/jlu-migration-20260923/group.txt
systemctl is-enabled jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer jlu-booking-web.service jlu-booking-scheduler.service > /root/jlu-migration-20260923/enabled.txt
systemctl is-active jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer jlu-booking-web.service jlu-booking-scheduler.service > /root/jlu-migration-20260923/active.txt
git -C /home/jlu/jlu-booking rev-parse HEAD > /root/jlu-migration-20260923/revisions.txt
git -C /home/jlu2/jlu-booking rev-parse HEAD >> /root/jlu-migration-20260923/revisions.txt
git -C /home/jlu3/jlu-booking rev-parse HEAD >> /root/jlu-migration-20260923/revisions.txt
git -C /home/jlu4/jlu-booking rev-parse HEAD >> /root/jlu-migration-20260923/revisions.txt
git -C /opt/jlu-booking rev-parse HEAD >> /root/jlu-migration-20260923/revisions.txt
git -C /var/www/jlu-gym-frontend rev-parse HEAD >> /root/jlu-migration-20260923/revisions.txt
du -sh /home/jlu /home/jlu2 /home/jlu3 /home/jlu4 /opt/jlu-booking /var/lib/jlu-booking /etc/jlu-booking /var/www/jlu-gym-frontend > /root/jlu-migration-20260923/sizes.txt
```

Expected timer baseline: `jlu-booking.timer` disabled; timers 2, 3, and 4 enabled. Stop if observed state differs and resolve the discrepancy before proceeding.

- [ ] **Step 5: Check destination identity conflicts**

Run on the destination:

```bash
getent passwd jlu jlu2 jlu3 jlu4 jlu-booking || true
getent group jlu jlu2 jlu3 jlu4 jlu-booking || true
ss -lntup
systemctl --failed --no-pager
df -h /
free -h
```

Expected: none of the five application identities exists, only expected base-image listeners are present, no failed units, and at least 1 GiB free memory plus 10 GiB disk. If a desired numeric UID or GID is already assigned to another identity, stop and choose an explicit remap before copying.

- [ ] **Step 6: Record the preflight checkpoint**

Add the Beijing timestamp, source revisions, timer baseline, size totals, and destination conflict result to the local run record. Do not record private configuration values.

### Task 2: Prepare the destination and temporary transport

**Files:**
- Create: `/swapfile`
- Create: `/root/.ssh/jlu-migration-20260923`
- Modify on source: `/root/.ssh/authorized_keys` with one tagged temporary key

**Interfaces:**
- Consumes: source public IP and source identity table from Task 1.
- Produces: Ubuntu runtime dependencies, 2 GiB swap, Asia/Shanghai timezone, and destination-to-source key authentication.

- [ ] **Step 1: Install destination prerequisites**

Run on the destination:

```bash
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip python3-dev build-essential git rsync sqlite3 nginx curl ca-certificates
```

Expected: every package installation exits zero. Do not install BT Panel or another control panel.

- [ ] **Step 2: Configure time and clock synchronization**

```bash
timedatectl set-timezone Asia/Shanghai
timedatectl set-ntp true
timedatectl status
```

Expected: timezone `Asia/Shanghai` and synchronized system clock.

- [ ] **Step 3: Add 2 GiB swap idempotently**

```bash
test -f /swapfile || fallocate -l 2G /swapfile
chmod 0600 /swapfile
file /swapfile | grep -q 'swap file' || mkswap /swapfile
swapon --show | grep -q '^/swapfile' || swapon /swapfile
grep -q '^/swapfile ' /etc/fstab || printf '%s\n' '/swapfile none swap sw 0 0' >> /etc/fstab
free -h
```

Expected: approximately 2 GiB swap is active and `/etc/fstab` contains exactly one `/swapfile` line.

- [ ] **Step 4: Generate a dedicated migration key on the destination**

```bash
ssh-keygen -t ed25519 -N '' -C 'jlu-migration-20260923' -f /root/.ssh/jlu-migration-20260923
chmod 0600 /root/.ssh/jlu-migration-20260923
cat /root/.ssh/jlu-migration-20260923.pub
```

Copy only the printed public key to the source. Prefix the source authorized-key entry with:

```text
from="39.105.84.29",no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty
```

- [ ] **Step 5: Verify transport without transmitting private data**

Run on the destination:

```bash
ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes root@47.114.47.118 'hostname; date --iso-8601=seconds'
```

Expected: the source hostname and Beijing timestamp, with no password prompt.

- [ ] **Step 6: Test the source-IP restriction**

Inspect the new source `authorized_keys` line and confirm it contains exactly one `jlu-migration-20260923` tag and `from="39.105.84.29"`. Record the successful transport check.

### Task 3: Restore identities, code, units, and administration commands

**Files:**
- Create: five destination users/groups from the recorded source identity table
- Create: application paths and booking systemd units
- Create: `/usr/local/bin` scripts and symlinks listed in the file map

**Interfaces:**
- Consumes: temporary SSH key from Task 2 and UID/GID manifest from Task 1.
- Produces: code and service definitions with every scheduler disabled.

- [ ] **Step 1: Create destination groups and users with recorded IDs**

The source identities are `jlu-booking:998`, `jlu:1001`, `jlu2:1002`, `jlu3:1003`, and `jlu4:1004`. First prove that these numeric IDs are free, then create them exactly:

```bash
getent passwd 998 1001 1002 1003 1004 || true
getent group 998 1001 1002 1003 1004 || true
groupadd --gid 998 jlu-booking
useradd --uid 998 --gid 998 --home-dir /var/lib/jlu-booking --no-create-home --shell /usr/sbin/nologin jlu-booking
groupadd --gid 1001 jlu
useradd --uid 1001 --gid 1001 --home-dir /home/jlu --create-home --shell /bin/bash jlu
groupadd --gid 1002 jlu2
useradd --uid 1002 --gid 1002 --home-dir /home/jlu2 --create-home --shell /bin/bash jlu2
groupadd --gid 1003 jlu3
useradd --uid 1003 --gid 1003 --home-dir /home/jlu3 --create-home --shell /bin/bash jlu3
groupadd --gid 1004 jlu4
useradd --uid 1004 --gid 1004 --home-dir /home/jlu4 --create-home --shell /bin/bash jlu4
getent passwd jlu-booking jlu jlu2 jlu3 jlu4
getent group jlu-booking jlu jlu2 jlu3 jlu4
```

Stop before `groupadd` if any numeric ID belongs to a different identity. Expected homes and shells are exactly those shown above.

- [ ] **Step 2: Pull application code without old virtual environments or caches**

Run on the destination for each source tree:

```bash
rsync -aHAX --numeric-ids --delete --exclude='.venv/' --exclude='__pycache__/' -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu/jlu-booking/ /home/jlu/jlu-booking/
rsync -aHAX --numeric-ids --delete --exclude='.venv/' --exclude='__pycache__/' -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu2/jlu-booking/ /home/jlu2/jlu-booking/
rsync -aHAX --numeric-ids --delete --exclude='.venv/' --exclude='__pycache__/' -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu3/jlu-booking/ /home/jlu3/jlu-booking/
rsync -aHAX --numeric-ids --delete --exclude='.venv/' --exclude='__pycache__/' -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu4/jlu-booking/ /home/jlu4/jlu-booking/
rsync -aHAX --numeric-ids --delete --exclude='.venv/' --exclude='__pycache__/' -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/opt/jlu-booking/ /opt/jlu-booking/
rsync -aHAX --numeric-ids --delete -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/var/www/jlu-gym-frontend/ /var/www/jlu-gym-frontend/
```

- [ ] **Step 3: Pull booking unit files and custom commands**

```bash
rsync -a -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' 'root@47.114.47.118:/etc/systemd/system/jlu-booking*.service' /etc/systemd/system/
rsync -a -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' 'root@47.114.47.118:/etc/systemd/system/jlu-booking*.timer' /etc/systemd/system/
rsync -a -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/usr/local/bin/jlu-set-config /usr/local/bin/
rsync -a -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/usr/local/bin/jlu-status /usr/local/bin/
rsync -a -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/usr/local/bin/server-status /usr/local/bin/
ln -sfn /usr/local/bin/jlu-set-config /usr/local/bin/wang-set
ln -sfn /usr/local/bin/jlu-set-config /usr/local/bin/xia-set
ln -sfn /usr/local/bin/jlu-set-config /usr/local/bin/hong-set
ln -sfn /usr/local/bin/jlu-set-config /usr/local/bin/wangkeji-set
```

- [ ] **Step 4: Force every destination scheduler disabled before loading units**

```bash
systemctl disable jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer || true
systemctl disable jlu-booking-scheduler.service || true
systemctl daemon-reload
systemctl is-enabled jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer jlu-booking-scheduler.service
```

Expected: every listed scheduler reports `disabled`; no unit is started.

- [ ] **Step 5: Validate revisions and shortcut mappings**

```bash
git -C /home/jlu/jlu-booking rev-parse HEAD
git -C /home/jlu2/jlu-booking rev-parse HEAD
git -C /home/jlu3/jlu-booking rev-parse HEAD
git -C /home/jlu4/jlu-booking rev-parse HEAD
git -C /opt/jlu-booking rev-parse HEAD
git -C /var/www/jlu-gym-frontend rev-parse HEAD
readlink -f /usr/local/bin/wang-set
readlink -f /usr/local/bin/xia-set
readlink -f /usr/local/bin/hong-set
readlink -f /usr/local/bin/wangkeji-set
```

Expected: revisions match Task 1 and every symlink resolves to `/usr/local/bin/jlu-set-config`.

### Task 4: Restore private data and rebuild Python environments

**Files:**
- Create: per-user `.config/jlu-booking` and `.local/state/jlu-booking`
- Create: `/etc/jlu-booking`, `/var/lib/jlu-booking`, and rebuilt `.venv` directories

**Interfaces:**
- Consumes: restored source trees and user identities from Task 3.
- Produces: runnable application installations and a consistent staging web database, with schedulers still disabled.

- [ ] **Step 1: Pull each legacy user's private runtime data**

Run these explicit destination pulls:

```bash
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu/.config/jlu-booking/ /home/jlu/.config/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu/.local/state/jlu-booking/ /home/jlu/.local/state/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu2/.config/jlu-booking/ /home/jlu2/.config/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu2/.local/state/jlu-booking/ /home/jlu2/.local/state/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu3/.config/jlu-booking/ /home/jlu3/.config/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu3/.local/state/jlu-booking/ /home/jlu3/.local/state/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu4/.config/jlu-booking/ /home/jlu4/.config/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu4/.local/state/jlu-booking/ /home/jlu4/.local/state/jlu-booking/
```

- [ ] **Step 2: Create a consistent staging SQLite backup on the source**

Run on the source:

```bash
python3 - <<'PY'
import sqlite3
from pathlib import Path

source = sqlite3.connect('/var/lib/jlu-booking/web.sqlite3')
target_path = Path('/root/jlu-migration-20260923/web-staging.sqlite3')
target = sqlite3.connect(target_path)
with target:
    source.backup(target)
target.close()
source.close()
target_path.chmod(0o600)
PY
sqlite3 /root/jlu-migration-20260923/web-staging.sqlite3 'PRAGMA integrity_check;'
sha256sum /root/jlu-migration-20260923/web-staging.sqlite3 > /root/jlu-migration-20260923/web-staging.sqlite3.sha256
```

Expected: `ok` from `PRAGMA integrity_check`.

- [ ] **Step 3: Pull web configuration, keys, database backup, and runtime files**

```bash
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/etc/jlu-booking/ /etc/jlu-booking/
rsync -aHAX --numeric-ids --exclude='web.sqlite3' --exclude='web.sqlite3-wal' --exclude='web.sqlite3-shm' -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/var/lib/jlu-booking/ /var/lib/jlu-booking/
rsync -a -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/root/jlu-migration-20260923/web-staging.sqlite3 /var/lib/jlu-booking/web.sqlite3
```

Restore ownership from the source identity manifest and enforce `0600` on key files and the database.

- [ ] **Step 4: Rebuild all five Python virtual environments**

Run each legacy installation as its owning user:

```bash
sudo -u jlu python3 -m venv /home/jlu/jlu-booking/.venv
sudo -u jlu /home/jlu/jlu-booking/.venv/bin/python -m pip install --upgrade pip
sudo -u jlu /home/jlu/jlu-booking/.venv/bin/python -m pip install /home/jlu/jlu-booking
sudo -u jlu2 python3 -m venv /home/jlu2/jlu-booking/.venv
sudo -u jlu2 /home/jlu2/jlu-booking/.venv/bin/python -m pip install --upgrade pip
sudo -u jlu2 /home/jlu2/jlu-booking/.venv/bin/python -m pip install /home/jlu2/jlu-booking
sudo -u jlu3 python3 -m venv /home/jlu3/jlu-booking/.venv
sudo -u jlu3 /home/jlu3/jlu-booking/.venv/bin/python -m pip install --upgrade pip
sudo -u jlu3 /home/jlu3/jlu-booking/.venv/bin/python -m pip install /home/jlu3/jlu-booking
sudo -u jlu4 python3 -m venv /home/jlu4/jlu-booking/.venv
sudo -u jlu4 /home/jlu4/jlu-booking/.venv/bin/python -m pip install --upgrade pip
sudo -u jlu4 /home/jlu4/jlu-booking/.venv/bin/python -m pip install /home/jlu4/jlu-booking
```

Build the web environment separately:

```bash
python3 -m venv /opt/jlu-booking/.venv
/opt/jlu-booking/.venv/bin/python -m pip install --upgrade pip
/opt/jlu-booking/.venv/bin/python -m pip install '/opt/jlu-booking[web,dev]'
```

- [ ] **Step 5: Verify private ownership and mode without showing contents**

```bash
chown root:jlu-booking /etc/jlu-booking
chmod 0750 /etc/jlu-booking
chown -R jlu-booking:jlu-booking /etc/jlu-booking/secrets /var/lib/jlu-booking
chmod 0700 /etc/jlu-booking/secrets
chmod 0600 /etc/jlu-booking/secrets/*.key /var/lib/jlu-booking/web.sqlite3
chown root:jlu-booking /etc/jlu-booking/web.env
chmod 0640 /etc/jlu-booking/web.env
namei -l /etc/jlu-booking/secrets/token.key
namei -l /etc/jlu-booking/secrets/blind.key
stat -c '%U:%G %a %n' /home/jlu/.config/jlu-booking/token /home/jlu2/.config/jlu-booking/token /home/jlu3/.config/jlu-booking/token /home/jlu4/.config/jlu-booking/token
stat -c '%U:%G %a %n' /etc/jlu-booking/secrets/*.key /etc/jlu-booking/web.env /var/lib/jlu-booking/web.sqlite3
```

Expected: no world-readable token, key, environment, or database file; ownership matches the relevant service identity.

### Task 5: Perform offline and loopback-only verification

**Files:**
- Create or modify: destination Nginx site files copied from the source, kept out of public use until validation

**Interfaces:**
- Consumes: restored applications and data from Task 4.
- Produces: a signed-off local-verification gate; Task 6 must not start without it.

- [ ] **Step 1: Run unit-file validation while all schedulers remain disabled**

```bash
systemd-analyze verify /etc/systemd/system/jlu-booking.service /etc/systemd/system/jlu-booking2.service /etc/systemd/system/jlu-booking3.service /etc/systemd/system/jlu-booking4.service /etc/systemd/system/jlu-booking-web.service /etc/systemd/system/jlu-booking-scheduler.service
systemctl is-enabled jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer jlu-booking-scheduler.service
```

Expected: unit validation has no fatal error and all schedulers remain disabled.

- [ ] **Step 2: Run the full offline web repository checks**

```bash
cd /opt/jlu-booking
.venv/bin/python -m pytest
.venv/bin/python tools/privacy_check.py
.venv/bin/python -m compileall -q jlu_booking
git diff --check
git status --short
```

Expected: tests, privacy check, compileall, and diff check pass. The repository remains at the recorded committed revision. Stop immediately if a test attempts external network access.

- [ ] **Step 3: Validate each legacy installation without invoking booking entry points**

Run only imports and compilation:

```bash
/home/jlu/jlu-booking/.venv/bin/python -c 'import jlu_booking; print(jlu_booking.__file__)'
/home/jlu/jlu-booking/.venv/bin/python -m compileall -q /home/jlu/jlu-booking/jlu_booking
/home/jlu2/jlu-booking/.venv/bin/python -c 'import jlu_booking; print(jlu_booking.__file__)'
/home/jlu2/jlu-booking/.venv/bin/python -m compileall -q /home/jlu2/jlu-booking/jlu_booking
/home/jlu3/jlu-booking/.venv/bin/python -c 'import jlu_booking; print(jlu_booking.__file__)'
/home/jlu3/jlu-booking/.venv/bin/python -m compileall -q /home/jlu3/jlu-booking/jlu_booking
/home/jlu4/jlu-booking/.venv/bin/python -c 'import jlu_booking; print(jlu_booking.__file__)'
/home/jlu4/jlu-booking/.venv/bin/python -m compileall -q /home/jlu4/jlu-booking/jlu_booking
```

Expected: imports and compilation pass. Do not run `jlu-booking-auto`.

- [ ] **Step 4: Validate database integrity and non-secret counts**

```bash
sqlite3 /var/lib/jlu-booking/web.sqlite3 'PRAGMA integrity_check;'
sqlite3 /var/lib/jlu-booking/web.sqlite3 "SELECT 'users',count(*) FROM users UNION ALL SELECT 'companions',count(*) FROM companions UNION ALL SELECT 'booking_tasks',count(*) FROM booking_tasks UNION ALL SELECT 'task_runs',count(*) FROM task_runs UNION ALL SELECT 'audit_events',count(*) FROM audit_events UNION ALL SELECT 'request_throttles',count(*) FROM request_throttles UNION ALL SELECT 'user_credentials',count(*) FROM user_credentials UNION ALL SELECT 'web_sessions',count(*) FROM web_sessions UNION ALL SELECT 'schema_migrations',count(*) FROM schema_migrations;"
```

Run the same aggregate query against the source staging backup and compare all nine counts. Do not select usernames, tokens, companion data, password hashes, or encrypted values.

- [ ] **Step 5: Validate shortcut behavior without mutation**

```bash
command -v wang-set xia-set hong-set wangkeji-set jlu-status server-status
readlink -f /usr/local/bin/wang-set /usr/local/bin/xia-set /usr/local/bin/hong-set /usr/local/bin/wangkeji-set
grep -E 'wang-set|xia-set|hong-set|wangkeji-set' /usr/local/bin/jlu-set-config
```

Expected mappings: `wang-set→jlu`, `xia-set→jlu2`, `hong-set→jlu3`, `wangkeji-set→jlu4`. Do not execute the interactive configuration commands during verification.

- [ ] **Step 6: Install and validate Nginx locally**

Create a temporary HTTP-only `gym` virtual host without copying the source Certbot paths:

```bash
cat > /etc/nginx/sites-available/jlu-gym-frontend <<'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name gym.meiyh9924.xyz;
    root /var/www/jlu-gym-frontend/dist;
    index index.html;
    location / {
        try_files $uri $uri/ /index.html;
    }
    location /assets/ {
        try_files $uri =404;
        expires 7d;
        add_header Cache-Control "public, max-age=604800, immutable";
    }
}
EOF
```

Create the booking reverse proxy:

```bash
cat > /etc/nginx/sites-available/jlu-booking-web <<'EOF'
server {
    listen 80;
    listen [::]:80;
    server_name booking.meiyh9924.xyz;
    client_max_body_size 1m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }
}
EOF
rm -f /etc/nginx/sites-enabled/default
ln -sfn /etc/nginx/sites-available/jlu-gym-frontend /etc/nginx/sites-enabled/jlu-gym-frontend
ln -sfn /etc/nginx/sites-available/jlu-booking-web /etc/nginx/sites-enabled/jlu-booking-web
nginx -t
systemctl enable --now nginx
curl --resolve gym.meiyh9924.xyz:80:127.0.0.1 -I http://gym.meiyh9924.xyz/
```

Start only `jlu-booking-web.service`, confirm it listens on `127.0.0.1:8000`, then run the booking Host-header check:

```bash
systemctl start jlu-booking-web.service
ss -lntp | grep '127.0.0.1:8000'
curl --resolve booking.meiyh9924.xyz:80:127.0.0.1 -I http://booking.meiyh9924.xyz/login
```

Expected: the static site and login route respond locally, port 8000 is not bound to a public address, and the booking scheduler remains disabled.

- [ ] **Step 7: Record the verification gate**

Record exact pass/fail status for tests, privacy check, compileall, SQLite integrity, unit validation, permissions, shortcuts, timer-disabled state, loopback binding, and both local HTTP checks. Do not proceed if any item fails.

### Task 6: Freeze the source and perform the final delta sync

**Files:**
- Replace on destination: `/var/lib/jlu-booking/web.sqlite3` with the final consistent backup
- Update on destination: legacy logs/state and web runtime state

**Interfaces:**
- Consumes: successful Task 5 verification.
- Produces: final source snapshot plus proof that no source scheduler or booking process is active.

- [ ] **Step 1: Reconfirm the safe window and capture service state**

Run on both hosts:

```bash
TZ=Asia/Shanghai date --iso-8601=seconds
pgrep -af 'jlu-booking|jlu_booking' || true
```

Proceed only outside 07:27–07:33 with no active legacy booking process.

- [ ] **Step 2: Freeze all source scheduling before stopping web writes**

Run on the source:

```bash
systemctl disable --now jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer
systemctl disable jlu-booking.service jlu-booking2.service jlu-booking3.service jlu-booking4.service
systemctl disable --now jlu-booking-scheduler.service
systemctl stop jlu-booking-web.service
systemctl disable jlu-booking-web.service
systemctl is-active jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer jlu-booking-scheduler.service jlu-booking-web.service
systemctl is-enabled jlu-booking.service jlu-booking2.service jlu-booking3.service jlu-booking4.service jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer jlu-booking-scheduler.service jlu-booking-web.service
pgrep -af 'jlu-booking|jlu_booking' || true
```

Expected: every listed source booking unit is inactive and disabled, and no booking process remains. This is the point at which source scheduling is intentionally unavailable and remains unavailable unless rollback is explicitly selected.

- [ ] **Step 3: Create the final source database backup**

```bash
python3 - <<'PY'
import sqlite3
from pathlib import Path

source = sqlite3.connect('/var/lib/jlu-booking/web.sqlite3')
target_path = Path('/root/jlu-migration-20260923/web-final.sqlite3')
target = sqlite3.connect(target_path)
with target:
    source.backup(target)
target.close()
source.close()
target_path.chmod(0o600)
PY
sqlite3 /root/jlu-migration-20260923/web-final.sqlite3 'PRAGMA integrity_check;'
sha256sum /root/jlu-migration-20260923/web-final.sqlite3 > /root/jlu-migration-20260923/web-final.sqlite3.sha256
```

Expected: `ok` and a recorded checksum.

- [ ] **Step 4: Stop destination web and perform final delta pulls**

Stop the destination web service and run the final explicit pulls:

```bash
systemctl stop jlu-booking-web.service
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu/.config/jlu-booking/ /home/jlu/.config/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu/.local/state/jlu-booking/ /home/jlu/.local/state/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu2/.config/jlu-booking/ /home/jlu2/.config/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu2/.local/state/jlu-booking/ /home/jlu2/.local/state/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu3/.config/jlu-booking/ /home/jlu3/.config/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu3/.local/state/jlu-booking/ /home/jlu3/.local/state/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu4/.config/jlu-booking/ /home/jlu4/.config/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/home/jlu4/.local/state/jlu-booking/ /home/jlu4/.local/state/jlu-booking/
rsync -aHAX --numeric-ids -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/etc/jlu-booking/ /etc/jlu-booking/
rsync -aHAX --numeric-ids --exclude='web.sqlite3' --exclude='web.sqlite3-wal' --exclude='web.sqlite3-shm' -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/var/lib/jlu-booking/ /var/lib/jlu-booking/
rsync -a -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/root/jlu-migration-20260923/web-final.sqlite3 /var/lib/jlu-booking/web.sqlite3
rsync -aHAX --numeric-ids --delete -e 'ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes' root@47.114.47.118:/var/www/jlu-gym-frontend/ /var/www/jlu-gym-frontend/
chown -R jlu-booking:jlu-booking /var/lib/jlu-booking /etc/jlu-booking/secrets
chown root:jlu-booking /etc/jlu-booking /etc/jlu-booking/web.env
chmod 0600 /var/lib/jlu-booking/web.sqlite3 /etc/jlu-booking/secrets/*.key
chmod 0640 /etc/jlu-booking/web.env
```

- [ ] **Step 5: Validate final equality and ownership**

```bash
sqlite3 /var/lib/jlu-booking/web.sqlite3 'PRAGMA integrity_check;'
sha256sum /var/lib/jlu-booking/web.sqlite3
stat -c '%U:%G %a %n' /var/lib/jlu-booking/web.sqlite3 /etc/jlu-booking/secrets/*.key /etc/jlu-booking/web.env
systemctl is-active jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer jlu-booking-scheduler.service
```

Expected: destination database checksum equals the source final checksum, integrity is `ok`, private ownership is correct, and all destination schedulers are still inactive.

- [ ] **Step 6: Record the rollback boundary**

Record the final checksum, final sync timestamp, source-inactive proof, and destination-inactive proof. From this point forward, rollback must disable destination scheduling before source scheduling is restored.

### Task 7: Start destination services and cut over routing

**Files:**
- Modify: destination systemd enablement state
- Modify externally after verification: Alibaba Cloud DNS A records

**Interfaces:**
- Consumes: final verified snapshot and source-inactive proof from Task 6.
- Produces: active destination application with exact scheduler state and optional DNS cutover.

- [ ] **Step 1: Start the destination web service and re-run local health checks**

```bash
systemctl enable --now jlu-booking-web.service
systemctl status jlu-booking-web.service --no-pager -l
curl --resolve booking.meiyh9924.xyz:80:127.0.0.1 -I http://booking.meiyh9924.xyz/login
curl --resolve gym.meiyh9924.xyz:80:127.0.0.1 -I http://gym.meiyh9924.xyz/
```

Expected: Web and static-site checks succeed before any scheduler is enabled.

- [ ] **Step 2: Restore the exact legacy timer state**

```bash
systemctl disable jlu-booking.timer
systemctl enable --now jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer
systemctl list-timers 'jlu-booking*.timer' --all --no-pager
```

Expected: primary timer disabled/inactive; timers 2, 3, and 4 enabled/active with 07:27 Asia/Shanghai next-run semantics.

- [ ] **Step 3: Enable the web scheduler only after a second source-inactive check**

From the destination, query the source:

```bash
ssh -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes root@47.114.47.118 'systemctl is-active jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer jlu-booking-scheduler.service; pgrep -af "jlu-booking|jlu_booking" || true'
```

If and only if all source schedulers are inactive and no booking process exists:

```bash
systemctl enable --now jlu-booking-scheduler.service
systemctl status jlu-booking-scheduler.service --no-pager -l
```

- [ ] **Step 4: Observe startup without triggering work**

```bash
journalctl -u jlu-booking-web.service -u jlu-booking-scheduler.service --since '-10 minutes' --no-pager
systemctl --failed --no-pager
ss -lntup
```

Expected: no restart loop, no decryption/database error, no scheduler duplicate-run warning, and no unexpected public listener.

- [ ] **Step 5: Decide the public-routing gate from ICP status**

If ICP remains incomplete, keep both public DNS records unchanged and use SSH-tunnel/local checks only. Record `DNS deferred pending ICP`.

If ICP is approved and the new host is authorized for the domain, change the `gym` and `booking` A records from the source IP to `39.105.84.29`, then verify with multiple resolvers:

```bash
dig +short gym.meiyh9924.xyz A @223.5.5.5
dig +short booking.meiyh9924.xyz A @223.5.5.5
dig +short gym.meiyh9924.xyz A @1.1.1.1
dig +short booking.meiyh9924.xyz A @1.1.1.1
```

Expected after cutover: all answers converge on `39.105.84.29`. Do not issue certificates until the DNS and HTTP validation path both reach the destination.

- [ ] **Step 6: Configure TLS only when eligible**

After ICP, DNS, ports 80/443, and HTTP Host-header routing are all correct, run:

```bash
apt-get install -y certbot python3-certbot-nginx
certbot --nginx -d gym.meiyh9924.xyz
certbot --nginx -d booking.meiyh9924.xyz
nginx -t
certbot renew --dry-run
```

If any eligibility condition is false, record `TLS deferred pending ICP/DNS` and do not run these commands.

### Task 8: Close out access, security, and rollback readiness

**Files:**
- Modify: source `/root/.ssh/authorized_keys` to remove the tagged temporary key
- Delete: destination `/root/.ssh/jlu-migration-20260923*`
- Modify: `docs/operations/2026-09-23-server-migration-record.md`

**Interfaces:**
- Consumes: stable destination services and routing decision from Task 7.
- Produces: auditable migration result, removed temporary trust, and a 48-hour rollback checkpoint.

- [ ] **Step 1: Capture final non-secret service evidence**

Record destination unit enablement/activity, timer next-run display, filesystem totals, Git revisions, SQLite integrity result, local/public health results, and DNS/TLS status. Record the source units as inactive. Do not include journal lines containing user data.

- [ ] **Step 2: Remove temporary cross-server SSH trust**

Run on the source and verify exactly one tagged line is removed:

```bash
grep -c 'jlu-migration-20260923$' /root/.ssh/authorized_keys
sed -i '/jlu-migration-20260923$/d' /root/.ssh/authorized_keys
grep -c 'jlu-migration-20260923$' /root/.ssh/authorized_keys
```

Expected counts: `1`, then `0`. From the destination, verify key authentication now fails, then delete:

```bash
ssh -o BatchMode=yes -i /root/.ssh/jlu-migration-20260923 -o IdentitiesOnly=yes root@47.114.47.118 true
rm -f /root/.ssh/jlu-migration-20260923 /root/.ssh/jlu-migration-20260923.pub
```

Expected: the SSH test is rejected before the local temporary key files are deleted.

Keep the private migration working directories until the 48-hour rollback window ends; they remain mode `0700`.

- [ ] **Step 3: Rotate disclosed credentials and install normal SSH key access**

On the Mac, create a dedicated administration key with an interactive passphrase prompt:

```bash
ssh-keygen -t ed25519 -a 100 -C 'jlu-booking-admin' -f ~/.ssh/jlu-booking-admin
ssh-copy-id -i ~/.ssh/jlu-booking-admin.pub root@39.105.84.29
ssh -i ~/.ssh/jlu-booking-admin root@39.105.84.29 true
```

Keep the original destination session open until the key-authenticated test succeeds. Then change the destination root password interactively with `passwd root`; the user enters the new private value directly and it is never recorded. Rotate the disclosed web administrator password through the application UI.

The source ECS belongs to the user's friend. Do not change its root password unilaterally. Remove the temporary migration key as specified in Step 2, and ask the source owner to rotate their password through their own secure channel.

- [ ] **Step 4: Run final verification**

```bash
systemctl --failed --no-pager
systemctl is-enabled jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer jlu-booking-web.service jlu-booking-scheduler.service
systemctl is-active jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer jlu-booking-web.service jlu-booking-scheduler.service
sqlite3 /var/lib/jlu-booking/web.sqlite3 'PRAGMA integrity_check;'
nginx -t
```

Expected: no failed unit; primary timer disabled/inactive; timers 2–4, web, and scheduler enabled/active; database integrity `ok`; Nginx syntax valid.

- [ ] **Step 5: Commit the run record**

Before committing, scan it for secrets and private data:

```bash
python tools/privacy_check.py
git diff --check
git status --short
git add docs/operations/2026-09-23-server-migration-record.md
git commit -m 'docs: record server migration'
```

Expected: only the non-secret run record is committed.

- [ ] **Step 6: Hold the rollback window**

Keep the source server powered on with its application data intact for at least 48 hours, but leave all source booking timers, booking services, the web scheduler, and the booking web service stopped and disabled. During that period, verify the destination after the next scheduled window without exposing logs or issuing a manual booking. After the user confirms stability, shut down the old ECS and verify in the Alibaba Cloud console that it is stopped. Deleting temporary backups, releasing the old ECS, or deleting source data requires a separate explicit request.

- [ ] **Step 7: Prove the old host cannot resume booking after reboot**

Before shutting it down, run on the source:

```bash
systemctl disable jlu-booking.service jlu-booking2.service jlu-booking3.service jlu-booking4.service
systemctl disable jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer
systemctl disable jlu-booking-web.service jlu-booking-scheduler.service
systemctl is-enabled jlu-booking.service jlu-booking2.service jlu-booking3.service jlu-booking4.service
systemctl is-enabled jlu-booking.timer jlu-booking2.timer jlu-booking3.timer jlu-booking4.timer
systemctl is-enabled jlu-booking-web.service jlu-booking-scheduler.service
```

Expected: every unit reports `disabled`. Record this proof before the old ECS is shut down.

## Rollback procedure

Use this procedure if destination health, data integrity, scheduling, or routing fails after Task 6:

1. Disable and stop all destination timers and `jlu-booking-scheduler.service`.
2. Confirm with `pgrep` that no destination booking process remains.
3. If DNS changed, restore both A records to the source and confirm resolver convergence.
4. Restore source service state exactly: keep `jlu-booking.timer` disabled, enable timers 2–4, enable the source web service and web scheduler.
5. Confirm no destination scheduler is active before enabling the source scheduler.
6. Preserve the failed destination state and logs for diagnosis; do not overwrite the source with destination data.
7. Record the rollback timestamp and reason in the migration record.
