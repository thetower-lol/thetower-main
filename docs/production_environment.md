# Production Environment Reference

Documents the production server configuration: service user, venv, SSH keys, and paths.

## Server

- **Host**: `ubuntu-4gb-hel1-1`
- **OS**: Ubuntu (Linux)
- **Access**: SSH as `root`

## Service User

All thetower services run as the `tower` user:

```
tower:x:996:996::/var/lib/tower:/bin/false
```

- **Home directory**: `/var/lib/tower`
- **Shell**: `/bin/false` (no interactive login)
- **SSH config**: `/var/lib/tower/.ssh/config`

## Python Virtual Environment

- **Path**: `/opt/venv/tower/`
- **Activate**: `source /opt/venv/tower/bin/activate`
- **Python**: `/opt/venv/tower/bin/python3`
- **pip**: `/opt/venv/tower/bin/pip`

The venv is shared by all thetower services (web, bot, workers).

## Data Paths

| Purpose               | Path                               |
| --------------------- | ---------------------------------- |
| Database              | `/data/tower.sqlite3`              |
| Uploads (CSV)         | `/data/uploads/`                   |
| Static files          | `/data/static/`                    |
| Bot config DB         | `/data/discord/bot-config.sqlite3` |
| Bot data files (JSON) | `/data/discord`                    |
| Bot socket            | `/run/discord-bot/config.sock`     |
| Streamlit pages       | `/opt/thetower/`                   |
| Service files         | `/etc/systemd/system/`             |
| Service file backups  | `/data/services/`                  |

## SSH Keys

All deploy keys and SSH config live in `/var/lib/tower/.ssh/` (the tower user's home `.ssh`).

### Current Deploy Keys

| Package                   | Key file                          | SSH alias                       |
| ------------------------- | --------------------------------- | ------------------------------- |
| thetower-bcs              | `thetower_bcs_deploy`             | `github-tower-thetower-bcs`     |
| thetower-tourney-reminder | `thetower_tourneyreminder_deploy` | `github-tower-tourney-reminder` |
| thetower-managed-polls    | `thetower_managed_polls_deploy`   | `github-tower-managed-polls`    |
| thetower-bot              | `thetower_bot_deploy`             | `github-tower-thetower-bot`     |
| thetower (main)           | `thetower_core_deploy`            | _(public repo, not needed)_     |

### SSH Config (`/var/lib/tower/.ssh/config`)

```
Host github-tower-thetower-bcs
    HostName github.com
    User git
    IdentityFile ~/.ssh/thetower_bcs_deploy
    IdentitiesOnly yes
    StrictHostKeyChecking no

Host github-tower-tourney-reminder
    HostName github.com
    User git
    IdentityFile ~/.ssh/thetower_tourneyreminder_deploy
    IdentitiesOnly yes
    StrictHostKeyChecking no

Host github-tower-managed-polls
    HostName github.com
    User git
    IdentityFile ~/.ssh/thetower_managed_polls_deploy
    IdentitiesOnly yes
    StrictHostKeyChecking no

Host github-tower-thetower-bot
    HostName github.com
    User git
    IdentityFile ~/.ssh/thetower_bot_deploy
    IdentitiesOnly yes
    StrictHostKeyChecking no
```

> **Note**: Root also has copies of these aliases in `/root/.ssh/config` for running
> `pip install` commands as root. Both must be kept in sync when adding new packages.

## Why the tower User Needs Its Own SSH Config

Services run under the `tower` user via systemd (`User=tower`). When the Codebase Status
page runs `git ls-remote` to check for package updates, it runs as `tower` — which reads
`/var/lib/tower/.ssh/config`, not `/root/.ssh/config`. Both locations need the Host aliases
for private package update checking to work.

## Installing/Updating Private Packages

Always activate the venv first, then use the SSH alias URL:

```bash
source /opt/venv/tower/bin/activate

# Install/update a private package
pip install --force-reinstall --no-deps "git+ssh://git@github-tower-<name>/owner/repo.git"
```

See [private_cog_deployment.md](private_cog_deployment.md) for full setup instructions.

## Systemd Services

Services are defined in `/etc/systemd/system/`. All run as `User=tower Group=tower`.

| Service file                          | Purpose                       |
| ------------------------------------- | ----------------------------- |
| `tower-public_site.service`           | Public Streamlit site         |
| `tower-admin_site.service`            | Admin Streamlit site          |
| `tower-hidden_site.service`           | Hidden (staff) Streamlit site |
| `discord_bot.service`                 | Unified Discord bot           |
| `tower-bot_site.service`              | Bot web UI (bot.thetower.lol) |
| `import_results.service`              | CSV result importer           |
| `get_results.service`                 | Results fetcher               |
| `get_live_results.service`            | Live results fetcher          |
| `tower-recalc_worker.service`         | Recalculation worker          |
| `generate_live_bracket_cache.service` | Live bracket cache generator  |

Common commands:

```bash
systemctl restart tower-hidden_site.service
systemctl status discord_bot.service
journalctl -u discord_bot.service -f
systemctl daemon-reload  # after editing a service file
```

## Environment Variables

### discord_bot.service

| Variable                 | Value / Notes                                       |
| ------------------------ | --------------------------------------------------- |
| `DJANGO_SETTINGS_MODULE` | `thetower.backend.towerdb.settings`                 |
| `DISCORD_TOKEN`          | Bot token (secret)                                  |
| `DISCORD_APPLICATION_ID` | Bot application ID                                  |
| `DISCORD_BOT_CONFIG`     | `/data` — config DB and data file directory         |
| `BOT_SOCKET_TOKEN`       | Shared secret for bot ↔ web UI socket auth (secret) |
| `LOG_LEVEL`              | `INFO`                                              |

### tower-bot_site.service

| Variable                | Value / Notes                           |
| ----------------------- | --------------------------------------- |
| `DISCORD_CLIENT_ID`     | OAuth app client ID                     |
| `DISCORD_CLIENT_SECRET` | OAuth app client secret (secret)        |
| `BOTUI_SECRET_KEY`      | Session cookie encryption key (secret)  |
| `DISCORD_BOT_CONFIG`    | `/data` — same as bot, for DB access    |
| `BOT_SOCKET_TOKEN`      | Must match the bot's `BOT_SOCKET_TOKEN` |
| `LOG_LEVEL`             | `INFO`                                  |

> **Socket**: The tower-bot_site connects to the bot via `/run/discord-bot/config.sock` (Unix socket, auto-detected on Linux). The socket directory must exist and be writable by the `tower` user before starting either service.
