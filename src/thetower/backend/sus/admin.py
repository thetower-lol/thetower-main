import os

import nested_admin
from django import forms

# Admin for ApiKey
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import path
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from simple_history.admin import SimpleHistoryAdmin

from ..sus.models import CreatorCategory, CreatorLink, GameInstance, KnownPlayer, LinkedAccount, ModerationRecord, PlayerId, SusPerson

# Import custom User admin
from . import user_admin  # noqa: F401 - This registers the custom User admin
from .models import ApiKey


class ModerationRecordForm(forms.ModelForm):
    class Meta:
        model = ModerationRecord
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        tower_id = cleaned_data.get("tower_id")
        moderation_type = cleaned_data.get("moderation_type")
        source = cleaned_data.get("source")

        # Only validate for new records that are not API-created
        if not self.instance.pk and tower_id and moderation_type and source != "api":
            # Get existing active records for this player
            existing_active = ModerationRecord.objects.filter(tower_id=tower_id, resolved_at__isnull=True)  # Only active records

            existing_sus = existing_active.filter(moderation_type="sus").first()
            existing_ban = existing_active.filter(moderation_type="ban").first()
            existing_same_type = existing_active.filter(moderation_type=moderation_type).first()

            if moderation_type == "sus":
                # Manual SUS creation rules
                if existing_sus:
                    raise ValidationError(
                        f"A sus record already exists for player {tower_id}. "
                        f"Please go to the ModerationRecord list and find record ID {existing_sus.pk} to edit it."
                    )
                elif existing_ban:
                    raise ValidationError(
                        f"Player {tower_id} is already banned. Cannot create sus record for banned player. "
                        f"Please go to the ModerationRecord list and find record ID {existing_ban.pk} to view the ban."
                    )

            elif moderation_type == "ban":
                # Manual BAN creation rules
                if existing_ban:
                    raise ValidationError(
                        f"A ban record already exists for player {tower_id}. "
                        f"Please go to the ModerationRecord list and find record ID {existing_ban.pk} to edit it."
                    )
                # Note: We'll handle sus resolution in save_model since it requires database writes

            else:
                # Other moderation types (shun, soft_ban, etc.)
                if existing_same_type:
                    raise ValidationError(
                        f"An active {moderation_type} record already exists for player {tower_id}. "
                        f"Please go to the ModerationRecord list and find record ID {existing_same_type.pk} to edit it."
                    )

        return cleaned_data


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ("user", "key_suffix", "created_at", "last_used_at", "active", "invalidated_at")
    list_filter = ("active", "created_at", "invalidated_at")
    search_fields = ("user__username", "key")
    readonly_fields = ("key_suffix", "created_at", "last_used_at", "invalidated_at")

    def key_suffix(self, obj):
        return f"…{obj.key_suffix()}" if obj.key else ""

    key_suffix.short_description = "Key Suffix"

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields

    def get_fields(self, request, obj=None):
        return ["user", "active", "key_suffix", "created_at", "last_used_at", "invalidated_at"]

    def save_model(self, request, obj, form, change):
        # Prevent reactivation if invalidated_at is set
        if obj.invalidated_at and obj.active:
            messages.add_message(request, messages.ERROR, _("Cannot reactivate an invalidated API key."))
            obj.active = False
        super().save_model(request, obj, form, change)
        if not change:
            # Show the key only at creation
            messages.add_message(request, messages.INFO, _(f"API Key created: {obj.key} (save this now; it will not be shown again)."))


BASE_HIDDEN_URL = os.getenv("BASE_HIDDEN_URL")


@admin.register(SusPerson)
class SusPersonAdmin(SimpleHistoryAdmin):
    """
    READ-ONLY admin for legacy SusPerson model.
    This model is deprecated - use ModerationRecord instead.
    """

    change_form_template = "admin/sus/susperson/change_form.html"

    def get_urls(self):
        # READ-ONLY: Remove requeue functionality since this model is deprecated
        return super().get_urls()

    def _link(self, obj):
        return format_html(
            f"<a href='https://{BASE_HIDDEN_URL}/player?player={obj.player_id}' target='_new'>https://{BASE_HIDDEN_URL}/player?player={obj.player_id}</a>"
        )

    _link.short_description = "link"

    list_display = (
        "_created",
        "player_id",
        "name",
        "notes",
        "shun",
        "sus",
        "soft_banned",
        "banned",
        "api_ban",
        "api_sus",
        "_link",
        "_modified",
    )

    list_editable = ()  # READ-ONLY: No inline editing allowed

    search_fields = (
        "player_id",
        "name",
        "notes",
    )

    list_filter = (
        "shun",
        "sus",
        "soft_banned",
        "banned",
        "api_ban",
        "api_sus",
        "notes",
    )

    def _created(self, obj):
        return mark_safe(obj.created.strftime("%Y-%m-%d<br>%H:%M:%S"))

    def _modified(self, obj):
        return mark_safe(obj.modified.strftime("%Y-%m-%d<br>%H:%M:%S"))

    def get_readonly_fields(self, request, obj=None):
        # READ-ONLY: Make all fields readonly - this model is deprecated
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        # READ-ONLY: Prevent adding new SusPerson records
        return False

    def has_delete_permission(self, request, obj=None):
        # READ-ONLY: Prevent deleting SusPerson records (preserve for reference)
        return False

    def has_change_permission(self, request, obj=None):
        # Allow viewing but not changing
        return True

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["deprecation_message"] = (
            "⚠️ SusPerson model is deprecated and read-only. " "For new moderation actions, please use the ModerationRecord admin."
        )
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj, form, change):
        # READ-ONLY: Prevent saving - this model is deprecated
        messages.error(request, "SusPerson model is read-only. Please use ModerationRecord instead.")
        return


def queue_recalculation_for_player(player_id):
    """Mark all tournaments involving this player for recalculation.

    With GameInstance-level moderation, we need to mark tournaments for ALL
    tower_ids in the same GameInstance, not just the one in the moderation record.

    Args:
        player_id: Tower ID (player_id) that triggered the recalc

    Returns:
        int: Number of tournaments marked for recalc, or None on error
    """
    from ..tourney_results.models import TourneyResult
    from .models import PlayerId

    # Testing switch: set FORCE_QUEUE_FAIL=1 in the environment to simulate a failure
    # (useful to exercise admin warning UI without breaking production logic)
    if os.environ.get("FORCE_QUEUE_FAIL") == "1":
        return None

    try:
        # Get all tower_ids in the same GameInstance (if linked)
        try:
            player_id_obj = PlayerId.objects.select_related("game_instance").get(id=player_id)
            if player_id_obj.game_instance:
                # Get all tower_ids in this GameInstance
                tower_ids = list(player_id_obj.game_instance.player_ids.values_list("id", flat=True))
            else:
                # No GameInstance - just use the single tower_id
                tower_ids = [player_id]
        except PlayerId.DoesNotExist:
            # Tower ID doesn't exist yet - just use it directly
            tower_ids = [player_id]

        # Mark affected tournaments as needing recalculation - FAST operation
        # Use Q objects to OR together all tower_ids
        from django.db.models import Q

        q_filter = Q()
        for tid in tower_ids:
            q_filter |= Q(rows__player_id=tid)

        affected_count = TourneyResult.objects.filter(q_filter).distinct().update(needs_recalc=True, recalc_retry_count=0)  # Reset retry count
        return affected_count
    except Exception:
        # Swallow exceptions - recalculation queuing should not block admin save
        return None


def queue_recalculation_for_selected(modeladmin, request, queryset):
    """Admin action: queue recalculation for selected SusPerson entries"""
    count = 0
    failed = []
    for obj in queryset:
        res = queue_recalculation_for_player(obj.player_id)
        if res is None:
            failed.append(obj.player_id)
        else:
            count += 1

    if count:
        messages.info(request, _("Queued recalculation for %d players.") % count)
    if failed:
        messages.warning(request, _("Failed to queue for: %s") % ", ".join(failed))


# Register the action so it appears in the Django admin actions dropdown
# REMOVED: SusPersonAdmin.actions - model is now read-only
queue_recalculation_for_selected.short_description = "Queue recalculation for selected players"


class LinkedAccountInline(nested_admin.NestedTabularInline):
    model = LinkedAccount
    verbose_name = "Linked Social Account"
    verbose_name_plural = "Linked Social Accounts"
    extra = 1
    fields = (
        "platform",
        "account_id",
        "display_name",
        "verified",
        "verified_at",
        "primary",
        "role_source_instance",
        "reddit_flair",
        "reddit_flair_role",
        "public",
    )
    readonly_fields = ("verified_at",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Optimize role_source_instance dropdown to only show GameInstances for this player."""
        if db_field.name == "role_source_instance":
            # Get the KnownPlayer being edited
            if request.resolver_match and hasattr(request.resolver_match, "kwargs"):
                player_id = request.resolver_match.kwargs.get("object_id")
                if player_id:
                    # Limit choices to GameInstances belonging to this KnownPlayer
                    kwargs["queryset"] = GameInstance.objects.filter(player_id=player_id).select_related("player")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class PlayerIdInline(nested_admin.NestedTabularInline):
    model = PlayerId
    verbose_name = "Tower Player ID"
    verbose_name_plural = "Tower Player IDs"
    fk_name = "game_instance"
    extra = 1
    fields = ("id", "primary", "notes")


@admin.register(GameInstance)
class GameInstanceAdmin(SimpleHistoryAdmin, nested_admin.NestedModelAdmin):
    list_display = ("__str__", "player", "primary", "created_at")
    list_filter = ("primary", "created_at")
    search_fields = ("name", "player__name", "player_ids__id")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("player",)
    inlines = (PlayerIdInline,)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:  # Add view: allow setting player
            return ("created_at",)
        return ("player", "created_at")  # Change view: player is locked

    def get_queryset(self, request):
        """Optimize queryset to reduce queries."""
        qs = super().get_queryset(request)
        return qs.select_related("player").prefetch_related("player_ids")


class GameInstanceInline(nested_admin.NestedStackedInline):
    model = GameInstance
    verbose_name = "Game Instance"
    verbose_name_plural = "Game Instances"
    extra = 0
    fields = ("name", "primary", "created_at")
    readonly_fields = ("created_at",)
    inlines = [PlayerIdInline]


class CreatorLinkInline(nested_admin.NestedTabularInline):
    model = CreatorLink
    verbose_name = "Creator link"
    verbose_name_plural = "Creator links (Creator Codes page; staff-curated)"
    extra = 0
    fields = ("url", "label", "sort_order")


@admin.register(CreatorCategory)
class CreatorCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "_creators")
    list_editable = ("sort_order",)
    search_fields = ("name",)

    def _creators(self, obj):
        return obj.creators.count()

    _creators.short_description = "Creators"


@admin.register(KnownPlayer)
class KnownPlayerAdmin(SimpleHistoryAdmin, nested_admin.NestedModelAdmin):
    def _ids(self, obj):
        # Show IDs from primary game instance
        primary_instance = obj.game_instances.filter(primary=True).first()
        if primary_instance:
            id_data = primary_instance.player_ids.all().values_list("id", "primary")
            info = ""
            for id_, primary in id_data:
                primary_string = " (PRIMARY)" if primary else ""
                info += f"{id_}{primary_string}<br>"
            return mark_safe(info) if info else "No IDs"
        return "No game instance"

    _ids.short_description = "Primary Instance IDs"

    def _discord_accounts(self, obj):
        accounts = obj.linked_accounts.filter(platform="discord").values_list("account_id", flat=True)
        return ", ".join(accounts) if accounts else "None"

    _discord_accounts.short_description = "Discord IDs"

    def _game_instance_count(self, obj):
        return obj.game_instances.count()

    _game_instance_count.short_description = "# Instances"

    def get_queryset(self, request):
        """Optimize queryset to reduce N+1 queries from inlines."""
        qs = super().get_queryset(request)
        # Only apply prefetching on detail view, not list view
        if request.resolver_match.url_name.endswith("_change"):
            qs = qs.select_related("django_user").prefetch_related(
                "game_instances__player_ids",
                "linked_accounts__role_source_instance",
            )
        return qs

    list_display = ("name", "approved", "_discord_accounts", "creator_code", "django_user", "_game_instance_count", "_ids")
    list_editable = ("approved", "creator_code")
    search_fields = ("name", "creator_code", "game_instances__player_ids__id", "django_user__username", "linked_accounts__account_id")
    readonly_fields = ()
    filter_horizontal = ("creator_categories",)
    inlines = (LinkedAccountInline, GameInstanceInline, CreatorLinkInline)


@admin.register(ModerationRecord)
class ModerationRecordAdmin(SimpleHistoryAdmin):
    form = ModerationRecordForm

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("<path:object_id>/requeue/", self.admin_site.admin_view(self.requeue_view), name="sus_moderationrecord_requeue"),
        ]
        return custom_urls + urls

    def requeue_view(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            self.message_user(request, _("Object not found."), level=messages.ERROR)
            return redirect("..")

        res = queue_recalculation_for_player(obj.tower_id)
        if res is None:
            messages.warning(request, _(f"Failed to queue recalculation for player {obj.tower_id}; please try the action on the list view."))
        else:
            messages.success(request, _(f"Queued recalculation for player {obj.tower_id} ({res} tournaments)."))

        return redirect("..")

    def _known_player_display(self, obj):
        if obj.game_instance:
            return f"{obj.game_instance.player.name} - {obj.game_instance.name} (ID: {obj.game_instance.player.id})"
        return "Unverified Player"

    _known_player_display.short_description = "Known Player / Instance"

    def _created_by_display(self, obj):
        return obj.created_by_display

    _created_by_display.short_description = "Created By"

    def _resolved_by_display(self, obj):
        return obj.resolved_by_display

    _resolved_by_display.short_description = "Resolved By"

    def _status_display(self, obj):
        return "Active" if obj.is_active else "Resolved"

    _status_display.short_description = "Status"

    def _notes_display(self, obj):
        return obj.reason or "-"

    _notes_display.short_description = "Notes"

    def _zendesk_status_display(self, obj):
        """Display Zendesk processing status with clickable link if ticket exists."""
        if obj.zendesk_ticket_id:
            try:
                from ..zendesk_utils import get_zendesk_ticket_web_url

                ticket_url = get_zendesk_ticket_web_url(obj.zendesk_ticket_id)
                return format_html(
                    '<a href="{}" target="_blank" style="color: green; text-decoration: none;">✅ #{}</a>', ticket_url, obj.zendesk_ticket_id
                )
            except Exception:
                return format_html('<span style="color: green;">✅ #{}</span>', obj.zendesk_ticket_id)
        elif obj.needs_zendesk_ticket:
            if obj.zendesk_retry_count >= 3:
                return format_html('<span style="color: red;">❌ Failed ({} retries)</span>', obj.zendesk_retry_count)
            elif obj.zendesk_retry_count > 0:
                return format_html('<span style="color: orange;">⏳ Retrying (attempt {})</span>', obj.zendesk_retry_count + 1)
            else:
                return format_html('<span style="color: blue;">⏳ Pending</span>')
        else:
            return format_html('<span style="color: gray;">➖ Disabled</span>')

    _zendesk_status_display.short_description = "Zendesk Status"

    list_display = (
        "tower_id",
        "_known_player_display",
        "moderation_type",
        "_status_display",
        "source",
        "_zendesk_status_display",
        "created_at",
        "_created_by_display",
        "resolved_at",
        "_resolved_by_display",
    )

    list_filter = (
        "moderation_type",
        "source",
        "created_at",
        "resolved_at",
    )

    search_fields = (
        "tower_id",
        "game_instance__player__name",
        "reason",
    )

    readonly_fields = (
        "created_at",
        "game_instance",  # Auto-linked, not directly editable
        "_created_by_display",  # Read-only combo field
        "_resolved_by_display",  # Read-only combo field
        "created_by_discord_id",  # Only set by bot
        "created_by_api_key",  # Only set by API
        "resolved_by_discord_id",  # Only set by bot
        "resolved_by_api_key",  # Only set by API
        "_zendesk_status_display",  # Display formatted status with clickable link
    )

    fieldsets = (
        (
            "Moderation Details",
            {
                "fields": (
                    "tower_id",
                    "game_instance",
                    "moderation_type",
                    "source",
                    "created_at",
                    "_created_by_display",
                    "resolved_at",
                    "_resolved_by_display",
                    "reason",
                ),
                "description": "Enter the Tower ID. If this player is verified (has a game instance), they will be auto-linked.",
            },
        ),
        (
            "Zendesk Integration",
            {
                "fields": ("_zendesk_status_display", "needs_zendesk_ticket"),
                "description": "Zendesk ticket creation status and controls. Click status for direct ticket access. Once a ticket is created, the toggle becomes read-only as the queue ignores records with existing tickets.",
                "classes": ("collapse",),
            },
        ),
        (
            "Audit Trail",
            {
                "fields": (
                    "created_by",
                    "created_by_discord_id",
                    "created_by_api_key",
                    "resolved_by",
                    "resolved_by_discord_id",
                    "resolved_by_api_key",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("game_instance__player", "created_by", "resolved_by")

    def get_readonly_fields(self, request, obj=None):
        # Base readonly fields that are always readonly
        readonly = list(self.readonly_fields)

        # Make needs_zendesk_ticket readonly if ticket already exists
        if obj and obj.zendesk_ticket_id:
            readonly.append("needs_zendesk_ticket")

        # For non-superusers, make audit trail fields and source readonly
        if not request.user.is_superuser:
            protected_fields = [
                "source",  # Only superusers can change the source
                "created_by",
                "created_by_discord_id",
                "created_by_api_key",
                "resolved_by",
                "resolved_by_discord_id",
                "resolved_by_api_key",
            ]
            readonly.extend(protected_fields)

        return readonly

    def has_delete_permission(self, request, obj=None):
        # Restrict delete permissions - moderation records should be resolved, not deleted
        return request.user.is_superuser  # Only superusers can delete

    def has_change_permission(self, request, obj=None):
        # Allow viewing for all users, but restrict editing of API-created records
        if obj and obj.source == "api" and not request.user.is_superuser:
            return False  # Non-superusers cannot edit API-created records
        return True  # Allow editing for manual/bot records or superusers

    def delete_model(self, request, obj):
        # Trigger recalc before deletion if this was an active record affecting tournaments
        if obj.moderation_type in ["sus", "shun", "ban"] and obj.resolved_at is None:
            affected = queue_recalculation_for_player(obj.tower_id)
            if affected:
                messages.info(request, f"Triggered tournament recalculation before deleting record for {obj.tower_id}")

        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        """Handle bulk delete operations from admin interface"""
        # Trigger recalc for all affected players before bulk deletion
        affected_players = set()
        for obj in queryset:
            if obj.moderation_type in ["sus", "shun", "ban"] and obj.resolved_at is None:
                affected_players.add(obj.tower_id)

        # Trigger recalculation for each affected player
        total_affected = 0
        for tower_id in affected_players:
            affected = queue_recalculation_for_player(tower_id)
            if affected:
                total_affected += affected

        if total_affected:
            messages.info(
                request,
                f"Triggered tournament recalculation for {len(affected_players)} player(s) before bulk deletion ({total_affected} tournaments affected)",
            )

        # Perform the actual bulk deletion
        super().delete_queryset(request, queryset)

    def save_model(self, request, obj, form, change):
        # Store original state for comparison
        original_affects_tournaments = False
        if change and obj.pk:
            try:
                original = ModerationRecord.objects.get(pk=obj.pk)
                # Check if the original record affected tournaments (sus/shun/ban)
                original_affects_tournaments = original.moderation_type in ["sus", "shun", "ban"] and original.resolved_at is None
            except ModerationRecord.DoesNotExist:
                pass

        if not change:  # New object
            obj.created_by = request.user

            # Handle ban escalation logic (resolve sus when creating ban)
            if obj.tower_id and obj.moderation_type == "ban":
                from django.utils import timezone

                # Get existing active sus records
                existing_sus_records = ModerationRecord.objects.filter(tower_id=obj.tower_id, moderation_type="sus", resolved_at__isnull=True)

                sus_notes_to_copy = []
                for existing_sus in existing_sus_records:
                    # Collect sus notes before resolving
                    if existing_sus.reason and existing_sus.reason.strip():
                        sus_notes_to_copy.append(existing_sus.reason.strip())

                    # Append resolution info to existing reason
                    resolution_info = f"Automatically resolved due to ban escalation by {request.user.username}"
                    if existing_sus.reason:
                        existing_sus.reason = f"{existing_sus.reason}\n\n{resolution_info}"
                    else:
                        existing_sus.reason = resolution_info

                    # Resolve existing sus record before creating ban
                    existing_sus.resolved_at = timezone.now()
                    existing_sus.resolved_by = request.user
                    existing_sus.save()

                    messages.success(request, f"Resolved existing sus record for player {obj.tower_id} due to ban escalation.")

                # Copy sus notes into ban reason
                if sus_notes_to_copy:
                    existing_reason = obj.reason or ""
                    sus_notes_section = "Previous sus notes:\n\n" + "\n\n".join(sus_notes_to_copy)

                    if existing_reason.strip():
                        obj.reason = f"{existing_reason}\n\n{sus_notes_section}"
                    else:
                        obj.reason = sus_notes_section

            # Auto-link to GameInstance if one exists for this tower_id
            if obj.tower_id:
                try:
                    # Look for a GameInstance that has this tower_id
                    player_id_obj = PlayerId.objects.select_related("game_instance").get(id=obj.tower_id)
                    obj.game_instance = player_id_obj.game_instance
                except PlayerId.DoesNotExist:
                    # No GameInstance has this tower_id - leave as unverified
                    obj.game_instance = None

        super().save_model(request, obj, form, change)

        # Check if we need to trigger tournament recalculation
        # Sus, shun, and ban moderation types affect tournament positions
        current_affects_tournaments = obj.moderation_type in ["sus", "shun", "ban"] and obj.resolved_at is None  # Active moderation

        # Trigger recalc if:
        # 1. New active sus/shun/ban record, OR
        # 2. Existing record changed from affecting tournaments to not affecting (resolved), OR
        # 3. Existing record changed from not affecting to affecting (reactivated)
        should_recalc = (not change and current_affects_tournaments) or (  # New active sus/shun
            change and original_affects_tournaments != current_affects_tournaments
        )  # Status change

        if should_recalc:
            affected = queue_recalculation_for_player(obj.tower_id)
            if affected is None:
                messages.warning(request, _(f"Failed to queue recalculation for player {obj.tower_id}; please requeue manually."))
            else:
                messages.info(request, _(f"Queued tournament recalculation for {affected} tournaments involving player {obj.tower_id}."))


def queue_recalculation_for_moderation_records(modeladmin, request, queryset):
    """Admin action: queue recalculation for selected ModerationRecord entries"""
    count = 0
    failed = []
    processed_players = set()  # Avoid duplicate processing for same player

    for obj in queryset:
        # Only process each player once, even if multiple records selected
        if obj.tower_id in processed_players:
            continue
        processed_players.add(obj.tower_id)

        res = queue_recalculation_for_player(obj.tower_id)
        if res is None:
            failed.append(obj.tower_id)
        else:
            count += 1

    if count:
        messages.info(request, _("Queued recalculation for %d players.") % count)
    if failed:
        messages.warning(request, _("Failed to queue for: %s") % ", ".join(failed))


def resolve_moderation_records(modeladmin, request, queryset):
    """Admin action: resolve selected active ModerationRecord entries"""
    from django.utils import timezone

    resolved_count = 0
    already_resolved = 0
    players_to_recalc = set()

    for obj in queryset:
        if obj.resolved_at is None:  # Only resolve active records
            # Check if this record affects tournaments before resolving
            if obj.moderation_type in ["sus", "shun", "ban"]:
                players_to_recalc.add(obj.tower_id)

            obj.resolved_at = timezone.now()
            obj.resolved_by = request.user
            obj.save()
            resolved_count += 1
        else:
            already_resolved += 1

    # Trigger recalculation for affected players
    recalc_success = 0
    recalc_failed = []
    for player_id in players_to_recalc:
        res = queue_recalculation_for_player(player_id)
        if res is None:
            recalc_failed.append(player_id)
        else:
            recalc_success += 1

    # User feedback
    if resolved_count:
        messages.success(request, _("Resolved %d moderation records.") % resolved_count)
    if already_resolved:
        messages.info(request, _("%d records were already resolved.") % already_resolved)
    if recalc_success:
        messages.info(request, _("Triggered tournament recalculation for %d players.") % recalc_success)
    if recalc_failed:
        messages.warning(request, _("Failed to queue recalculation for: %s") % ", ".join(recalc_failed))


# Register the actions for ModerationRecord admin
ModerationRecordAdmin.actions = [queue_recalculation_for_moderation_records, resolve_moderation_records]
queue_recalculation_for_moderation_records.short_description = "Queue tournament recalculation for selected players"
resolve_moderation_records.short_description = "Resolve selected moderation records"
