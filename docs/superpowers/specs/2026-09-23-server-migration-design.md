# JLU Booking Server Migration Design

**Date:** 2026-09-23  
**Source server:** Alibaba Cloud ECS, Ubuntu 22.04  
**Destination server:** Alibaba Cloud Lightweight Application Server, Ubuntu 24.04  
**Status:** Approved in chat; awaiting written-spec review

## 1. Objective

Move the complete JLU booking workload and the existing `gym` website from the current server to the owner's new server without changing booking behavior, losing user data, exposing credentials, or running the same booking schedule on both hosts.

The migration preserves the existing four command-line booking accounts, the multi-user web application, historical booking state and logs, the static `gym` site, systemd service names, timer enablement state, and the administrator's custom shortcut commands. DNS is changed only after the destination has passed local verification.

The migration does not modify the booking state machine, candidate ordering, CORE timing, failure handling, deadline behavior, or any other booking logic.

## 2. Success criteria

The migration is complete when all of the following are true:

1. The four legacy booking installations exist on the destination with their original Unix users, ownership, private configuration, tokens, state, and logs.
2. The web application uses the migrated SQLite database and the original encryption and blind-index keys, so existing accounts and encrypted user data remain usable.
3. The `gym` static website and the booking web application render correctly through local destination checks.
4. All systemd services load successfully, and the timer enablement state matches the source: the primary legacy timer remains disabled while timers 2, 3, and 4 retain their enabled state at cutover.
5. The web service and scheduler run only when explicitly enabled during cutover.
6. The custom commands keep the same names and behavior:
   - `wang-set`
   - `xia-set`
   - `hong-set`
   - `wangkeji-set`
   - `jlu-status`
   - `server-status`
7. No live JLU booking API is called as part of migration testing.
8. DNS is switched only after the new host passes verification, and the old host remains available for rollback.

## 3. Chosen approach

Use an application-level migration onto a clean Ubuntu 24.04 host.

Reinstall operating-system packages and rebuild Python virtual environments on the destination. Transfer only business applications, configuration, secrets, databases, state, logs, website assets, systemd units, and administrator tools. This avoids copying host-specific networking, SSH host keys, package databases, caches, and Alibaba/BT panel internals from Ubuntu 22.04.

Rejected alternatives:

- **Whole-machine image cloning:** carries obsolete operating-system state, BT panel components, SSH material, and source-host configuration into a different server product and OS release.
- **Broad root-filesystem rsync:** risks replacing destination networking, boot, SSH, and package-managed files and gives no reliable rollback boundary.

## 4. Migration scope

### 4.1 Included

- Legacy Unix users and home directories required by the booking jobs:
  - `jlu`
  - `jlu2`
  - `jlu3`
  - `jlu4`
- Each user's booking repository, private configuration, token, runtime state, and historical logs.
- `/opt/jlu-booking` web-platform checkout at its current committed revision.
- Web runtime data under `/var/lib/jlu-booking`, including a consistent SQLite backup.
- Web secrets and environment configuration under `/etc/jlu-booking`.
- Booking-related systemd services and timers:
  - `jlu-booking.service` and `jlu-booking.timer`
  - `jlu-booking2.service` and `jlu-booking2.timer`
  - `jlu-booking3.service` and `jlu-booking3.timer`
  - `jlu-booking4.service` and `jlu-booking4.timer`
  - `jlu-booking-web.service`
  - `jlu-booking-scheduler.service`
- The `gym` static website working tree under `/var/www/jlu-gym-frontend`, including its existing backup directory.
- Booking and `gym` Nginx configuration, adapted only where the clean destination layout requires it.
- Custom administration scripts and symlinks under `/usr/local/bin`.
- Relevant historical service logs when they are stored in the application users' state directories.

### 4.2 Excluded

- The BT panel itself and its runtime under `/www/server`, including `site_total.service`.
- The BT-managed daily ACME cron job. TLS renewal will use the destination's standard Certbot/systemd mechanism after ICP and public routing permit certificate issuance.
- Source-server SSH host keys, root shell history, caches, temporary files, OS package databases, kernel state, monitoring-agent state, and host-specific network configuration.
- Unrelated system services and cloud-agent units supplied by the source ECS image.

Excluding these components does not remove them from the source server.

## 5. Identity and command preservation

Create the four Unix users on the destination before restoring files. Preserve numeric ownership consistently during the transfer, or remap ownership explicitly if the destination allocates different UIDs/GIDs.

Install `/usr/local/bin/jlu-set-config`, `/usr/local/bin/jlu-status`, and `/usr/local/bin/server-status` with their existing executable permissions. Recreate these symlinks exactly:

| Command | Target | Booking user | Displayed person |
|---|---|---|---|
| `wang-set` | `/usr/local/bin/jlu-set-config` | `jlu` | 王子荣 |
| `xia-set` | `/usr/local/bin/jlu-set-config` | `jlu2` | 夏凯 |
| `hong-set` | `/usr/local/bin/jlu-set-config` | `jlu3` | 洪思珂 |
| `wangkeji-set` | `/usr/local/bin/jlu-set-config` | `jlu4` | 王科技 |

The journal commands continue to use the original service names, so existing terminal snippets remain valid.

## 6. Destination architecture

The destination keeps the current process boundaries:

- Nginx is the only public web entry point after DNS cutover.
- The booking web process listens on loopback only.
- The web scheduler remains a separate systemd service.
- Each legacy booking account runs under its own unprivileged Unix user.
- Tokens, database encryption keys, and environment files remain readable only by their intended user or root.
- A 2 GiB swap file is added to protect the 2 GiB server from short memory spikes.

No container layer or control panel is introduced in this migration.

## 7. Migration sequence

### Phase A: Prepare the destination

1. Record a source inventory and checksums without printing secret contents.
2. Update destination packages and install only required runtime packages.
3. Configure the swap file and basic host settings, including the Asia/Shanghai timezone.
4. Create application users and required directories.
5. Install code and rebuild Python virtual environments from pinned project dependencies.
6. Copy systemd units and custom commands, but keep all booking timers and schedulers disabled.
7. Install Nginx configuration in a disabled or loopback-testable state.

### Phase B: Initial data copy

1. Transfer legacy configurations, tokens, state, logs, and application trees using an encrypted SSH transport.
2. Create a consistent SQLite backup through SQLite's backup mechanism; do not copy a live database file without its transactional state.
3. Transfer the database backup, web secrets, environment file, and static website tree.
4. Restore strict ownership and file permissions.

The source remains active during the initial copy. No source timer state is changed in this phase.

### Phase C: Offline destination verification

1. Verify file manifests, checksums, ownership, and permissions.
2. Run repository test suites, privacy checks, compile checks, Nginx configuration validation, and systemd unit validation.
3. Start the web service only on loopback for local HTTP and database-login checks.
4. Exercise command help/configuration inspection paths that do not call JLU APIs.
5. Confirm all booking timers and the booking scheduler are still disabled.

### Phase D: Final cutover

Perform cutover after the day's booking window has ended and with sufficient time before the next 07:27 scheduled start.

1. Stop and disable the source booking scheduler and all source booking timers.
2. Stop source web writes briefly and create a final consistent SQLite backup.
3. Perform a final delta transfer of state, logs, database, and configuration.
4. Re-run destination checksums, permissions, database integrity, and local health checks.
5. Start destination web services.
6. Restore timer state exactly: keep `jlu-booking.timer` disabled; enable timers 2, 3, and 4.
7. Enable the destination scheduler only after confirming the source scheduler is inactive.
8. Observe service logs for startup errors without triggering a manual booking run.

At no point may the same scheduled booking account be active on both servers.

### Phase E: DNS and TLS

1. Change `gym.meiyh9924.xyz` and `booking.meiyh9924.xyz` DNS records only after destination local verification passes.
2. Keep public routing constrained by the current ICP status.
3. Issue or reinstall certificates only after ICP and HTTP validation allow normal certificate issuance.
4. Validate HTTPS, redirects, security headers, and automatic renewal.

## 8. Data consistency and secret handling

- Secret values must never be printed in command output, committed to Git, placed in migration documentation, or embedded in shell command lines.
- Transfers use SSH. Temporary archives, if needed, are stored with restrictive permissions and deleted after checksum verification.
- SQLite is copied from an explicit backup, followed by `PRAGMA integrity_check` on the destination.
- File permissions for tokens, keys, and environment files are verified against the source before services start.
- Existing web password hashes are migrated as database data; plaintext user passwords are not needed.
- After successful cutover, install key-based SSH authentication and rotate the destination root password and disclosed web administrator password. Because the source ECS belongs to another owner, remove migration access but leave source-root password rotation to that owner rather than changing it unilaterally.

## 9. Failure handling and rollback

Any failed checksum, database integrity error, missing secret, service validation error, or unexpected live API access stops the cutover.

Before DNS cutover, rollback means leaving the source services active and keeping destination schedulers disabled.

After source schedulers have been stopped, rollback follows this order:

1. Disable all destination timers and the destination scheduler.
2. Confirm no destination booking process remains active.
3. Re-enable the source timers and scheduler in their recorded original state.
4. Restore DNS to the source if it had already changed.

The source server and its data remain untouched for at least 48 hours after successful cutover. No deletion or destructive cleanup is part of this migration.

## 10. Verification checklist

Verification must cover:

- Destination OS, disk, memory, swap, timezone, and clock synchronization.
- Required users, directory ownership, and private file permissions.
- Git revision and clean/known working-tree state for each application tree.
- Python dependency installation and imports.
- Project `pytest`, privacy check, and compile checks without real API calls.
- SQLite integrity and expected account counts without exposing sensitive fields.
- Custom command resolution and the four user mappings.
- `systemd-analyze verify` and `systemctl daemon-reload`.
- Exact timer enablement and next-run times.
- Nginx syntax and loopback HTTP health checks.
- Source scheduler inactivity before destination scheduling is enabled.
- DNS resolution and HTTPS only after cutover.

## 11. Operational constraints

- Do not call the real JLU reservation endpoints for testing.
- Do not manually trigger a booking service.
- Do not change existing CORE timing or booking decision logic.
- Do not deploy from an uncommitted application worktree.
- Do not switch DNS before the destination passes local verification.
- Do not remove or repurpose files on the source server during this migration.
