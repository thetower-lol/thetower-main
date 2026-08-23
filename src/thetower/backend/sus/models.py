import datetime
import secrets
from typing import Optional

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
from django.utils import timezone
from simple_history.models import HistoricalRecords


class ApiKey(models.Model):
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="api_keys")
    key = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        super().save(*args, **kwargs)

    @staticmethod
    def generate_key():
        return secrets.token_urlsafe(48)

    def invalidate(self):
        self.active = False
        self.invalidated_at = timezone.now()
        self.save()

    def key_suffix(self):
        return self.key[-8:] if self.key else ""

    def __str__(self):
        return f"API Key for {self.user.username} (…{self.key_suffix()})"


class KnownPlayer(models.Model):
    name = models.CharField(max_length=100, blank=True, null=True, db_index=True, help_text="Player's friendly name, e.g. common discord handle")
    discord_id = models.CharField(max_length=50, blank=True, null=True, help_text="DEPRECATED: Discord numeric id (use linked_accounts instead)")
    creator_code = models.CharField(max_length=50, blank=True, null=True, help_text="Creator/supporter code to promote in shop")
    approved = models.BooleanField(blank=False, null=False, default=True, help_text="Has this entry been validated?")
    django_user = models.OneToOneField(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="known_player", help_text="Linked Django user account"
    )

    def __str__(self):
        user_info = f" ({self.django_user.username})" if self.django_user else ""
        primary_instance = self.game_instances.filter(primary=True).first()
        if primary_instance:
            primary_id = primary_instance.player_ids.filter(primary=True).first()
            id_str = primary_id.id if primary_id else ""
        else:
            id_str = ""
        return f"{self.name} ({id_str}){user_info}"

    def save(self, *args, **kwargs):
        self.nname = self.name.strip()
        super().save(*args, **kwargs)

    @property
    def is_linked_to_django_user(self):
        """Check if this KnownPlayer is linked to a Django user account."""
        return self.django_user is not None

    @classmethod
    def get_by_discord_id(cls, discord_id):
        """Backward compat helper: lookup by discord ID via LinkedAccount."""
        linked_account = LinkedAccount.objects.filter(platform=LinkedAccount.Platform.DISCORD, account_id=str(discord_id), active=True).first()
        return linked_account.player if linked_account else None

    def get_primary_discord_accounts(self):
        """Get all Discord LinkedAccount objects for this player."""
        return self.linked_accounts.filter(platform=LinkedAccount.Platform.DISCORD)

    def get_primary_game_instance(self):
        """Get the primary game instance (determines roles)."""
        return self.game_instances.filter(primary=True).first()

    def get_all_player_ids(self):
        """Get ALL PlayerIds across all game instances."""
        return PlayerId.objects.filter(game_instance__player=self)

    def get_primary_player_id(self):
        """Get primary PlayerId from primary GameInstance."""
        primary_instance = self.get_primary_game_instance()
        if primary_instance:
            return primary_instance.player_ids.filter(primary=True).first()
        return None

    def get_display_name(self) -> Optional[str]:
        """Return the best available display name for this player.

        Walks primary GameInstance → primary Discord LinkedAccount (via role_source_instance)
        → display_name. Falls back to self.name, then None.
        """
        primary_instance = self.get_primary_game_instance()
        if primary_instance:
            discord_account = self.linked_accounts.filter(
                platform=LinkedAccount.Platform.DISCORD,
                role_source_instance=primary_instance,
                active=True,
            ).first()
            if discord_account and discord_account.display_name:
                return discord_account.display_name
        return self.name or None

    history = HistoricalRecords()


class LinkedAccount(models.Model):
    """Social media accounts linked to a KnownPlayer."""

    class Platform(models.TextChoices):
        DISCORD = "discord", "Discord"
        REDDIT = "reddit", "Reddit"
        TWITTER = "twitter", "Twitter"
        TWITCH = "twitch", "Twitch"

    player = models.ForeignKey(KnownPlayer, on_delete=models.CASCADE, related_name="linked_accounts", help_text="The player this account belongs to")
    platform = models.CharField(max_length=20, choices=Platform.choices, help_text="Social media platform")
    account_id = models.CharField(max_length=100, help_text="Platform-specific account ID or username")
    display_name = models.CharField(max_length=100, blank=True, null=True, help_text="Display name on this platform (optional)")
    verified = models.BooleanField(default=True, help_text="DEPRECATED: always True; an active link is what makes an account verified (use 'active' instead)")
    verified_at = models.DateTimeField(null=True, blank=True, default=timezone.now, help_text="When this account was verified")
    primary = models.BooleanField(default=False, help_text="Is this the primary account for this platform? (only one per player per platform)")
    active = models.BooleanField(
        default=True, help_text="Is this account link active? Inactive accounts are retired/disabled and hidden from regular users."
    )
    role_source_instance = models.ForeignKey(
        "GameInstance",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="linked_accounts_receiving_roles",
        help_text="Which game instance provides Discord roles for this account. Null = no roles assigned.",
    )

    class Meta:
        unique_together = [("platform", "account_id")]
        indexes = [
            models.Index(fields=["platform", "account_id"]),
        ]

    def __str__(self):
        return f"{self.get_platform_display()}: {self.account_id}"

    def get_role_instance(self):
        """Get the GameInstance that determines this account's roles. Returns None if no roles should be assigned."""
        if self.role_source_instance:
            # Verify instance still belongs to same player
            if self.role_source_instance.player_id != self.player_id:
                # Instance was transferred to different player, clear the reference
                self.role_source_instance = None
                self.save(update_fields=["role_source_instance"])
                return None
        return self.role_source_instance

    def save(self, *args, **kwargs):
        """Ensure only one primary account per player per platform."""
        if self.primary:
            # Clear any other primary accounts for this player on this platform
            LinkedAccount.objects.filter(player=self.player, platform=self.platform, primary=True).exclude(pk=self.pk).update(primary=False)
        super().save(*args, **kwargs)

    history = HistoricalRecords()


class GameInstance(models.Model):
    """A single installation/account in The Tower game."""

    player = models.ForeignKey(
        KnownPlayer, on_delete=models.CASCADE, related_name="game_instances", help_text="The player who owns this game instance"
    )
    name = models.CharField(max_length=50, blank=True, null=True, help_text="Friendly name for this instance (e.g., 'Instance 1', 'Instance 2')")
    primary = models.BooleanField(default=False, help_text="Primary instance - determines Discord roles for all linked accounts")
    active = models.BooleanField(
        default=True,
        help_text="Whether this game instance is active (visible to regular users); inactive instances are hidden from users but preserved for mod history",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-primary", "created_at"]

    def __str__(self):
        primary_marker = " (PRIMARY)" if self.primary else ""
        name_str = f" - {self.name}" if self.name else ""
        return f"{self.player.name}{name_str}{primary_marker}"

    def save(self, *args, **kwargs):
        # Ensure only one primary instance per player
        if self.primary:
            GameInstance.objects.filter(player=self.player).exclude(pk=self.pk).update(primary=False)
        super().save(*args, **kwargs)

    history = HistoricalRecords()


class PlayerId(models.Model):
    id = models.CharField(max_length=32, primary_key=True, help_text="Player id from The Tower, pk")
    game_instance = models.ForeignKey(
        GameInstance,
        null=True,
        blank=True,
        related_name="player_ids",
        on_delete=models.CASCADE,
        help_text="The game instance this Tower ID belongs to",
    )
    player = models.ForeignKey(
        KnownPlayer,
        null=True,
        blank=True,
        related_name="ids",
        on_delete=models.CASCADE,
        help_text="DEPRECATED: Direct link to player (use game_instance.player)",
    )
    primary = models.BooleanField(default=False, help_text="Primary ID for this game instance (in case of ID changes)")
    notes = models.TextField(null=True, blank=True, max_length=1000, help_text="Documentation about this Tower ID (e.g., ID change notes)")

    def __str__(self):
        return f"{self.id}"

    def save(self, *args, **kwargs):
        # Ensure ID is always uppercase
        self.id = self.id.upper()
        super().save(*args, **kwargs)

    def save_base(self, *args, force_insert=False, **kwargs):
        if force_insert and self.primary:
            # Ensure only one primary ID per game instance
            self.game_instance.player_ids.filter(~Q(id=self.id), primary=True).update(primary=False)

        return super().save_base(*args, **kwargs)

    history = HistoricalRecords()


class ModerationRecord(models.Model):
    """
    Unified moderation system - game instance centric.
    - For verified players: links to GameInstance (what is banned) AND stores tower_id (what triggered it)
    - For unverified players: stores only tower_id (game_instance=null)
    - Auto-linking: when unverified player links Discord, existing moderation gets game_instance updated
    - Identity tracking: get KnownPlayer via game_instance.player
    """

    # Moderation Types
    class ModerationType(models.TextChoices):
        SUS = "sus", "Suspicious"
        BAN = "ban", "Banned"
        SHUN = "shun", "Shunned (Discord)"
        SOFT_BAN = "soft_ban", "Soft Banned"

    # Moderation Sources
    class ModerationSource(models.TextChoices):
        MANUAL = "manual", "Manual (Admin)"
        API = "api", "API"
        BOT = "bot", "Discord Bot"
        AUTOMATED = "automated", "Automated System"

    # Moderation timeline - using datetime fields instead of status
    started_at = models.DateTimeField(default=timezone.now, help_text="When this moderation action began")

    # Core identification - game instance centric
    game_instance = models.ForeignKey(
        "GameInstance",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="moderation_records",
        help_text="GameInstance being moderated (null for unverified players)",
    )
    tower_id = models.CharField(
        max_length=32, db_index=True, help_text="Tower ID that triggered moderation (always stored regardless of verification status)"
    )

    # Moderation details
    moderation_type = models.CharField(max_length=20, choices=ModerationType.choices, help_text="Type of moderation action")
    source = models.CharField(
        max_length=20, choices=ModerationSource.choices, default=ModerationSource.MANUAL, help_text="Source of the moderation action"
    )

    # Audit trail - dual attribution system
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # Admin interface attribution
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_moderation_records",
        help_text="Django admin user who created this moderation record",
    )

    # Bot/Discord attribution
    created_by_discord_id = models.CharField(
        max_length=20, null=True, blank=True, help_text="Discord ID of user who created this record via bot command"
    )

    # API attribution
    created_by_api_key = models.ForeignKey(
        ApiKey,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_moderation_records",
        help_text="API key used to create this record",
    )

    # Resolution audit trail
    resolved_at = models.DateTimeField(null=True, blank=True, help_text="When this moderation was resolved")

    # Admin interface resolution
    resolved_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_moderation_records",
        help_text="Django admin user who resolved this moderation record",
    )

    # Bot/Discord resolution
    resolved_by_discord_id = models.CharField(
        max_length=20, null=True, blank=True, help_text="Discord ID of user who resolved this record via bot command"
    )

    # API resolution
    resolved_by_api_key = models.ForeignKey(
        ApiKey,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_moderation_records",
        help_text="API key used to resolve this record",
    )

    # Notes and context
    reason = models.TextField(null=True, blank=True, max_length=1000, help_text="Reason for this moderation action")

    # Zendesk ticket creation queue fields
    needs_zendesk_ticket = models.BooleanField(default=True, help_text="Moderation record needs a Zendesk ticket created")
    zendesk_ticket_id = models.IntegerField(null=True, blank=True, help_text="Zendesk ticket ID once created")
    zendesk_last_attempt = models.DateTimeField(null=True, blank=True, help_text="When Zendesk ticket creation was last attempted")
    zendesk_retry_count = models.SmallIntegerField(default=0, help_text="Number of failed Zendesk ticket creation attempts")

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            # Fast tower_id lookups for tournament filtering
            models.Index(fields=["tower_id", "resolved_at"]),
            models.Index(fields=["tower_id", "moderation_type", "resolved_at"]),
            # Game instance lookups (primary moderation check)
            models.Index(fields=["game_instance", "resolved_at"]),
            models.Index(fields=["game_instance", "moderation_type", "resolved_at"]),
            # Source and type filtering
            models.Index(fields=["source", "moderation_type"]),
            # Time-based queries
            models.Index(fields=["started_at"]),
            models.Index(fields=["created_at"]),
            # Zendesk queue processing
            models.Index(fields=["needs_zendesk_ticket", "zendesk_retry_count", "created_at"], name="idx_zendesk_queue"),
            models.Index(fields=["needs_zendesk_ticket"], name="idx_needs_zendesk"),
        ]

    def __str__(self):
        if self.game_instance:
            player_info = f"{self.game_instance.player.name} ({self.game_instance.name})"
        else:
            player_info = f"Tower ID {self.tower_id}"
        status = "Active" if self.is_active else "Resolved"
        return f"{self.get_moderation_type_display()} - {player_info} ({status})"

    def resolve(self, resolved_by_user=None, resolved_by_discord_id=None, resolved_by_api_key=None):
        """Mark this moderation record as resolved and queue tournament recalculation"""
        self.resolved_at = timezone.now()
        self.resolved_by = resolved_by_user
        self.resolved_by_discord_id = resolved_by_discord_id
        self.resolved_by_api_key = resolved_by_api_key
        self.save()
        # Queue recalculation since moderation status changed
        self._queue_recalculation()

    @property
    def is_active(self):
        """Check if this moderation is currently active (not resolved)"""
        return self.resolved_at is None

    @property
    def is_resolved(self):
        """Check if this moderation has been resolved"""
        return self.resolved_at is not None

    @property
    def created_by_display(self):
        """Human-readable string showing who created this record"""
        if self.created_by:
            return f"Admin: {self.created_by.username}"
        elif self.created_by_discord_id:
            return f"Discord: {self.created_by_discord_id}"
        elif self.created_by_api_key:
            return f"API: {self.created_by_api_key.user.username} (…{self.created_by_api_key.key_suffix()})"
        return "System"

    @property
    def resolved_by_display(self):
        """Human-readable string showing who resolved this record"""
        if not self.resolved_at:
            return "-"
        if self.resolved_by:
            return f"Admin: {self.resolved_by.username}"
        elif self.resolved_by_discord_id:
            return f"Discord: {self.resolved_by_discord_id}"
        elif self.resolved_by_api_key:
            return f"API: {self.resolved_by_api_key.user.username} (…{self.resolved_by_api_key.key_suffix()})"
        return "System"

    @staticmethod
    def _auto_link_game_instance(tower_id):
        """Helper to find and return GameInstance for a tower_id, or None if not found."""
        try:
            player_id_obj = PlayerId.objects.select_related("game_instance").get(id=tower_id)
            return player_id_obj.game_instance if player_id_obj.game_instance else None
        except PlayerId.DoesNotExist:
            return None

    @classmethod
    def create_for_admin(cls, tower_id, moderation_type, admin_user, reason=None, **kwargs):
        """Create a moderation record from admin interface"""
        # Don't override needs_zendesk_ticket if explicitly set
        if "needs_zendesk_ticket" not in kwargs:
            kwargs["needs_zendesk_ticket"] = True

        # Auto-link to GameInstance if one exists for this tower_id
        if "game_instance" not in kwargs:
            kwargs["game_instance"] = cls._auto_link_game_instance(tower_id)

        record = cls.objects.create(
            tower_id=tower_id, moderation_type=moderation_type, source=cls.ModerationSource.MANUAL, created_by=admin_user, reason=reason, **kwargs
        )
        record._queue_recalculation()
        return record

    @classmethod
    def create_for_bot(cls, tower_id, moderation_type, discord_id, reason=None, **kwargs):
        """Create a moderation record from Discord bot"""
        # Don't override needs_zendesk_ticket if explicitly set
        if "needs_zendesk_ticket" not in kwargs:
            kwargs["needs_zendesk_ticket"] = True

        # Auto-link to GameInstance if one exists for this tower_id
        if "game_instance" not in kwargs:
            kwargs["game_instance"] = cls._auto_link_game_instance(tower_id)

        record = cls.objects.create(
            tower_id=tower_id,
            moderation_type=moderation_type,
            source=cls.ModerationSource.BOT,
            created_by_discord_id=discord_id,
            reason=reason,
            **kwargs,
        )
        record._queue_recalculation()
        return record

    @classmethod
    def create_for_api(cls, tower_id, moderation_type, api_key, reason=None, **kwargs):
        """Create a moderation record from API with comprehensive business logic"""
        from django.utils import timezone

        # API-sourced records should NOT create Zendesk tickets to avoid circular loops
        if "needs_zendesk_ticket" not in kwargs:
            kwargs["needs_zendesk_ticket"] = False

        # Auto-link to GameInstance if one exists for this tower_id
        if "game_instance" not in kwargs:
            kwargs["game_instance"] = cls._auto_link_game_instance(tower_id)

        # Get existing active records for this player
        existing_active = cls.objects.filter(tower_id=tower_id, resolved_at__isnull=True)  # Only active records

        existing_sus = existing_active.filter(moderation_type="sus").first()
        existing_ban = existing_active.filter(moderation_type="ban").first()
        existing_same_type = existing_active.filter(moderation_type=moderation_type).first()

        if moderation_type == "sus":
            # API SUS creation rules
            if existing_sus:
                if existing_sus.source == "api":
                    # Already sus by API - return success message
                    return {"record": existing_sus, "created": False, "message": f"Player {tower_id} is already marked as suspicious by API"}
                else:
                    # Manual sus exists - reinforce with API
                    api_note = f"Reinforced by API (key: {api_key.key_suffix})"
                    if existing_sus.reason and "Reinforced by API" not in existing_sus.reason:
                        existing_sus.reason = f"{existing_sus.reason}\n{api_note}"
                    elif not existing_sus.reason:
                        existing_sus.reason = api_note

                    # Update to API source
                    existing_sus.source = cls.ModerationSource.API
                    existing_sus.created_by_api_key = api_key
                    existing_sus.save()

                    return {
                        "record": existing_sus,
                        "created": False,
                        "message": f"Reinforced existing manual sus record for player {tower_id} with API",
                    }
            else:
                # No existing sus - create new
                new_record = cls.objects.create(
                    tower_id=tower_id,
                    moderation_type=moderation_type,
                    source=cls.ModerationSource.API,
                    created_by_api_key=api_key,
                    reason=reason,
                    **kwargs,
                )
                new_record._queue_recalculation()
                return {"record": new_record, "created": True, "message": f"Created new sus record for player {tower_id}"}

        elif moderation_type == "ban":
            # API BAN creation rules
            if existing_ban:
                if existing_ban.source == "api":
                    # Already banned by API - return success message
                    return {"record": existing_ban, "created": False, "message": f"Player {tower_id} is already banned by API"}
                else:
                    # Manual ban exists - reinforce with API
                    api_note = f"Reinforced by API (key: {api_key.key_suffix})"
                    if existing_ban.reason and "Reinforced by API" not in existing_ban.reason:
                        existing_ban.reason = f"{existing_ban.reason}\n{api_note}"
                    elif not existing_ban.reason:
                        existing_ban.reason = api_note

                    # Update to API source
                    existing_ban.source = cls.ModerationSource.API
                    existing_ban.created_by_api_key = api_key
                    existing_ban.save()

                    return {
                        "record": existing_ban,
                        "created": False,
                        "message": f"Reinforced existing manual ban record for player {tower_id} with API",
                    }
            else:
                # Resolve any existing sus records first
                if existing_sus:
                    # Append resolution info to existing reason
                    resolution_info = f"Automatically resolved due to ban escalation (API key: {api_key.key_suffix})"
                    if existing_sus.reason:
                        existing_sus.reason = f"{existing_sus.reason}\n\n{resolution_info}"
                    else:
                        existing_sus.reason = resolution_info

                    existing_sus.resolved_at = timezone.now()
                    existing_sus.resolved_by_api_key = api_key
                    existing_sus.save()

                # Create new ban record
                new_record = cls.objects.create(
                    tower_id=tower_id,
                    moderation_type=moderation_type,
                    source=cls.ModerationSource.API,
                    created_by_api_key=api_key,
                    reason=reason,
                    **kwargs,
                )
                new_record._queue_recalculation()

                message = f"Created new ban record for player {tower_id}"
                if existing_sus:
                    message += " and resolved existing sus record"

                return {"record": new_record, "created": True, "message": message}

        else:
            # Other moderation types (shun, soft_ban, etc.)
            if existing_same_type:
                # Reinforce existing record
                api_note = f"Reinforced by API (key: {api_key.key_suffix})"
                if existing_same_type.reason and "Reinforced by API" not in existing_same_type.reason:
                    existing_same_type.reason = f"{existing_same_type.reason}\n{api_note}"
                elif not existing_same_type.reason:
                    existing_same_type.reason = api_note

                existing_same_type.source = cls.ModerationSource.API
                existing_same_type.created_by_api_key = api_key
                existing_same_type.save()

                return {
                    "record": existing_same_type,
                    "created": False,
                    "message": f"Reinforced existing {moderation_type} record for player {tower_id} with API",
                }
            else:
                # Create new record
                new_record = cls.objects.create(
                    tower_id=tower_id,
                    moderation_type=moderation_type,
                    source=cls.ModerationSource.API,
                    created_by_api_key=api_key,
                    reason=reason,
                    **kwargs,
                )
                new_record._queue_recalculation()
                return {"record": new_record, "created": True, "message": f"Created new {moderation_type} record for player {tower_id}"}

    @classmethod
    def get_active_moderation_ids(cls, moderation_type):
        """Get tower_ids with active moderation of specified type"""
        return set(
            cls.objects.filter(moderation_type=moderation_type, resolved_at__isnull=True).values_list("tower_id", flat=True)  # Active = not resolved
        )

    def _queue_recalculation(self):
        """Queue recalculation for tournaments involving this moderation record.

        With GameInstance-level moderation, we need to mark tournaments for ALL
        tower_ids in the same GameInstance, not just the one in the moderation record.
        """
        try:
            from django.db.models import Q

            from ..tourney_results.models import TourneyResult

            # Get all tower_ids in the same GameInstance (if linked)
            if self.game_instance:
                # Get all tower_ids in this GameInstance
                tower_ids = list(self.game_instance.player_ids.values_list("id", flat=True))
            else:
                # No GameInstance - just use the single tower_id from this record
                try:
                    player_id_obj = PlayerId.objects.select_related("game_instance").get(id=self.tower_id)
                    if player_id_obj.game_instance:
                        # Tower ID got linked since record was created - use all IDs in that instance
                        tower_ids = list(player_id_obj.game_instance.player_ids.values_list("id", flat=True))
                    else:
                        tower_ids = [self.tower_id]
                except PlayerId.DoesNotExist:
                    # Tower ID doesn't exist yet - just use it directly
                    tower_ids = [self.tower_id]

            # Mark affected tournaments as needing recalculation
            q_filter = Q()
            for tid in tower_ids:
                q_filter |= Q(rows__player_id=tid)

            TourneyResult.objects.filter(q_filter).distinct().update(needs_recalc=True, recalc_retry_count=0)
        except Exception:
            # Swallow exceptions - recalculation queuing should not block moderation operations
            pass

    history = HistoricalRecords()


class SusPerson(models.Model):
    player_id = models.CharField(max_length=32, primary_key=True, help_text="Player id from The Tower, pk")
    name = models.CharField(max_length=100, blank=True, null=True, help_text="Player's friendly name, e.g. common discord handle")
    notes = models.TextField(null=True, blank=True, max_length=1000, help_text="Additional comments")
    shun = models.BooleanField(
        null=False,
        blank=False,
        default=False,
        help_text="Are they shunned from the Discord? If checked, user won't appear in leaderboards or earn tourney roles.",
    )
    sus = models.BooleanField(
        null=False, blank=False, default=True, help_text="Is the person sus? If checked, they will be removed from the results on the public website."
    )
    soft_banned = models.BooleanField(null=False, blank=False, default=False, help_text="Soft-banned by Pog. For internal use.")
    banned = models.BooleanField(null=False, blank=False, default=False, help_text="Banned by support. For internal use.")
    # Mark whether this ban/sus was set by the API. These are provenance flags only.
    api_ban = models.BooleanField(null=False, blank=False, default=False, help_text="Was the ban set by the API?")
    api_sus = models.BooleanField(null=False, blank=False, default=False, help_text="Was the sus flag set by the API?")

    created = models.DateTimeField(auto_now_add=datetime.datetime.now, null=False, editable=False, db_index=True)
    modified = models.DateTimeField(auto_now=datetime.datetime.now, null=False, editable=False)

    class Meta:
        ordering = ("-modified",)

    history = HistoricalRecords()

    def __str__(self):
        return f"[DEPRECATED] {self.player_id} - {self.name}"
