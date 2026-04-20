"""
Service Status component for Streamlit hidden site.

Shows the status of various systemd services used by the Tower system.
Gracefully handles Windows development environments.
"""

import logging
import platform
import subprocess
from datetime import datetime, timezone
from typing import Optional, Tuple

import streamlit as st

logger = logging.getLogger(__name__)


@st.dialog("Service Logs", width="large")
def show_service_logs(service_name: str, logs: str):
    """Display service logs in a modal dialog."""
    st.markdown(f"**{service_name}** - Recent Logs")
    st.code(logs, language="bash")

    if st.button("✅ Close", width="stretch", type="primary"):
        st.rerun()


@st.dialog("Operation Result", width="large")
def show_operation_result(success: bool, title: str, message: str):
    """Display operation result in a modal dialog."""
    if success:
        st.success(title)
    else:
        st.error(title)

    st.code(message, language="bash")

    if st.button("✅ Close & Refresh", width="stretch", type="primary"):
        st.rerun()


def is_windows() -> bool:
    """Check if running on Windows."""
    return platform.system().lower() == "windows"


def get_service_status(service_name: str) -> Tuple[str, str, str]:
    """
    Get the status of a systemd service.

    Returns:
        tuple: (status, active_state, sub_state)
    """
    if is_windows():
        # On Windows, return mock status for development
        return ("not-available", "unknown", "windows-dev")

    try:
        result = subprocess.run(["systemctl", "is-active", service_name], capture_output=True, text=True, timeout=5)
        active_state = result.stdout.strip()

        # Get more detailed status
        result = subprocess.run(
            ["systemctl", "show", service_name, "--property=SubState,ActiveState,LoadState"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        properties = {}
        for line in result.stdout.strip().split("\n"):
            if "=" in line:
                key, value = line.split("=", 1)
                properties[key] = value

        return (properties.get("LoadState", "unknown"), properties.get("ActiveState", active_state), properties.get("SubState", "unknown"))

    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        return ("not-found", "inactive", "dead")


def get_service_start_time(service_name: str) -> Optional[str]:
    """
    Get the time when a systemd service was last started.

    Returns:
        str: Formatted start time or None if unavailable
    """
    if is_windows():
        # On Windows, return mock start time for development
        return "Development Mode - No Start Time"

    try:
        # Get service start time using systemctl show
        result = subprocess.run(
            ["systemctl", "show", service_name, "--property=ActiveEnterTimestamp"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        for line in result.stdout.strip().split("\n"):
            if line.startswith("ActiveEnterTimestamp="):
                timestamp_str = line.split("=", 1)[1].strip()

                # Handle empty timestamp (service never started)
                if not timestamp_str or timestamp_str == "n/a":
                    return "Never Started"

                # Parse the timestamp
                # systemctl returns timestamps in format: "Tue 2024-08-20 14:30:15 UTC"
                try:
                    # Remove day of week if present
                    if timestamp_str.startswith(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
                        timestamp_str = " ".join(timestamp_str.split()[1:])

                    # Parse the datetime (systemctl gives e.g. "2024-08-20 14:30:15 UTC")
                    dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S %Z")

                    # Ensure the parsed datetime is timezone-aware. If the
                    # parsed object is naive, assume UTC (systemctl emits UTC
                    # in our deployments).
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)

                    # Calculate how long ago this was using a timezone-aware
                    # now in UTC.
                    now = datetime.now(timezone.utc)
                    time_diff = now - dt

                    if time_diff.days > 0:
                        time_ago = f"{time_diff.days} day{'s' if time_diff.days != 1 else ''} ago"
                    elif time_diff.seconds > 3600:
                        hours = time_diff.seconds // 3600
                        time_ago = f"{hours} hour{'s' if hours != 1 else ''} ago"
                    elif time_diff.seconds > 60:
                        minutes = time_diff.seconds // 60
                        time_ago = f"{minutes} minute{'s' if minutes != 1 else ''} ago"
                    else:
                        time_ago = "Just now"

                    # Format the display string
                    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                    return f"{formatted_time}\n({time_ago})"

                except ValueError:
                    # If parsing fails, return raw timestamp
                    return timestamp_str

        return "Unknown"

    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        return "Unavailable"


def get_service_logs(service_name: str, lines: int = 8) -> str:
    """
    Get the last `lines` of console output for a service from journalctl.

    Returns a plaintext string suitable for showing in a code block. On
    Windows or when journalctl is unavailable, returns a friendly message.
    """
    if is_windows():
        return "Development Mode - logs unavailable"

    try:
        # Try by unit name as provided. If that yields nothing, try with
        # a .service suffix as some deployments use that explicitly.
        result = subprocess.run(
            ["journalctl", "-u", service_name, "-n", str(lines), "--no-pager", "-o", "short-iso"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        output = (result.stdout or "").strip()

        if not output:
            # Try with explicit .service suffix
            result = subprocess.run(
                ["journalctl", "-u", f"{service_name}.service", "-n", str(lines), "--no-pager", "-o", "short-iso"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = (result.stdout or "").strip()

        if not output:
            return "No logs found"

        return output

    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        return "Logs unavailable"


def get_status_color(active_state: str, sub_state: str) -> str:
    """Get the appropriate color for service status."""
    if active_state == "active" and sub_state == "running":
        return "green"
    elif active_state == "active":
        return "orange"
    elif active_state == "inactive":
        return "gray"
    elif active_state == "failed":
        return "red"
    else:
        return "yellow"


def get_status_emoji(active_state: str, sub_state: str, load_state: str = "loaded") -> str:
    """Get the appropriate emoji for service status."""
    if is_windows() and sub_state == "windows-dev":
        return "🖥️"
    elif load_state == "not-found":
        return "❌"
    elif load_state == "masked":
        return "🚫"
    elif load_state != "loaded":
        return "⚠️"
    elif active_state == "active" and sub_state == "running":
        return "🟢"
    elif active_state == "active":
        return "🟡"
    elif active_state == "inactive":
        return "⚪"
    elif active_state == "failed":
        return "🔴"
    else:
        return "🟡"


def restart_service(service_name: str) -> bool:
    """
    Restart a systemd service.

    Returns:
        bool: True if restart was successful, False otherwise
    """
    if is_windows():
        # On Windows, simulate restart for development
        return True

    try:
        result = subprocess.run(["/usr/bin/sudo", "/usr/bin/systemctl", "restart", service_name], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.error(f"Failed to restart {service_name}: {result.stderr}")
        return result.returncode == 0
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        logger.error(f"Exception restarting {service_name}: {e}")
        return False


def start_service(service_name: str) -> bool:
    """
    Start a systemd service.

    Returns:
        bool: True if start was successful, False otherwise
    """
    if is_windows():
        # On Windows, simulate start for development
        return True

    try:
        result = subprocess.run(["/usr/bin/sudo", "/usr/bin/systemctl", "start", service_name], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.error(f"Failed to start {service_name}: {result.stderr}")
        return result.returncode == 0
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        logger.error(f"Exception starting {service_name}: {e}")
        return False


def service_status_page():
    """Main service status page component."""
    st.title("🔧 Service Status")

    # Show environment info
    if is_windows():
        st.info("🖥️ **Development Mode**: Running on Windows - service status simulated for development")
    else:
        st.markdown("Monitor and manage Tower system services")

    # Warning about restarting services
    st.warning(
        "⚠️ **Important**: Do not restart services without talking to **thedisasterfish** first! Service restarts can affect live users and ongoing tournaments."
    )

    # Define services to monitor (from admin.py restart actions)
    services = {
        "tower-public_site": {
            "name": "Public Site",
            "description": "Main public website (thetower.lol)",
            "service": "tower-public_site.service",
            "restart_allowed": True,
        },
        "tower-hidden_site": {
            "name": "Hidden Site",
            "description": "Internal analytics site (hidden.thetower.lol)",
            "service": "tower-hidden_site.service",
            "restart_allowed": True,
        },
        "tower-admin_site": {
            "name": "Admin Site",
            "description": "Admin interface (admin.thetower.lol)",
            "service": "tower-admin_site.service",
            "restart_allowed": True,
        },
        "discord_bot": {
            "name": "TheTower Bot",
            "description": "Discord bot for game interactions",
            "service": "discord_bot.service",
            "restart_allowed": True,
        },
        "bot-webui": {
            "name": "Bot Web UI",
            "description": "Discord bot web interface (bot.thetower.lol)",
            "service": "bot-webui.service",
            "restart_allowed": True,
        },
        "get_results": {
            "name": "Get Results",
            "description": "Service that fetches tournament data (start-only)",
            "service": "get_results.service",
            "restart_allowed": False,
        },
        "import_results": {
            "name": "Import Results",
            "description": "Service that imports tournament results (start-only)",
            "service": "import_results.service",
            "restart_allowed": False,
        },
        "get_live_results": {
            "name": "Get Live Results",
            "description": "Service that fetches live tournament data (start-only)",
            "service": "get_live_results.service",
            "restart_allowed": False,
        },
        "import_live_results": {
            "name": "Import Live Results",
            "description": "Appends live snapshots to delta archives every 30 min",
            "service": "import_live_results.service",
            "restart_allowed": True,
        },
        "tower-recalc_worker": {
            "name": "Recalc Worker",
            "description": "Background tournament recalculation queue worker",
            "service": "tower-recalc_worker.service",
            "restart_allowed": True,
        },
        "tower-zendesk_queue": {
            "name": "Zendesk Queue Worker",
            "description": "Background worker for creating Zendesk tickets from moderation reports",
            "service": "tower-zendesk_queue.service",
            "restart_allowed": True,
        },
        "generate_live_bracket_cache": {
            "name": "Live Bracket Cache",
            "description": "Generates and maintains the live bracket cache used by live views",
            "service": "generate_live_bracket_cache.service",
            "restart_allowed": True,
        },
        "tower-backup": {
            "name": "Backup Service",
            "description": "Uploads raw tars and DB snapshots to Cloudflare R2 (start-only)",
            "service": "tower-backup.service",
            "restart_allowed": False,
        },
    }

    # Refresh controls
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("🔄 Refresh Now"):
            st.rerun()
    with col2:
        utc_time = datetime.now(timezone.utc).strftime("%H:%M:%S")
        st.markdown(f"*Last updated: {utc_time} UTC*")

    # How many log lines to show for each service
    log_lines = st.slider("Log lines to show in service status", min_value=1, max_value=50, value=15)

    st.markdown("---")

    # Service status grid
    for service_id, config in services.items():
        with st.container():
            col1, col2, col3, col4 = st.columns([3, 2, 2.5, 1])

            # Get service status and start time
            load_state, active_state, sub_state = get_service_status(config["service"])
            status_emoji = get_status_emoji(active_state, sub_state, load_state)
            start_time = get_service_start_time(config["service"])

            with col1:
                st.markdown(f"**{status_emoji} {config['name']}**")
                st.caption(config["description"])

            with col2:
                # Combined status that includes both active state and load state issues
                if is_windows() and sub_state == "windows-dev":
                    st.info("Development Mode")
                elif load_state == "not-found":
                    st.error("Not Found")
                elif load_state == "masked":
                    st.warning("Disabled/Masked")
                elif load_state != "loaded":
                    st.error(f"Error ({load_state})")
                elif active_state == "active" and sub_state == "running":
                    st.success("Running")
                elif active_state == "active":
                    st.warning(f"Active ({sub_state})")
                elif active_state == "failed":
                    st.error("Failed")
                elif active_state == "inactive":
                    st.info("Stopped")
                else:
                    st.warning(f"Unknown ({active_state})")

            with col3:
                # Display start time information
                if start_time:
                    if start_time == "Development Mode - No Start Time":
                        st.markdown("🖥️ *Dev Mode*")
                    elif start_time == "Never Started":
                        st.markdown("⏸️ *Never Started*")
                    elif start_time == "Unknown" or start_time == "Unavailable":
                        st.markdown("❓ *Unknown*")
                    else:
                        # Show formatted time with tooltip
                        if "\n" in start_time:
                            time_parts = start_time.split("\n")
                            full_time = time_parts[0]
                            time_ago = time_parts[1].strip("()")
                            st.markdown(f"🕐 **{time_ago}**")
                            st.caption(full_time)
                        else:
                            st.markdown(f"🕐 {start_time}")
                else:
                    st.markdown("❓ *Unknown*")

            with col4:
                # Action button logic
                if load_state == "loaded" or is_windows():
                    restart_allowed = config.get("restart_allowed", True)

                    # Determine button state and text
                    if not restart_allowed:
                        # Start-only services (import_results, get_results)
                        if is_windows() or (active_state != "active" or sub_state != "running"):
                            # Show start button if stopped or in dev mode
                            button_icon = "▶️"
                            button_help = f"Start {config['name']}" if not is_windows() else f"Simulate start {config['name']} (dev mode)"
                            action_text = "start"
                        else:
                            # Service is running, no button for start-only services
                            st.markdown("🔒 Start-only")
                            button_icon = None
                    else:
                        # Regular restart services
                        button_icon = "🔄"
                        button_help = f"Restart {config['name']}" if not is_windows() else f"Simulate restart {config['name']} (dev mode)"
                        action_text = "restart"

                    # Show button if we have an icon
                    if button_icon:
                        restart_key = f"action_{service_id}"
                        if st.button(button_icon, key=restart_key, help=button_help):
                            action_word = "Simulating" if is_windows() else action_text.title() + "ing"
                            with st.spinner(f"{action_word} {config['name']}..."):
                                # Use appropriate service function
                                if action_text == "start":
                                    success = start_service(config["service"])
                                else:
                                    success = restart_service(config["service"])

                                if success:
                                    past_tense = f"{action_text}ed" if action_text.endswith("t") else f"{action_text}ed"
                                    sim_text = f" ({action_text} simulated)" if is_windows() else f" {past_tense}"
                                    title = f"✅ {config['name']}{sim_text} successfully!"
                                    message = f"Service {action_text} operation completed."
                                else:
                                    title = f"❌ Failed to {action_text} {config['name']}"
                                    message = f"Service {action_text} operation failed. Check logs for details."

                                show_operation_result(success, title, message)

        # Show recent console logs button
        logs_key = f"logs_{service_id}"
        if st.button(f"📝 View Logs ({config['name']})", key=logs_key):
            try:
                logs = get_service_logs(config["service"], lines=log_lines)
            except Exception:
                logs = "Logs unavailable"
            show_service_logs(config["name"], logs)

        st.markdown("---")

    # Summary section
    st.markdown("### 📊 Service Summary")

    # Count services by status
    if is_windows():
        status_counts = {"dev_mode": len(services), "running": 0, "stopped": 0, "failed": 0, "other": 0}
    else:
        status_counts = {"running": 0, "stopped": 0, "failed": 0, "other": 0}

        for service_id, config in services.items():
            load_state, active_state, sub_state = get_service_status(config["service"])
            if active_state == "active" and sub_state == "running":
                status_counts["running"] += 1
            elif active_state == "inactive":
                status_counts["stopped"] += 1
            elif active_state == "failed":
                status_counts["failed"] += 1
            else:
                status_counts["other"] += 1

    if is_windows():
        col1, col2 = st.columns(2)
        with col1:
            st.metric("🖥️ Dev Mode", status_counts["dev_mode"])
        with col2:
            st.info("Service monitoring available in Linux production environment")
    else:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("🟢 Running", status_counts["running"])
        with col2:
            st.metric("⚪ Stopped", status_counts["stopped"])
        with col3:
            st.metric("🔴 Failed", status_counts["failed"])
        with col4:
            st.metric("🟡 Other", status_counts["other"])

    # Queue status (if recalc worker exists)
    st.markdown("### 🔄 Queue Status")
    try:
        # Try to get queue status using Django management command
        import os
        import sys

        # Add Django project to path
        django_path = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
        if django_path not in sys.path:
            sys.path.insert(0, django_path)

        # Try to import and run queue status
        try:
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "thetower.backend.towerdb.settings")
            import django

            django.setup()

            from datetime import timedelta

            # Import django timezone under a different name to avoid
            # shadowing the module-level `timezone` name used above.
            from django.utils import timezone as dj_timezone

            from thetower.backend.tourney_results.models import TourneyResult

            # Get queue statistics
            pending_count = TourneyResult.objects.filter(needs_recalc=True).count()
            failed_count = TourneyResult.objects.filter(needs_recalc=True, recalc_retry_count__gte=3).count()

            # Get recent processing stats (last 24h)
            yesterday = dj_timezone.now() - timedelta(days=1)
            recent_processed = TourneyResult.objects.filter(last_recalc_at__gte=yesterday).count()

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("📋 Pending", pending_count)
            with col2:
                st.metric("❌ Failed", failed_count)
            with col3:
                st.metric("✅ Processed (24h)", recent_processed)

        except Exception:
            st.warning("Could not load queue status")

    except Exception:
        st.warning("Queue status unavailable")

    # Zendesk Queue status
    st.markdown("### 🎫 Zendesk Queue Status")
    try:
        # Try to get Zendesk queue status
        try:
            from thetower.backend.sus.models import ModerationRecord

            # Get queue statistics
            pending_count = ModerationRecord.objects.filter(needs_zendesk_ticket=True, zendesk_ticket_id__isnull=True).count()

            # Get failed count (max retries reached, typically 3)
            failed_count = ModerationRecord.objects.filter(
                needs_zendesk_ticket=True, zendesk_ticket_id__isnull=True, zendesk_retry_count__gte=3
            ).count()

            # Get successfully created tickets (last 24h)
            yesterday = dj_timezone.now() - timedelta(days=1)
            recent_created = ModerationRecord.objects.filter(zendesk_ticket_id__isnull=False, zendesk_last_attempt__gte=yesterday).count()

            # Get total tickets ever created
            total_created = ModerationRecord.objects.filter(zendesk_ticket_id__isnull=False).count()

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("📋 Pending", pending_count)
            with col2:
                st.metric("❌ Failed", failed_count)
            with col3:
                st.metric("✅ Created (24h)", recent_created)
            with col4:
                st.metric("🎫 Total Tickets", total_created)

        except Exception:
            st.warning("Could not load Zendesk queue status")

    except Exception:
        st.warning("Zendesk queue status unavailable")

    # Instructions
    with st.expander("ℹ️ About Service Status"):
        st.markdown(
            """
        **Service Status:**
        - 🟢 **Running**: Service is active and working normally
        - ⚪ **Stopped**: Service is inactive but ready to start
        - � **Failed**: Service has failed and needs attention
        - �🟡 **Active**: Service is loaded but may not be running (e.g., one-shot services)
        - ❌ **Not Found**: Service configuration doesn't exist
        - � **Disabled/Masked**: Service is intentionally disabled
        - ⚠️ **Error**: Service has configuration issues

        **Start Time Information:**
        - 🕐 Shows when each service was last started/restarted
        - Displays both absolute time (UTC) and relative time (e.g., "2 hours ago")
        - ⏸️ **Never Started**: Service has never been activated
        - ❓ **Unknown**: Start time information unavailable

        **Actions:**
        - 🔄 **Restart button**: Restart services (most services)
        - ▶️ **Start button**: Start stopped services (import_results, get_results only)
        - 🔒 **Start-only**: Some services can only be started when stopped, not restarted when running
        - Use manual refresh to monitor services and update start times
        - Check the Queue Status for tournament recalculation progress

        **Services:**
        - **Public/Hidden/Admin Sites**: Web applications serving different interfaces
        - **TheTower Bot**: Discord bot for game interactions
        - **Import/Get Results**: Background services that fetch tournament data (start-only)
        - **Recalc Worker**: Processes tournament position recalculations

        **Development Note:**
        - On Windows: Service status and start times are simulated for development purposes
        - On Linux: Actual systemctl service status and timestamps are displayed
        """
        )


if __name__ == "__main__":
    service_status_page()
