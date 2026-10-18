#!/usr/bin/env python
"""
Setup script to extract Streamlit pages from installed thetower package.

This script copies the src/thetower/web/ directory and .streamlit/ config
from the installed package to /opt/thetower/, ensuring Streamlit services
have access to pages.py and config. Idempotent: skips extraction if version
hasn't changed.
"""
import fcntl
import logging
import shutil
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

STREAMLIT_TARGET_DIR = Path("/opt/thetower")
VERSION_MARKER = STREAMLIT_TARGET_DIR / ".thetower_version"
# /run/lock is the standard (tmpfs) home for lock files and, unlike /tmp, is not made per-service by PrivateTmp=,
# so the public and hidden sites keep sharing this lock.
LOCK_FILE = Path("/run/lock/thetower_init_streamlit.lock")


def setup_streamlit():
    """Extract Streamlit pages from package to /opt/thetower/ if version changed."""
    try:
        import thetower

        # Ensure target directory exists
        STREAMLIT_TARGET_DIR.mkdir(parents=True, exist_ok=True)

        # Acquire exclusive lock to prevent concurrent runs from multiple services
        # (e.g. public and hidden sites starting simultaneously via ExecStartPre)
        with open(LOCK_FILE, "w") as lock_fd:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            except OSError as e:
                logger.warning(f"Could not acquire lock: {e}")

            # Get the installed thetower version
            try:
                # Try to get version from package
                installed_version = thetower.__version__
            except AttributeError:
                # Fallback: use pkg_resources or importlib.metadata
                try:
                    from importlib.metadata import version

                    installed_version = version("thetower")
                except Exception:
                    # If all else fails, use a timestamp-based marker
                    installed_version = "unknown"

            # Check if we already extracted this version (and files actually exist)
            if VERSION_MARKER.exists():
                existing_version = VERSION_MARKER.read_text().strip()
                web_dst_check = STREAMLIT_TARGET_DIR / "src" / "thetower" / "web"
                if existing_version == installed_version and web_dst_check.exists():
                    logger.info(f"Streamlit pages already up-to-date (version {installed_version})")
                    return 0

            # Get the thetower package location
            thetower_path = Path(thetower.__file__).parent
            logger.info(f"Found thetower package at {thetower_path}")

            # Source: web subdirectory of thetower package
            web_src = thetower_path / "web"
            streamlit_cfg_src = thetower_path / ".streamlit"

            if not web_src.exists():
                raise FileNotFoundError(f"Web directory not found at {web_src}")
            if not streamlit_cfg_src.exists():
                raise FileNotFoundError(f"Streamlit config not found at {streamlit_cfg_src}")

            # Target: /opt/thetower/src/thetower/web
            web_dst = STREAMLIT_TARGET_DIR / "src" / "thetower" / "web"
            streamlit_cfg_dst = STREAMLIT_TARGET_DIR / ".streamlit"

            logger.info(f"Extracting Streamlit pages (version {installed_version})")
            logger.info(f"  Web source: {web_src}")
            logger.info(f"  Web target: {web_dst}")
            logger.info(f"  Config source: {streamlit_cfg_src}")
            logger.info(f"  Config target: {streamlit_cfg_dst}")

            # Create parent directories
            web_dst.parent.mkdir(parents=True, exist_ok=True)

            # Remove existing web dir if present
            if web_dst.exists():
                logger.info(f"Removing existing {web_dst}")
                shutil.rmtree(web_dst)

            # Remove existing .streamlit dir if present
            if streamlit_cfg_dst.exists():
                logger.info(f"Removing existing {streamlit_cfg_dst}")
                shutil.rmtree(streamlit_cfg_dst)

            # Copy web directory
            shutil.copytree(web_src, web_dst)

            # Copy .streamlit config
            shutil.copytree(streamlit_cfg_src, streamlit_cfg_dst)

            # Write version marker
            VERSION_MARKER.write_text(installed_version)

            logger.info("✓ Streamlit pages extracted successfully")
            return 0

    except Exception as e:
        logger.error(f"✗ Failed to setup Streamlit: {e}")
        import traceback

        traceback.print_exc()
        return 1


def main():
    """Entry point for thetower-init-streamlit command."""
    sys.exit(setup_streamlit())


if __name__ == "__main__":
    main()
