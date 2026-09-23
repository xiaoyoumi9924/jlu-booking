# JLU Booking Server Migration Record

## Preflight

- Checked at `2026-09-23T11:18:01+08:00`, outside the 07:27–07:33 booking window.
- No legacy `jlu-booking-auto` process was running on either host. The source web scheduler remained active for the staging phase, as designed.
- Source timer baseline: primary timer disabled/inactive; timers 2–4 enabled/active; web and web scheduler enabled/active.
- Source revisions, in account order: `2540c742`, `15e5c57`, `15e5c57`, `15e5c57`; web `ef5babe`; static site `31e539a`.
- Source sizes: user homes 121/51/49/45 MiB; web checkout 71 MiB; web runtime 256 KiB; web configuration 20 KiB; static site 110 MiB.
- Destination had no application identities, failed units, or application listeners; it had 35 GiB disk available and 1.2 GiB memory available.
- Destination UID/GID 998 is reserved by `systemd-network`. The web service account will use a destination-native system UID/GID, followed by explicit ownership normalization. Legacy account IDs 1001–1004 are free and will be preserved.
- Private migration working directories were created with mode `0700` on both hosts.

## Initial sync

- Destination prerequisites installed successfully on Ubuntu 24.04; BT Panel was not installed.
- Destination timezone is `Asia/Shanghai`; NTP reports synchronized.
- A 2 GiB `/swapfile` is active and persisted in `/etc/fstab`.
- Package installation briefly started default Nginx; it was immediately stopped and disabled pending validated site configuration.
- A temporary ED25519 migration key was created on the destination and restricted on the source to `39.105.84.29` with forwarding and PTY disabled.
- Destination-to-source key authentication succeeded at `2026-09-23T11:30:09+08:00` without a password prompt.
- Destination identities created: legacy users retain UID/GID 1001–1004; `jlu-booking` uses destination-native UID 999 and GID 988 because 998 belongs to `systemd-network`.
- All five application checkouts and the complete static-site tree were copied without source virtual environments or Python caches.
- Destination revisions match the source: `2540c742`, three copies of `15e5c57`, web `ef5babe`, and static site `31e539a`.
- Six booking-related service files, four timer files, three administration scripts, and four shortcut symlinks were restored.
- All destination booking timers and the web scheduler are disabled and inactive during staging.
- Shortcut symlinks `wang-set`, `xia-set`, `hong-set`, and `wangkeji-set` all resolve to `/usr/local/bin/jlu-set-config`.
- Four legacy private configurations, tokens, logs, and success-state directories were copied to their matching users.
- Staging SQLite backup passed `PRAGMA integrity_check` on both hosts; source and destination SHA-256 matched: `d5810d9ab6b347e17ed8721da86f6f8aeedf263e5d9a6211e64d71546a7fa1aa`.
- Five Python virtual environments were rebuilt successfully using the destination Python 3.12 runtime.
- All four private tokens, both web keys, and the destination SQLite database are mode `0600`; `web.env` is mode `0640` and restricted to the web service group.

## Offline verification

- Booking systemd units passed `systemd-analyze verify` with exit code 0. Its only warnings concerned the destination image's unrelated `cloudmonitor.service`.
- Destination full repository suite: **327 passed, 2 dependency deprecation warnings** after installing Ubuntu `python3-tk` for GUI test collection. The first run failed to collect three GUI tests because that system package was absent; the rerun passed.
- Repository privacy check, Python `compileall`, and `git diff --check` passed. The web checkout is at committed revision `ef5babe` with no worktree changes.
- All four legacy checkouts imported from their own service working directories and compiled successfully.
- Destination SQLite `PRAGMA integrity_check` returned `ok`; all nine aggregate table counts match the source staging backup: users 1, companions 0, booking tasks 0, task runs 0, audit events 0, request throttles 2, user credentials 1, web sessions 2, schema migrations 1.
- A read-only local check under the destination web service account successfully decrypted the one migrated user credential and verified its blind index against the decrypted value. No token was printed or sent to the JLU API; there were no companion rows to decrypt.
- All six custom commands resolve, and the four configuration commands map to their original Unix users and Chinese display names.
- Nginx syntax passed. Both virtual hosts are bound to `127.0.0.1:8080` during staging. The static `gym` page returned HTTP 200.
- Web service started on `127.0.0.1:8000`; booking login GET through the local Nginx host returned HTTP 200. An immediate HEAD request during startup returned 502 before Uvicorn began listening; a later HEAD returned 405 because `/login` only permits GET.
- All four destination booking timers and the web scheduler remain disabled and inactive. Web and Nginx are active only for loopback validation.
- **Local verification gate: passed.**

## Final cutover

- At `2026-09-23T12:07:41+08:00`, neither host had an active legacy booking process; source web scheduler was the only active booking scheduler.
- Source timer units 1–4, legacy booking services 1–4, web scheduler, and web service were stopped and disabled. A second check found all six scheduled/web units inactive and disabled, with no booking process.
- Final source SQLite online backup passed integrity check; final source and destination SHA-256 matched: `d5810d9ab6b347e17ed8721da86f6f8aeedf263e5d9a6211e64d71546a7fa1aa`.
- All four legacy private directories, web configuration and runtime, final database, and the static site received a final delta sync. Destination WAL/SHM sidecars were removed before replacing the SQLite file.
- Destination database integrity returned `ok`; key, environment, and database ownership and modes were verified again.
- At `2026-09-23T12:11:03+08:00`, all destination booking timers and scheduler were still inactive. This is the verified rollback boundary before destination scheduling starts.
- Destination web service is enabled and active on `127.0.0.1:8000`; local booking login and `gym` GET requests both returned HTTP 200 after startup.
- Timer `Persistent` is false for timer 4 and omitted, thus false by systemd default, for timers 1–3. Enabling the timers did not start a booking process.
- Destination timer state matches the source baseline: primary timer disabled/inactive; timers 2–4 enabled/active. All three next runs are `2026-09-24 07:27:00 CST`.
- Source inactivity was rechecked before enabling the destination web scheduler. Destination scheduler is enabled/active, shows no restart loop, and the migrated database has zero booking tasks.
- Destination has zero failed systemd units. Its only public listening port is SSH 22; Nginx and the web process listen on loopback.

## DNS and TLS

- `gym.meiyh9924.xyz` and `booking.meiyh9924.xyz` still resolve to the source address. An external HTTP check for booking returned `403 Forbidden` from Alibaba Cloud's `Beaver` gateway, consistent with pending ICP access.
- **DNS deferred pending ICP. TLS deferred pending ICP/DNS.** Destination Nginx remains loopback-only and the old ECS remains powered on for rollback; its booking scripts and schedulers remain stopped and disabled.

## Temporary-access removal

- The tagged one-time public key was removed from the source root `authorized_keys`: matching entry count changed from 1 to 0. Authentication with the temporary destination-to-source key was rejected before its private and public key files were deleted from the destination.
- A dedicated Mac-to-destination administration key was installed and verified with noninteractive SSH authentication. Its private key and the two newly generated password files are stored only under the Mac user's `~/.ssh/`, each with mode `0600`; their values are not in this record or Git. The administration key has no passphrase, so the Mac account and its local file permissions protect it until the user adds a passphrase or replaces it.
- The destination root password was rotated after key access succeeded. The destination web administrator password was rotated using the application's password hashing service; its existing sessions were revoked, and the account requires a password change at its next login. The web service restarted and local login GET returned HTTP 200. This password rotation changed the SQLite database after the final migration checksum checkpoint.
- The source ECS belongs to a different account holder. Its root password was not changed; that owner should rotate the previously disclosed credential through their own secure channel.
- The Mac user should move the two locally stored replacement passwords into a password manager and remove those temporary files, and add a passphrase to or replace the administration SSH key when practical.
- Both hosts retain private migration working directories with mode `0700` through the rollback window. Their backup data and the source application files have not been deleted.

## Rollback deadline and source shutdown

- The source-to-destination scheduling boundary was verified at `2026-09-23T12:11:03+08:00`. The earliest end of the required 48-hour source retention period is `2026-09-25T12:11:03+08:00`.
- Final source proof: all four booking services, four booking timers, the web service, and the web scheduler report **disabled** and **inactive**. This prevents their automatic restart after a source reboot unless someone explicitly enables them.
- Final destination proof at `2026-09-23 12:27 CST`: the primary timer is disabled/inactive; timers 2–4, web service, and scheduler are enabled/active. The three next timer runs are `2026-09-24 07:27:00 CST`; there are no failed units. SQLite integrity returned `ok`, Nginx configuration passed, and local booking login GET returned HTTP 200.
- The old ECS is still powered on solely for rollback. Observe the destination after the next scheduled window without manually invoking any real booking API. After at least 48 hours and confirmation that scheduling and data are stable, shut down the old ECS and verify the stopped state in the Alibaba Cloud console. The public `gym` and `booking` domains still point to the old address; their move to this destination, public Nginx listeners, and TLS remain separate work after ICP eligibility is confirmed.
- A rollback after this point must first reconcile destination database writes and legacy success state into a verified source checkpoint. The frozen source database still contains the old administrator password hash and sessions; source web access must remain disabled until its password is rotated and old sessions revoked, or a verified current destination database with those changes is restored. If either gate cannot be met, keep the source disabled and repair the destination.
