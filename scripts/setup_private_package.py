#!/usr/bin/env python3
"""
Setup script for deploying private packages from GitHub repositories.

Automates the entire deploy key + SSH alias + pip install workflow:
1. Generates an ED25519 deploy key pair
2. Creates a dedicated SSH host alias (one per repo)
3. Shows the public key and GitHub URL for adding as a deploy key
4. Waits for confirmation before proceeding
5. Tests SSH connectivity and installs the package
6. Cleans up generated keys if the user cancels

Usage:
    python scripts/setup_private_package.py <package_name> <github_url>

Examples:
    python scripts/setup_private_package.py thetower-bcs https://github.com/username/repo
    python scripts/setup_private_package.py cogname ssh://git@github.com/owner/repo.git
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


TOWER_SSH_DIR = Path("/var/lib/tower/.ssh")
TOWER_USER = "tower"


def parse_github_url(url: str) -> tuple[str, str]:
    """Extract owner and repo name from a GitHub URL.

    Supports formats:
        https://github.com/owner/repo
        https://github.com/owner/repo.git
        git@github.com:owner/repo.git
        ssh://git@github.com/owner/repo.git
    """
    # Strip trailing slashes and .git
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]

    # HTTPS format
    match = re.match(r"https?://github\.com/([^/]+)/([^/]+)", url)
    if match:
        return match.group(1), match.group(2)

    # SSH format (git@github.com:owner/repo)
    match = re.match(r"git@github\.com:([^/]+)/([^/]+)", url)
    if match:
        return match.group(1), match.group(2)

    # SSH URL format (ssh://git@github.com/owner/repo)
    match = re.match(r"ssh://git@github\.com/([^/]+)/([^/]+)", url)
    if match:
        return match.group(1), match.group(2)

    print(f"Error: Could not parse GitHub URL: {url}")
    sys.exit(1)


def run_command(cmd: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    try:
        result = subprocess.run(cmd, capture_output=capture, text=True, check=check)
        return result
    except subprocess.CalledProcessError as e:
        if capture:
            print(f"Command failed: {' '.join(cmd)}")
            if e.stdout:
                print(f"stdout: {e.stdout}")
            if e.stderr:
                print(f"stderr: {e.stderr}")
        raise


def get_ssh_dir() -> Path:
    """Get the SSH directory path."""
    return Path.home() / ".ssh"


def get_key_path(package_name: str) -> Path:
    """Get the private key file path for a package."""
    return get_ssh_dir() / f"thetower_{package_name}_deploy"


def get_ssh_alias(package_name: str) -> str:
    """Get the SSH host alias for a package."""
    return f"github-tower-{package_name.replace('_', '-')}"


def key_exists(package_name: str) -> bool:
    """Check if a deploy key already exists for this package."""
    key_path = get_key_path(package_name)
    return key_path.exists()


def generate_key(package_name: str) -> Path:
    """Generate an ED25519 deploy key pair."""
    key_path = get_key_path(package_name)
    ssh_dir = get_ssh_dir()

    # Ensure .ssh directory exists with correct permissions
    ssh_dir.mkdir(mode=0o700, exist_ok=True)

    comment = f"thetower-{package_name.replace('_', '-')}-deploy"

    print(f"\n🔑 Generating deploy key: {key_path}")
    run_command(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-C",
            comment,
            "-f",
            str(key_path),
            "-N",
            "",  # No passphrase
        ]
    )

    # Set permissions
    os.chmod(key_path, 0o600)
    os.chmod(f"{key_path}.pub", 0o644)

    print("   ✅ Key pair generated")
    return key_path


def cleanup_key(package_name: str) -> None:
    """Remove the deploy key pair for a package."""
    key_path = get_key_path(package_name)
    removed = False

    if key_path.exists():
        key_path.unlink()
        removed = True

    pub_path = Path(f"{key_path}.pub")
    if pub_path.exists():
        pub_path.unlink()
        removed = True

    if removed:
        print(f"   🗑️  Cleaned up key files: {key_path}[.pub]")


def read_ssh_config() -> str:
    """Read the current SSH config file."""
    config_path = get_ssh_dir() / "config"
    if config_path.exists():
        return config_path.read_text()
    return ""


def write_ssh_config(content: str) -> None:
    """Write the SSH config file."""
    config_path = get_ssh_dir() / "config"
    config_path.write_text(content)
    os.chmod(config_path, 0o600)


def alias_exists(package_name: str) -> bool:
    """Check if the SSH alias already exists in config."""
    alias = get_ssh_alias(package_name)
    config = read_ssh_config()
    return f"Host {alias}" in config


def add_ssh_alias(package_name: str) -> str:
    """Add a dedicated SSH host alias for this package's repo."""
    alias = get_ssh_alias(package_name)
    key_path = get_key_path(package_name)

    block = f"""
Host {alias}
    HostName github.com
    User git
    IdentityFile {key_path}
    IdentitiesOnly yes
    StrictHostKeyChecking no
"""

    config = read_ssh_config()

    if f"Host {alias}" in config:
        print(f"   ⚠️  SSH alias '{alias}' already exists in config, skipping")
        return alias

    config = config.rstrip() + "\n" + block
    write_ssh_config(config)

    print(f"   ✅ Added SSH alias '{alias}' to ~/.ssh/config")
    return alias


def remove_ssh_alias(package_name: str) -> None:
    """Remove the SSH host alias for a package from the config."""
    alias = get_ssh_alias(package_name)
    config = read_ssh_config()

    if f"Host {alias}" not in config:
        return

    # Remove the Host block (from "Host <alias>" to next "Host " or end of file)
    lines = config.split("\n")
    new_lines = []
    skip = False

    for line in lines:
        if line.strip() == f"Host {alias}":
            skip = True
            continue
        if skip and line.startswith("Host "):
            skip = False
        if not skip:
            new_lines.append(line)

    # Clean up extra blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(new_lines)).strip() + "\n"
    write_ssh_config(cleaned)

    print(f"   🗑️  Removed SSH alias '{alias}' from ~/.ssh/config")


def setup_tower_user_ssh(package_name: str, tower_ssh_dir: Path) -> bool:
    """Copy deploy keys and add SSH alias for the tower service user."""
    src_key = get_key_path(package_name)
    if not src_key.exists():
        print(f"   ⚠️  Key not found at {src_key}, skipping tower user setup")
        return False

    try:
        tower_ssh_dir.mkdir(mode=0o700, exist_ok=True)
        run_command(["chown", f"{TOWER_USER}:{TOWER_USER}", str(tower_ssh_dir)], check=False)
    except Exception as e:
        print(f"   ⚠️  Could not create {tower_ssh_dir}: {e}")
        return False

    dst_key = tower_ssh_dir / src_key.name
    dst_pub = Path(f"{dst_key}.pub")
    try:
        shutil.copy2(src_key, dst_key)
        shutil.copy2(Path(f"{src_key}.pub"), dst_pub)
        os.chmod(dst_key, 0o600)
        os.chmod(dst_pub, 0o644)
        run_command(["chown", f"{TOWER_USER}:{TOWER_USER}", str(dst_key), str(dst_pub)], check=False)
        print(f"   ✅ Copied keys to {tower_ssh_dir}")
    except Exception as e:
        print(f"   ⚠️  Could not copy keys to tower user dir: {e}")
        return False

    alias = get_ssh_alias(package_name)
    config_path = tower_ssh_dir / "config"
    config = config_path.read_text() if config_path.exists() else ""

    if f"Host {alias}" in config:
        print(f"   ⚠️  SSH alias '{alias}' already exists in tower's config, skipping")
    else:
        block = f"""
Host {alias}
    HostName github.com
    User git
    IdentityFile {dst_key}
    IdentitiesOnly yes
    StrictHostKeyChecking no
"""
        config = config.rstrip() + "\n" + block
        config_path.write_text(config)
        os.chmod(config_path, 0o600)
        run_command(["chown", f"{TOWER_USER}:{TOWER_USER}", str(config_path)], check=False)
        print(f"   ✅ Added SSH alias '{alias}' to tower user's SSH config")

    return True


def cleanup_tower_user_ssh(package_name: str, tower_ssh_dir: Path) -> None:
    """Remove deploy keys and SSH alias from the tower service user's SSH setup."""
    src_key = get_key_path(package_name)
    dst_key = tower_ssh_dir / src_key.name
    dst_pub = Path(f"{dst_key}.pub")

    for path in (dst_key, dst_pub):
        if path.exists():
            path.unlink()
            print(f"   🗑️  Removed {path}")

    config_path = tower_ssh_dir / "config"
    if config_path.exists():
        alias = get_ssh_alias(package_name)
        config = config_path.read_text()
        if f"Host {alias}" in config:
            lines = config.split("\n")
            new_lines = []
            skip = False
            for line in lines:
                if line.strip() == f"Host {alias}":
                    skip = True
                    continue
                if skip and line.startswith("Host "):
                    skip = False
                if not skip:
                    new_lines.append(line)
            cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(new_lines)).strip() + "\n"
            config_path.write_text(cleaned)
            print(f"   🗑️  Removed SSH alias '{alias}' from tower user's SSH config")


def add_to_agent(package_name: str) -> None:
    """Add the deploy key to the SSH agent."""
    key_path = get_key_path(package_name)
    try:
        run_command(["ssh-add", str(key_path)])
        print("   ✅ Key added to SSH agent")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("   ⚠️  Could not add key to SSH agent (agent may not be running)")


def test_ssh_connection(alias: str, owner: str, repo: str) -> bool:
    """Test SSH connectivity to the repository."""
    print(f"\n🔗 Testing SSH connection via '{alias}'...")
    result = run_command(
        ["git", "ls-remote", "--tags", "--refs", f"ssh://git@{alias}/{owner}/{repo}.git"],
        check=False,
    )

    if result.returncode == 0:
        print("   ✅ SSH connection successful!")
        tag_count = len([line for line in result.stdout.strip().split("\n") if line.strip()])
        if tag_count:
            print(f"   📦 Found {tag_count} version tag(s)")
        return True
    else:
        print("   ❌ SSH connection failed!")
        if result.stderr:
            print(f"   Error: {result.stderr.strip()}")
        return False


def install_package(alias: str, owner: str, repo: str) -> bool:
    """Install the package via pip."""
    install_url = f"git+ssh://git@{alias}/{owner}/{repo}.git"
    print(f"\n📦 Installing package from {install_url}...")

    result = run_command(
        [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-deps", install_url],
        check=False,
        capture=False,
    )

    if result.returncode == 0:
        print("\n   ✅ Package installed successfully!")
        return True
    else:
        print("\n   ❌ Installation failed!")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Setup SSH deploy key and install a private package from GitHub",
        epilog="Example: python scripts/setup_private_package.py thetower-bcs https://github.com/thetower-lol/thetower-bcs",
    )
    parser.add_argument("package_name", help="Short name for the package (e.g., thetower-bcs, managed_polls)")
    parser.add_argument("github_url", help="GitHub repository URL (HTTPS or SSH format)")
    parser.add_argument("--skip-install", action="store_true", help="Only set up SSH, don't pip install")
    parser.add_argument("--force", action="store_true", help="Overwrite existing key and alias")
    parser.add_argument(
        "--tower-ssh-dir", type=Path, default=TOWER_SSH_DIR, metavar="DIR", help=f"Tower service user SSH directory (default: {TOWER_SSH_DIR})"
    )
    parser.add_argument("--no-tower-user", action="store_true", help="Skip tower service user SSH setup")
    args = parser.parse_args()

    package_name = args.package_name
    owner, repo = parse_github_url(args.github_url)
    alias = get_ssh_alias(package_name)

    print(f"{'=' * 60}")
    print("  Private Package Deploy Key Setup")
    print(f"{'=' * 60}")
    print(f"  Package:    {package_name}")
    print(f"  Repository: {owner}/{repo}")
    print(f"  SSH Alias:  {alias}")
    print(f"  Key File:   {get_key_path(package_name)}")
    print(f"{'=' * 60}")

    # Check for existing setup
    if key_exists(package_name) and not args.force:
        print(f"\n⚠️  Deploy key already exists for '{package_name}'.")
        print("   Use --force to regenerate, or skip to SSH test.")

        if alias_exists(package_name):
            # Key and alias exist, just test and optionally install
            if test_ssh_connection(alias, owner, repo):
                if not args.skip_install:
                    install_package(alias, owner, repo)
            return
        else:
            print(f"   SSH alias '{alias}' missing — adding it now.")
            add_ssh_alias(package_name)
            add_to_agent(package_name)
            if test_ssh_connection(alias, owner, repo):
                if not args.skip_install:
                    install_package(alias, owner, repo)
            return

    if args.force and key_exists(package_name):
        print("\n🔄 Force mode: removing existing key and alias...")
        cleanup_key(package_name)
        remove_ssh_alias(package_name)
        if not args.no_tower_user:
            cleanup_tower_user_ssh(package_name, args.tower_ssh_dir)

    # Step 1: Generate deploy key
    print(f"\n{'─' * 60}")
    print("  Step 1: Generate Deploy Key")
    print(f"{'─' * 60}")
    generate_key(package_name)

    # Step 2: Add SSH alias
    print(f"\n{'─' * 60}")
    print("  Step 2: Configure SSH Alias")
    print(f"{'─' * 60}")
    add_ssh_alias(package_name)

    # Step 3: Add to SSH agent
    add_to_agent(package_name)

    # Step 3b: Set up tower service user SSH
    if not args.no_tower_user:
        print(f"\n{'─' * 60}")
        print("  Step 3: Configure Tower Service User SSH")
        print(f"{'─' * 60}")
        if args.tower_ssh_dir.parent.exists():
            setup_tower_user_ssh(package_name, args.tower_ssh_dir)
        else:
            print(f"   ⚠️  Tower SSH dir parent not found: {args.tower_ssh_dir.parent}")
            print("   Skipping tower user setup (use --no-tower-user to suppress)")

    # Step 4: Show public key and wait for user to add it to GitHub
    print(f"\n{'─' * 60}")
    print("  Step 4: Add Deploy Key to GitHub")
    print(f"{'─' * 60}")

    pub_key_path = Path(f"{get_key_path(package_name)}.pub")
    pub_key = pub_key_path.read_text().strip()

    deploy_key_url = f"https://github.com/{owner}/{repo}/settings/keys/new"

    print("\n📋 Public key to copy:\n")
    print(f"   {pub_key}")
    print("\n🌐 Add it as a deploy key at:")
    print(f"   {deploy_key_url}")
    print(f"\n   Title: TheTower Production - {package_name}")
    print("   ⚠️  Leave 'Allow write access' UNCHECKED (read-only)")

    print(f"\n{'─' * 60}")

    try:
        response = input("\n✋ Have you added the deploy key to GitHub? [y/N/cancel]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        response = "cancel"

    if response in ("cancel", "c", ""):
        print("\n❌ Cancelled. Cleaning up...")
        cleanup_key(package_name)
        remove_ssh_alias(package_name)
        if not args.no_tower_user:
            cleanup_tower_user_ssh(package_name, args.tower_ssh_dir)
        print("   Done. No changes remain.")
        sys.exit(1)

    if response not in ("y", "yes"):
        print("\n❌ Cancelled. Cleaning up...")
        cleanup_key(package_name)
        remove_ssh_alias(package_name)
        if not args.no_tower_user:
            cleanup_tower_user_ssh(package_name, args.tower_ssh_dir)
        print("   Done. No changes remain.")
        sys.exit(1)

    # Step 5: Test connection
    print(f"\n{'─' * 60}")
    print("  Step 5: Test & Install")
    print(f"{'─' * 60}")

    if not test_ssh_connection(alias, owner, repo):
        print("\n⚠️  SSH test failed. The deploy key may not have been added correctly.")
        retry = input("   Retry? [y/N]: ").strip().lower()
        if retry in ("y", "yes"):
            if not test_ssh_connection(alias, owner, repo):
                print("\n❌ Still failing. Keeping key files for manual debugging.")
                print(f"   Key: {get_key_path(package_name)}")
                print(f"   Debug: ssh -vT git@{alias}")
                sys.exit(1)
        else:
            print("\n   Keeping key files for manual debugging.")
            print(f"   Debug: ssh -vT git@{alias}")
            sys.exit(1)

    # Step 6: Install
    if not args.skip_install:
        install_package(alias, owner, repo)

    # Summary
    print(f"\n{'=' * 60}")
    print("  Setup Complete!")
    print(f"{'=' * 60}")
    print(f"  SSH Alias:    {alias}")
    print(f"  Install URL:  git+ssh://git@{alias}/{owner}/{repo}.git")
    print("  Update cmd:   pip install --upgrade --force-reinstall --no-deps \\")
    print(f"                  git+ssh://git@{alias}/{owner}/{repo}.git")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
