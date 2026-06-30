import os
from datetime import datetime, timezone

from django.conf import settings
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ApiKey, ModerationRecord
from .serializers import BanPlayerSerializer


class APIKeyPermission(permissions.BasePermission):
    """Custom permission to check for a valid API key in the header and user permissions."""

    def has_permission(self, request, view):
        api_key = request.headers.get("X-API-KEY")
        if not api_key:
            return False
        try:
            key_obj = ApiKey.objects.get(key=api_key, active=True)
        except ApiKey.DoesNotExist:
            return False
        # Update last_used_at
        key_obj.last_used_at = datetime.now(timezone.utc)
        key_obj.save(update_fields=["last_used_at"])
        user = key_obj.user
        # Check user permission for changing ModerationRecord
        if not user.has_perm("sus.add_moderationrecord"):
            return False
        request.api_key_user = user
        request.api_key_obj = key_obj
        # Set request.user for simple_history middleware to pick up
        request.user = user
        return True


def log_api_request(api_key_user, player_id, action, success, note=None):
    data_dir = getattr(settings, "DATA_DIR", None) or os.environ.get("DATA_DIR", ".")
    log_path = os.path.join(str(data_dir), "api_requests.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        status_str = "SUCCESS" if success else "FAIL"
        note_str = note.replace("\n", " ") if note else ""
        f.write(f"{ts}\t{api_key_user}\t{player_id}\t{action}\t{status_str}\t{note_str}\n")


class BanPlayerAPI(APIView):
    permission_classes = [APIKeyPermission]

    def post(self, request):
        serializer = BanPlayerSerializer(data=request.data)
        if not serializer.is_valid():
            log_api_request(
                getattr(request, "api_key_user", "UNKNOWN"),
                request.data.get("player_id", ""),
                request.data.get("action", ""),
                False,
                note="Invalid data",
            )
            return Response({"detail": "Invalid data", "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        # Check permission again for extra safety (DRF best practice)
        user = getattr(request, "api_key_user", None)
        if not user or not user.has_perm("sus.add_moderationrecord"):
            log_api_request(user or "UNKNOWN", request.data.get("player_id", ""), request.data.get("action", ""), False, note="Permission denied")
            return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        player_id = serializer.validated_data["player_id"]
        action = serializer.validated_data["action"]
        note = serializer.validated_data.get("note", "")
        api_key_user = getattr(request, "api_key_user", None)
        api_key_obj = getattr(request, "api_key_obj", None)

        try:
            # Map API actions to ModerationRecord types
            action_to_type = {
                "ban": ModerationRecord.ModerationType.BAN,
                "sus": ModerationRecord.ModerationType.SUS,
                "unban": ModerationRecord.ModerationType.BAN,  # Will create inactive record
                "unsus": ModerationRecord.ModerationType.SUS,  # Will create inactive record
            }

            if action not in action_to_type:
                log_api_request(api_key_user, player_id, action, False, note="Unknown action")
                return Response({"detail": "Unknown action."}, status=status.HTTP_400_BAD_REQUEST)

            moderation_type = action_to_type[action]

            # For unban/unsus actions, check if there's an active moderation to resolve
            if action in ["unban", "unsus"]:
                active_records = ModerationRecord.objects.filter(
                    tower_id=player_id, moderation_type=moderation_type, resolved_at__isnull=True  # Active = not resolved
                )
                if not active_records.exists():
                    log_api_request(api_key_user, player_id, action, False, note=f"No active {moderation_type} record to resolve")
                    return Response(
                        {"detail": f"No active {moderation_type} record found for player {player_id}."}, status=status.HTTP_400_BAD_REQUEST
                    )

                # Resolve the active record(s)
                for record in active_records:
                    # Append resolution note to existing reason if provided
                    if note:
                        if record.reason:
                            record.reason = f"{record.reason}\n\nResolved: {note}"
                        else:
                            record.reason = f"Resolved: {note}"
                        record.save()

                    record.resolve(resolved_by_api_key=api_key_obj)
                action_desc = f"un{moderation_type.lower()}"
            else:
                # Create new active moderation record
                ModerationRecord.create_for_api(tower_id=player_id, moderation_type=moderation_type, api_key=api_key_obj, reason=note)
                action_desc = moderation_type.lower()

                # Notify bot immediately for bans so the verified role is removed in real time
                if action == "ban":
                    try:
                        import asyncio

                        from thetower.backend.sus.bot_socket_client import DISCORD_BOT_SOCKET_TOKEN, _send_socket_request

                        enforce_request = {"action": "enforce_ban", "tower_id": player_id}
                        if DISCORD_BOT_SOCKET_TOKEN:
                            enforce_request["token"] = DISCORD_BOT_SOCKET_TOKEN
                        asyncio.run(_send_socket_request(enforce_request, timeout=3.0))
                    except Exception as notify_exc:
                        # Non-fatal: bot notification is best-effort; ban record is already saved
                        import logging

                        logging.getLogger(__name__).warning("enforce_ban socket notify failed for %s: %s", player_id, notify_exc)

            log_api_request(api_key_user, player_id, action, True, note=note)
            return Response({"detail": f"Player {player_id} marked as {action_desc}."}, status=status.HTTP_200_OK)
        except Exception as e:
            log_api_request(api_key_user, player_id, action, False, note=str(e))
            return Response({"detail": "Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
