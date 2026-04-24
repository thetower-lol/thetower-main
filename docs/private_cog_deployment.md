# Private Package Deployment Guide

This document describes how to set up SSH deploy keys for installing private packages (cogs, modules, etc.) on the production server.

## Overview

Private packages are external Python packages stored in private GitHub repositories. They use SSH deploy keys for secure, read-only access during installation. This approach provides better security than using personal access tokens or account-level SSH keys.

## Automated Setup (Recommended)

Use the `setup_private_package.py` script to automate the entire process:

```bash
# From the project root on the production server
python scripts/setup_private_package.py <package_name> <github_url>

# Examples
python scripts/setup_private_package.py thetower-bcs https://github.com/username/repo
python scripts/setup_private_package.py tourney_reminder https://github.com/username/cogname
```

The script handles:

1. Generating an ED25519 deploy key pair
2. Creating a dedicated SSH host alias (one per repo)
3. Showing the public key and GitHub URL for adding as a deploy key
4. Waiting for confirmation before proceeding
5. Testing SSH connectivity and installing the package
6. Cleaning up generated keys if cancelled

Options:

- `--skip-install`: Only set up SSH, don't pip install
- `--force`: Overwrite existing key and alias

## Security Model

- **Deploy Keys**: Repository-specific SSH keys with read-only access
- **One key per repo**: Each private package gets its own unique deploy key
- **One SSH alias per repo**: Each repo gets a dedicated alias (e.g., `github-tower-<name>`)
- **IdentitiesOnly**: Each alias only offers its own key, ensuring correct authentication

> **Why separate aliases?** GitHub deploy keys authenticate AND authorize in one step.
> SSH authenticates with the first key GitHub accepts, then GitHub checks if that key
> has access to the requested repo. With multiple keys under one alias, the wrong key
> may authenticate first — causing access denied errors. Separate aliases ensure each
> connection uses the correct key.

## Manual SSH Setup (Reference)

### 1. Create SSH Config on Production Server

SSH into production and create/edit `~/.ssh/config`:

```bash
nano ~/.ssh/config
```

Each private package gets its own Host block with a dedicated alias:

```
Host github-tower-cogname
    HostName github.com
    User git
    IdentityFile ~/.ssh/thetower_cogname_deploy
    IdentitiesOnly yes
    StrictHostKeyChecking no

# Add another block for each additional cog
Host github-tower-anothercog
    HostName github.com
    User git
    IdentityFile ~/.ssh/thetower_anothercog_deploy
    IdentitiesOnly yes
    StrictHostKeyChecking no
```

Set proper permissions:

```bash
chmod 600 ~/.ssh/config
```

## Adding a New Private Cog

Follow these steps for each new private cog repository.

### 1. Generate Deploy Key on Production Server

```bash
# SSH into production
ssh root@your-production-server

# Generate deploy key (replace 'cogname' with actual cog name)
ssh-keygen -t ed25519 -C "thetower-cogname-deploy" -f ~/.ssh/thetower_cogname_deploy

# Set proper permissions
chmod 600 ~/.ssh/thetower_cogname_deploy
chmod 644 ~/.ssh/thetower_cogname_deploy.pub

# Display the public key to copy
cat ~/.ssh/thetower_cogname_deploy.pub
```

Copy the entire public key output (starts with `ssh-ed25519`).

### 2. Add Deploy Key to GitHub Repository

1. Go to your private cog repository on GitHub
2. Navigate to **Settings** → **Deploy keys** (or visit `https://github.com/username/RepoName/settings/keys`)
3. Click **"Add deploy key"**
4. **Title**: `TheTower Production Server`
5. **Key**: Paste the public key
6. **Allow write access**: **LEAVE UNCHECKED** ✅ (read-only is safer)
7. Click **"Add key"**

### 3. Update SSH Config

Add a new Host block with a dedicated alias for the new cog:

```bash
nano ~/.ssh/config
```

Add a new Host block:

```
Host github-tower-cogname
    HostName github.com
    User git
    IdentityFile ~/.ssh/thetower_cogname_deploy
    IdentitiesOnly yes
    StrictHostKeyChecking no
```

> **Important:** Each repo MUST have its own Host alias. Do NOT combine multiple
> IdentityFile lines under a single alias — this will cause authentication failures.

### 4. Add Key to SSH Agent

```bash
# Start SSH agent (if not already running)
eval "$(ssh-agent -s)"

# Add the new key
ssh-add ~/.ssh/thetower_cogname_deploy

# Verify it's loaded
ssh-add -l
```

### 5. Test SSH Connection

```bash
# Test the connection using the repo-specific alias
ssh -T git@github-tower-cogname
```

Expected output:

```
Hi username/RepoName! You've successfully authenticated, but GitHub does not provide shell access.
```

If you see an error, debug with verbose output:

```bash
ssh -vT git@github-tower-cogname
```

### 6. Install the Private Cog

```bash
# Activate production venv
source /tourney/.venv/bin/activate

# Install using the repo-specific alias
pip install git+ssh://git@github-tower-cogname/username/RepoName.git
```

### 7. Verify Installation

```bash
# Check if package is installed
pip list | grep cogname

# Verify entry point is discoverable
python -c "
import importlib.metadata
eps = importlib.metadata.entry_points().select(group='thetower.bot.cogs')
for ep in eps:
    print(f'{ep.name}: {ep.value}')
"
```

### 8. Restart Bot and Enable Cog

```bash
# Restart the Discord bot service
sudo systemctl restart discord_bot.service

# Check logs for errors
sudo journalctl -u discord_bot.service -f
```

Then in Discord:

1. Go to `/settings` → **Bot Settings** → **Cog Management**
2. Click **"Refresh Cog Sources"**
3. Select the new cog from the dropdown
4. Configure bot owner settings (enabled, public/authorized guilds)
5. Click **"Save & Load Cog"**

## Updating an Existing Private Cog

To update an already-installed private cog:

```bash
# Activate venv
source /tourney/.venv/bin/activate

# Upgrade to latest version (uses the repo-specific alias baked into metadata)
pip install --upgrade --force-reinstall --no-deps git+ssh://git@github-tower-cogname/username/RepoName.git

# Restart bot
sudo systemctl restart discord_bot.service
```

## Troubleshooting

### SSH Connection Fails

```bash
# Use verbose mode to see which key is being tried
ssh -vT git@github-tower-private

# Check if key is loaded in agent
ssh-add -l

# Manually add key if missing
ssh-add ~/.ssh/thetower_cogname_deploy
```

### Deploy Key Already in Use

GitHub doesn't allow the same deploy key on multiple repositories. Generate a unique key for each repo.

### Permission Denied

- Verify the public key was added correctly to GitHub
- Check that the private key file has correct permissions (600)
- Ensure the key is loaded in ssh-agent

### Cog Not Discoverable

Check the cog's `pyproject.toml` has the entry point pointing at the **top-level package** (not a `.cogs` sub-module):

```toml
[project.entry-points."thetower.bot.cogs"]
cogname = "package_name"   # src/package_name/__init__.py must have async setup(bot)
```

### Codebase Status Page Shows "git ls-remote failed"

The `/codebase` admin page checks for updates by running `git ls-remote` against the URL stored in installed package metadata. If the package has a hardcoded HTTPS URL (e.g. `https://github.com/...`) rather than the SSH alias URL, the check fails for private repos because HTTPS requires credentials.

**Root cause**: The package's `pyproject.toml` has a static `[project.urls]` entry instead of dynamic URL injection via `setup.py`.

**Fix**: Ensure the package uses dynamic URL injection — the same pattern as `cog-tourney-reminder`:

1. In `pyproject.toml`, add `"urls"` to `dynamic` and remove the static `[project.urls]` section:

    ```toml
    [project]
    dynamic = ["version", "urls"]
    # No [project.urls] section
    ```

2. In `setup.py`, read the git remote at build time:

    ```python
    import subprocess
    from setuptools import setup

    def get_git_remote_url():
        try:
            result = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return "https://github.com/owner/repo"  # fallback

    setup(project_urls={"Repository": get_git_remote_url()})
    ```

3. Reinstall using the SSH alias URL so the alias gets baked into the metadata:

    ```bash
    pip install --force-reinstall --no-deps "git+ssh://git@github-tower-<name>/owner/repo.git"
    ```

After reinstall, verify the metadata URL is correct:

```bash
python3 -c "import importlib.metadata; print(importlib.metadata.metadata('<pkg>').get_all('Project-URL'))"
```

It should show `ssh://git@github-tower-<name>/...` not `https://...`.

## Security Notes

- ✅ **DO**: Use unique deploy keys per repository
- ✅ **DO**: Keep deploy keys read-only (don't check "Allow write access")
- ✅ **DO**: Use descriptive key comments for identification
- ❌ **DON'T**: Add keys to your personal GitHub account (use deploy keys instead)
- ❌ **DON'T**: Reuse the same key across multiple repositories
- ❌ **DON'T**: Enable write access unless absolutely necessary

## Example: Managed Polls Cog

Complete example for the Managed_PollsCog:

```bash
# Automated (recommended)
python scripts/setup_private_package.py managed_polls https://github.com/thetower-lol/Managed_PollsCog

# Or manually:

# 1. Generate key
ssh-keygen -t ed25519 -C "thetower-managed-polls-deploy" -f ~/.ssh/thetower_managed_polls_deploy

# 2. Display and copy public key
cat ~/.ssh/thetower_managed_polls_deploy.pub

# 3. Add to GitHub at: https://github.com/thetower-lol/Managed_PollsCog/settings/keys

# 4. Add SSH alias (dedicated per-repo alias)
# In ~/.ssh/config:
# Host github-tower-managed-polls
#     HostName github.com
#     User git
#     IdentityFile ~/.ssh/thetower_managed_polls_deploy
#     IdentitiesOnly yes
#     StrictHostKeyChecking no

# 5. Add to agent
ssh-add ~/.ssh/thetower_managed_polls_deploy

# 6. Test
ssh -T git@github-tower-managed-polls

# 7. Install
pip install git+ssh://git@github-tower-managed-polls/thetower-lol/Managed_PollsCog.git

# 8. Verify
pip show thetower-managed-polls

# 9. Restart bot
sudo systemctl restart discord_bot.service
```

## Quick Reference

| Task                | Command                                                                                             |
| ------------------- | --------------------------------------------------------------------------------------------------- |
| **Automated setup** | `python scripts/setup_private_package.py <name> <url>`                                              |
| Generate deploy key | `ssh-keygen -t ed25519 -C "description" -f ~/.ssh/keyname`                                          |
| Show public key     | `cat ~/.ssh/keyname.pub`                                                                            |
| Add key to agent    | `ssh-add ~/.ssh/keyname`                                                                            |
| List loaded keys    | `ssh-add -l`                                                                                        |
| Test SSH connection | `ssh -T git@github-tower-<name>`                                                                    |
| Install private pkg | `pip install git+ssh://git@github-tower-<name>/user/repo.git`                                       |
| Update private pkg  | `pip install --upgrade --force-reinstall --no-deps git+ssh://git@github-tower-<name>/user/repo.git` |
| Restart bot         | `sudo systemctl restart discord_bot.service`                                                        |
| Check bot logs      | `sudo journalctl -u discord_bot.service -f`                                                         |
