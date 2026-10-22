"""Client for communicating with the Discord bot's socket server."""

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Bot socket server configuration from environment
DISCORD_BOT_SOCKET_PORT = int(os.getenv("DISCORD_BOT_SOCKET_PORT", "19876"))
DISCORD_BOT_SOCKET_TOKEN = os.getenv("BOT_SOCKET_TOKEN")


async def get_discord_user(user_id: str) -> dict[str, str] | None:
    """Resolve a Discord user ID to display name fields.

    Args:
        user_id: Numeric Discord snowflake (string or int).  May include a
                 platform prefix like "discord:123456" — the prefix is stripped.

    Returns:
        Dict with username, global_name, display_name, or None if unavailable.
    """
    # Strip optional "discord:" prefix
    clean_id = user_id.split(":")[-1].strip() if ":" in user_id else user_id.strip()
    if not clean_id.isdigit():
        return None

    request = {"action": "get_discord_user", "user_id": clean_id}
    if DISCORD_BOT_SOCKET_TOKEN:
        request["token"] = DISCORD_BOT_SOCKET_TOKEN

    try:
        response = await _send_socket_request(request)
        if response and response.get("status") == "ok":
            return {
                "username": response.get("username", ""),
                "global_name": response.get("global_name", ""),
                "display_name": response.get("display_name", ""),
            }
    except Exception as exc:
        logger.debug("get_discord_user failed for %s: %s", user_id, exc)
    return None


async def refresh_verification_discord_messages(stem: str, mod_user: str) -> str | None:
    """Ask the Discord bot to refresh a submission's verification messages now and return the log message's URL.

    For the web's manual "Discord refresh" button, which shows the result. Ordinary changes need no call: they emit
    the verification.updated domain event (verification.log_submission_update).

    Communicates directly with the bot's socket server (TCP on Windows, Unix on Linux).

    Args:
        stem: Submission identifier
        mod_user: Moderator identifier (e.g. "discord:123456" or "web_username")

    Returns:
        Direct Discord URL to the log message if available, otherwise None.
    """
    request = {"action": "refresh_verification_messages", "stem": stem, "mod_user": mod_user}

    # Inject auth token if configured
    if DISCORD_BOT_SOCKET_TOKEN:
        request["token"] = DISCORD_BOT_SOCKET_TOKEN

    try:
        response = await _send_socket_request(request)
        if response and response.get("status") == "ok":
            logger.info(f"Successfully refreshed Discord messages for submission {stem}")
            return response.get("discord_url")
        error_msg = response.get("message", response.get("error", "Unknown error")) if response else "No response"
        logger.warning(f"Failed to refresh Discord messages: {error_msg}")
        return None
    except Exception as e:
        # Log but don't fail the web request if Discord updates fail
        logger.warning(f"Failed to refresh Discord messages for submission {stem}: {e}")
        return None


async def _send_socket_request(request: dict[str, Any], timeout: float = 3.0) -> dict[str, Any] | None:
    """Send a JSON request to the bot's socket server and return the response.

    On Windows: TCP socket at localhost:PORT (default 19876)
    On Linux: Unix domain socket at /run/discord-bot/config.sock

    Args:
        request: JSON-serializable dict with "action" key
        timeout: Timeout in seconds for socket operations

    Returns:
        Parsed JSON response dict, or None if bot is unreachable
    """
    try:
        # Connect to socket based on OS
        if os.name == "nt":
            # Windows: TCP socket
            reader, writer = await asyncio.wait_for(asyncio.open_connection("localhost", DISCORD_BOT_SOCKET_PORT), timeout=timeout)
        else:
            # Linux: Unix domain socket
            socket_path = "/run/discord-bot/config.sock"
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(path=socket_path), timeout=timeout)

        try:
            # Send request
            request_json = json.dumps(request) + "\n"
            writer.write(request_json.encode("utf-8"))
            await asyncio.wait_for(writer.drain(), timeout=timeout)

            # Read response
            response_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not response_line:
                logger.warning("Empty response from bot socket")
                return None

            # Parse JSON
            response = json.loads(response_line.decode("utf-8"))
            return response

        finally:
            writer.close()
            await writer.wait_closed()

    except asyncio.TimeoutError:
        logger.debug("Bot socket timeout")
        return None
    except (ConnectionRefusedError, FileNotFoundError):
        logger.debug("Bot socket unavailable")
        return None
    except Exception as e:
        logger.warning(f"Socket client error: {e}")
        return None
