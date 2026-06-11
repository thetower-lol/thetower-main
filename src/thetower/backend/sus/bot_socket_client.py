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


async def refresh_verification_discord_messages(stem: str, mod_user: str) -> None:
    """Request the Discord bot to refresh verification messages for a submission.

    This is called after web-based mod actions to ensure Discord embeds update
    consistently regardless of whether the mod used Discord or the web interface.

    Communicates directly with the bot's socket server (TCP on Windows, Unix on Linux).

    Args:
        stem: Submission identifier
        mod_user: Moderator identifier (e.g. "discord:123456" or "web_username")
    """
    request = {"action": "refresh_verification_messages", "stem": stem, "mod_user": mod_user}

    # Inject auth token if configured
    if DISCORD_BOT_SOCKET_TOKEN:
        request["token"] = DISCORD_BOT_SOCKET_TOKEN

    try:
        response = await _send_socket_request(request)
        if response and response.get("status") == "ok":
            logger.info(f"Successfully refreshed Discord messages for submission {stem}")
            # Opportunistically process any pending retries on successful send
            await _process_pending_retries()
        else:
            error_msg = response.get("error", "Unknown error") if response else "No response"
            logger.warning(f"Failed to refresh Discord messages: {error_msg}")
            # Save for retry
            await _save_failed_refresh(stem, mod_user, error_msg)
    except Exception as e:
        # Log but don't fail the web request if Discord updates fail
        logger.warning(f"Failed to refresh Discord messages for submission {stem}: {e}")
        # Save for retry
        await _save_failed_refresh(stem, mod_user, str(e))


async def _save_failed_refresh(stem: str, mod_user: str, error: str) -> None:
    """Save a failed Discord refresh attempt for later retry.

    Args:
        stem: Submission identifier
        mod_user: Moderator identifier
        error: Error message
    """
    from asgiref.sync import sync_to_async

    def save_pending():
        from thetower.backend.sus import verification

        verification.save_pending_discord_refresh(stem, mod_user, error)

    try:
        await sync_to_async(save_pending)()
        logger.info(f"Saved pending Discord refresh for submission {stem}")
    except Exception as ex:
        logger.error(f"Failed to save pending Discord refresh: {ex}", exc_info=True)


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


async def _process_pending_retries(max_retries: int = 10) -> None:
    """Opportunistically process pending Discord refresh retries.

    Called after a successful refresh to piggyback on successful bot communication.
    Processes up to max_retries pending refreshes, deleting successful ones and
    incrementing retry count for failures.

    Args:
        max_retries: Maximum number of pending refreshes to process in one call
    """
    from asgiref.sync import sync_to_async

    def get_pending():
        from thetower.backend.sus import verification

        return verification.get_pending_discord_refreshes(limit=max_retries)

    try:
        pending_refreshes = await sync_to_async(get_pending)()
        if not pending_refreshes:
            return

        logger.info(f"Processing {len(pending_refreshes)} pending Discord refresh retries")

        for refresh in pending_refreshes:
            refresh_id = refresh["id"]
            stem = refresh["stem"]
            mod_user = refresh["mod_user"]

            request = {"action": "refresh_verification_messages", "stem": stem, "mod_user": mod_user}

            # Inject auth token if configured
            if DISCORD_BOT_SOCKET_TOKEN:
                request["token"] = DISCORD_BOT_SOCKET_TOKEN

            try:
                response = await _send_socket_request(request)
                if response and response.get("status") == "ok":
                    # Success - delete from pending queue
                    def delete_pending():
                        from thetower.backend.sus import verification

                        verification.delete_pending_discord_refresh(refresh_id)

                    await sync_to_async(delete_pending)()
                    logger.info(f"Successfully retried Discord refresh for submission {stem} (pending ID {refresh_id})")
                else:
                    # Still failing - increment retry count
                    error_msg = response.get("error", "Unknown error") if response else "No response"

                    def increment_retry():
                        from thetower.backend.sus import verification

                        verification.increment_pending_discord_refresh_retry_count(refresh_id)

                    await sync_to_async(increment_retry)()
                    logger.warning(f"Retry failed for submission {stem}: {error_msg}")
            except Exception as e:
                # Retry failed - increment retry count
                def increment_retry():
                    from thetower.backend.sus import verification

                    verification.increment_pending_discord_refresh_retry_count(refresh_id)

                try:
                    await sync_to_async(increment_retry)()
                except Exception as ex:
                    logger.error(f"Failed to increment retry count for pending refresh {refresh_id}: {ex}")
                logger.warning(f"Retry failed for submission {stem}: {e}")

    except Exception as e:
        # Don't fail the main request if retry processing fails
        logger.error(f"Failed to process pending retries: {e}", exc_info=True)
