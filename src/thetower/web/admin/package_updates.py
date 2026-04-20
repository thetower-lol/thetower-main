"""
Package Update Utilities for thetower-project packages.

Scans for installed packages with Private::thetower-project classifier
and provides update checking/installation via git repositories.
"""

import asyncio
import importlib.metadata
import re
import sys
from typing import Dict, List, Optional

from packaging.version import parse as parse_version


def get_thetower_packages() -> List[Dict[str, any]]:
    """
    Scan installed packages for those with Private::thetower-project classifier.

    Returns:
        List of dicts with package information (includes install_type: editable or regular)
    """
    packages = []
    seen_packages = set()  # Track (name, version, install_type) to avoid true duplicates

    for dist in importlib.metadata.distributions():
        try:
            metadata = dist.metadata
            classifiers = metadata.get_all("Classifier") or []

            # Check if this is a thetower-project package
            is_thetower = any("Private :: thetower-project" in c for c in classifiers)

            if is_thetower:
                # Determine package type from classifiers
                package_type = "unknown"
                for classifier in classifiers:
                    if "Private :: thetower.main" in classifier:
                        package_type = "main"
                        break
                    elif "Private :: thetower.cog" in classifier:
                        package_type = "cog"
                        break
                    elif "Private :: thetower.bot" in classifier:
                        package_type = "bot"
                        break
                    elif "Private :: thetower.module" in classifier:
                        package_type = "module"
                        break

                # Get repository URL from Project-URL
                project_urls = metadata.get_all("Project-URL") or []
                repo_url = None
                for url_entry in project_urls:
                    if url_entry.startswith("Repository,"):
                        repo_url = url_entry.split(",", 1)[1].strip()
                        break

                # Detect install type (editable vs regular)
                install_type = "regular"
                try:
                    # Editable installs use .egg-info, regular installs use .dist-info
                    if hasattr(dist, "_path") and dist._path:
                        metadata_path = str(dist._path)
                        if metadata_path.endswith(".egg-info"):
                            install_type = "editable"
                        elif metadata_path.endswith(".dist-info"):
                            # Check for direct_url.json with editable flag (newer pip)
                            direct_url_file = dist._path.parent / "direct_url.json"
                            if direct_url_file.exists():
                                import json

                                with open(direct_url_file) as f:
                                    direct_url = json.load(f)
                                    if direct_url.get("dir_info", {}).get("editable"):
                                        install_type = "editable"
                except Exception:
                    # If we can't determine, keep as regular
                    pass

                # Create unique key for this package installation
                package_key = (dist.name, dist.version, install_type)

                # Skip true duplicates (same name, version, and install_type)
                if package_key in seen_packages:
                    continue

                seen_packages.add(package_key)

                packages.append(
                    {
                        "name": dist.name,
                        "version": dist.version,
                        "type": package_type,
                        "install_type": install_type,
                        "repository_url": repo_url,
                        "metadata": dist.metadata,
                    }
                )

        except Exception:
            # Skip packages with metadata issues
            continue

    return packages


async def check_package_updates(package_name: str, repo_url: Optional[str] = None) -> Dict[str, any]:
    """
    Check if updates are available for a package.

    Args:
        package_name: Name of the installed package
        repo_url: Repository URL (optional, will be read from metadata if not provided)

    Returns:
        dict with keys: current_version, latest_version, update_available, repository_url, error
    """
    result = {"current_version": None, "latest_version": None, "update_available": False, "repository_url": None, "error": None}

    try:
        # Get current installed version
        current_version = importlib.metadata.version(package_name)
        result["current_version"] = current_version

        # Get repository URL if not provided
        if not repo_url:
            metadata = importlib.metadata.metadata(package_name)
            project_urls = metadata.get_all("Project-URL") or []

            for url_entry in project_urls:
                if url_entry.startswith("Repository,"):
                    repo_url = url_entry.split(",", 1)[1].strip()
                    break

        if not repo_url:
            result["error"] = "No repository URL found in package metadata"
            return result

        result["repository_url"] = repo_url

        # Use git ls-remote to get latest tags without cloning
        # This works with SSH URLs via configured deploy keys
        cmd = ["git", "ls-remote", "--tags", "--refs", repo_url]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            result["error"] = f"git ls-remote failed: {stderr.decode()}"
            return result

        # Parse tags from output (format: <commit>\trefs/tags/<tag>)
        tags = []
        for line in stdout.decode().splitlines():
            if "\trefs/tags/" in line:
                tag = line.split("\trefs/tags/")[1]
                # Filter out non-version tags (only keep *.*.* format)
                if re.match(r"\d+\.\d+", tag):
                    tags.append(tag)

        if not tags:
            result["error"] = "No version tags found in repository"
            return result

        # Sort tags to find latest using semantic version sorting
        latest_tag = max(tags, key=parse_version)
        result["latest_version"] = latest_tag

        # Compare versions using semantic version comparison
        try:
            current_ver = parse_version(current_version.lstrip("v"))
            latest_ver = parse_version(latest_tag.lstrip("v"))
            result["update_available"] = latest_ver > current_ver
        except Exception:
            # Fallback to string comparison if parsing fails
            result["update_available"] = latest_tag.lstrip("v") != current_version.lstrip("v")

    except Exception as e:
        result["error"] = str(e)

    return result


async def update_package(package_name: str, target_version: Optional[str] = None, repo_url: Optional[str] = None) -> Dict[str, any]:
    """
    Update a package to a specific version or latest.

    Args:
        package_name: Name of the installed package
        target_version: Specific version tag (e.g., "v0.2.0"), or None for latest
        repo_url: Repository URL (optional, will be read from metadata if not provided)

    Returns:
        dict with keys: success, message, new_version
    """
    result = {"success": False, "message": "", "new_version": None}

    try:
        # Get repository URL if not provided
        if not repo_url:
            metadata = importlib.metadata.metadata(package_name)
            project_urls = metadata.get_all("Project-URL") or []

            for url_entry in project_urls:
                if url_entry.startswith("Repository,"):
                    repo_url = url_entry.split(",", 1)[1].strip()
                    break

        if not repo_url:
            result["message"] = "No repository URL found in metadata"
            return result

        # If no version specified, check for latest
        if not target_version:
            update_check = await check_package_updates(package_name, repo_url)
            if update_check.get("error"):
                result["message"] = f"Could not determine latest version: {update_check['error']}"
                return result
            target_version = update_check["latest_version"]

        # Build pip install command
        # Format: pip install --upgrade --force-reinstall git+<url>@<tag>
        install_url = f"git+{repo_url}@{target_version}"

        # Execute pip install
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-deps", install_url]  # Don't reinstall dependencies

        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            result["success"] = True
            result["new_version"] = target_version
            result["message"] = f"Successfully updated {package_name} to {target_version}\n\n{stdout.decode()}"
        else:
            result["message"] = f"Update failed:\n{stderr.decode()}\n{stdout.decode()}"

    except Exception as e:
        result["message"] = f"Update error: {str(e)}"

    return result


def check_package_updates_sync(package_name: str, repo_url: Optional[str] = None) -> Dict[str, any]:
    """
    Synchronous wrapper for check_package_updates.

    Args:
        package_name: Name of the installed package
        repo_url: Repository URL (optional)

    Returns:
        dict with update information
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(check_package_updates(package_name, repo_url))
        loop.close()
        return result
    except Exception as e:
        return {"current_version": None, "latest_version": None, "update_available": False, "repository_url": None, "error": str(e)}


def update_package_sync(package_name: str, target_version: Optional[str] = None, repo_url: Optional[str] = None) -> Dict[str, any]:
    """
    Synchronous wrapper for update_package.

    Args:
        package_name: Name of the installed package
        target_version: Specific version tag or None for latest
        repo_url: Repository URL (optional)

    Returns:
        dict with update result
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(update_package(package_name, target_version, repo_url))
        loop.close()
        return result
    except Exception as e:
        return {"success": False, "message": str(e), "new_version": None}


def sync_dependencies(extras: Optional[List[str]] = None, package_name: str = "thetower") -> Dict[str, any]:
    """
    Re-run pip install for a thetower package with the given extras to bring installed
    dependency versions in line with what is pinned in pyproject.toml.

    In editable (dev) installs, installs from the local project path.
    In regular (prod) installs, installs from the repository URL.

    Args:
        extras: List of extra names to include (e.g. ["web", "bot"]).  Defaults to web.
        package_name: Name of the package to sync (default "thetower").

    Returns:
        dict with keys: success, message
    """
    result: Dict[str, any] = {"success": False, "message": ""}

    if extras is None:
        extras = ["web"]

    try:
        # Determine install source — editable vs regular
        editable_path: Optional[str] = None
        repo_url: Optional[str] = None

        for dist in importlib.metadata.distributions():
            if dist.name.lower() != package_name.lower():
                continue
            if hasattr(dist, "_path") and dist._path:
                path_str = str(dist._path)
                if path_str.endswith(".egg-info"):
                    # Editable install — project root is parent of .egg-info dir
                    editable_path = str(dist._path.parent.parent)
                    break
                elif path_str.endswith(".dist-info"):
                    direct_url_file = dist._path.parent / "direct_url.json"
                    if direct_url_file.exists():
                        import json

                        with open(direct_url_file) as f:
                            direct_url = json.load(f)
                        if direct_url.get("dir_info", {}).get("editable"):
                            editable_path = direct_url.get("url", "").removeprefix("file://")
                            break
                    # Regular install — get repo URL from metadata
                    metadata = dist.metadata
                    for url_entry in metadata.get_all("Project-URL") or []:
                        if url_entry.startswith("Repository,"):
                            repo_url = url_entry.split(",", 1)[1].strip()
                            break
            break

        if extras:
            extras_str = ",".join(extras)
        else:
            extras_str = ""

        if editable_path:
            target = f"{editable_path}[{extras_str}]" if extras_str else editable_path
            cmd = [sys.executable, "-m", "pip", "install", "-e", target]
        elif repo_url:
            target = f"{package_name}[{extras_str}] @ git+{repo_url}" if extras_str else f"git+{repo_url}"
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade", target]
        else:
            result["message"] = f"Could not determine install source for {package_name}."
            return result

        import subprocess

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        output = proc.stdout + ("\n" + proc.stderr if proc.stderr.strip() else "")
        if proc.returncode == 0:
            result["success"] = True
            result["message"] = output.strip()
        else:
            result["message"] = output.strip()

    except Exception as exc:
        result["message"] = str(exc)

    return result
