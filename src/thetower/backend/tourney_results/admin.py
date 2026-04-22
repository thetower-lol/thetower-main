import logging
import os
import subprocess
import threading

from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from simple_history.admin import SimpleHistoryAdmin

from ..sus.models import PlayerId
from .models import BattleCondition, Injection, NameDayWinner, PatchNew, PositionRole, PromptTemplate, RainPeriod, Role, TourneyResult, TourneyRow

# Graceful thetower_bcs import handling
try:
    from thetower_bcs import TournamentPredictor, predict_future_tournament

    TOWERBCS_AVAILABLE = True
except ImportError:
    TOWERBCS_AVAILABLE = False

    def predict_future_tournament(tourney_id, league):
        return []

    class TournamentPredictor:
        @staticmethod
        def get_tournament_info(date):
            return None, date, 0


class PatchFilter(admin.SimpleListFilter):
    title = "patch"
    parameter_name = "patch"

    def lookups(self, request, model_admin):
        """Return a list of tuples for the filter sidebar."""
        patches = PatchNew.objects.all().order_by("-version_minor", "-version_patch")
        return [(patch.pk, str(patch)) for patch in patches]

    def queryset(self, request, queryset):
        """Filter the queryset based on the selected patch."""
        if self.value():
            try:
                patch = PatchNew.objects.get(pk=self.value())
                return queryset.filter(date__gte=patch.start_date, date__lte=patch.end_date)
            except PatchNew.DoesNotExist:
                return queryset
        return queryset


BASE_ADMIN_URL = os.getenv("BASE_ADMIN_URL")

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


@admin.action(description="Restart public site (thetower.lol)")
def restart_public_site(modeladmin, request, queryset):
    subprocess.call("systemctl restart tower-public_site", shell=True)


@admin.action(description="Restart hidden site (hidden.thetower.lol)")
def restart_hidden_site(modeladmin, request, queryset):
    subprocess.call("systemctl restart tower-hidden_site", shell=True)


@admin.action(description="Restart admin site (admin.thetower.lol)")
def restart_admin_site(modeladmin, request, queryset):
    subprocess.call("systemctl restart tower-admin_site", shell=True)


@admin.action(description="Restart thetower bot")
def restart_thetower_bot(modeladmin, request, queryset):
    subprocess.call("systemctl restart thetower-bot", shell=True)


@admin.action(description="Restart import results (run me if you don't see TourneyResult objects from previous tourney when it should be there)")
def restart_import_results(modeladmin, request, queryset):
    subprocess.call("systemctl restart import_results", shell=True)


@admin.action(description="Restart get results (run me if import results is failing?)")
def restart_get_results(modeladmin, request, queryset):
    subprocess.call("systemctl restart get_results", shell=True)


@admin.action(description="Restart live bracket cache generator")
def restart_generate_live_bracket_cache(modeladmin, request, queryset):
    subprocess.call("systemctl restart generate_live_bracket_cache", shell=True)


@admin.action(description="Restart recalc worker")
def restart_recalc_worker(modeladmin, request, queryset):
    subprocess.call("systemctl restart tower-recalc_worker", shell=True)


@admin.action(description="Publicize")
def publicize(modeladmin, request, queryset):
    for item in queryset:
        item.public = True
        item.save()
        # queryset.update(public=True)


def update_summary(queryset):
    from .tourney_utils import get_summary

    last_date = sorted(queryset.values_list("date", flat=True))[-1]
    summary = get_summary(last_date)
    queryset.update(overview=summary)


@admin.action(description="Generate summary with the help of AI")
def generate_summary(modeladmin, request, queryset):
    thread = threading.Thread(target=update_summary, args=(queryset,))
    thread.start()


@admin.action(description="Regenerate battle conditions")
def regenerate_battle_conditions(modeladmin, request, queryset):
    """Regenerate battle conditions for selected tournaments using thetower_bcs predictions."""
    if not TOWERBCS_AVAILABLE:
        modeladmin.message_user(request, "Error: thetower-bcs package is not available. Cannot regenerate battle conditions.", level="ERROR")
        return

    from datetime import datetime
    from zoneinfo import ZoneInfo

    updated_count = 0
    error_count = 0

    for tournament in queryset:
        try:
            # Get tournament ID from date
            tourney_date = datetime.combine(tournament.date, datetime.min.time()).replace(tzinfo=ZoneInfo("UTC"))
            tourney_id, _, _, _ = TournamentPredictor.get_tournament_info(tourney_date)

            # Predict conditions
            predicted_conditions = set(predict_future_tournament(tourney_id, tournament.league))

            # Update tournament conditions
            if not predicted_conditions or predicted_conditions == {"None"} or predicted_conditions == {"none"}:
                tournament.conditions.clear()
            else:
                condition_ids = BattleCondition.objects.filter(name__in=predicted_conditions).values_list("id", flat=True)
                tournament.conditions.set(condition_ids)

            tournament.save()
            updated_count += 1

        except Exception as e:
            logger.error(f"Error regenerating battle conditions for {tournament}: {e}")
            error_count += 1

    if updated_count > 0:
        modeladmin.message_user(request, f"Successfully regenerated battle conditions for {updated_count} tournament(s).")

    if error_count > 0:
        modeladmin.message_user(
            request, f"Failed to regenerate battle conditions for {error_count} tournament(s). Check logs for details.", level="WARNING"
        )


@admin.register(TourneyRow)
class TourneyRowAdmin(SimpleHistoryAdmin):
    list_display = (
        "player_id",
        "position",
        "nickname",
        "_known_player",
        "result",
        "wave",
        "avatar_id",
        "relic_id",
    )

    search_fields = (
        "player_id",
        "nickname",
        "wave",
    )

    def _known_player(self, obj):
        player_id_obj = PlayerId.objects.select_related("game_instance__player").get(id=obj.player_id)
        if player_id_obj.game_instance and player_id_obj.game_instance.player:
            player_pk = player_id_obj.game_instance.player.id
        elif hasattr(player_id_obj, "player") and player_id_obj.player:
            # Fallback for unmigrated data
            player_pk = player_id_obj.player.id
        else:
            return "No KnownPlayer"
        return format_html(
            f"<a href='{BASE_ADMIN_URL}sus/knownplayer/{player_pk}/change/'>{BASE_ADMIN_URL}<br>sus/<br>knownplayer/{player_pk}/change/</a>"
        )

    list_filter = ["result__league", "result__date", "result__public", "avatar_id", "relic_id"]


@admin.register(TourneyResult)
class TourneyResultAdmin(SimpleHistoryAdmin):
    list_display = (
        "id",
        "league",
        "date",
        "_patch",
        "_conditions",
        "needs_recalc",
        "last_recalc_at",
        "recalc_retry_count",
        "result_file",
        "public",
        "_overview",
    )

    search_fields = (
        "id",
        "league",
        "date",
        "result_file",
        "public",
    )

    list_filter = ["needs_recalc", "date", "league", "public", "conditions", PatchFilter]

    def _overview(self, obj):
        return obj.overview[:500] + "..." if obj.overview else ""

    def _conditions(self, obj):
        return mark_safe("<br>".join([str(condition) for condition in obj.conditions.all()]))

    def _patch(self, obj):
        """Display the patch version for this tournament."""
        patch = obj.patch
        return str(patch) if patch else "Unknown"

    _patch.short_description = "Patch"
    _patch.admin_order_field = "date"  # Allow sorting by date as proxy for patch

    def mark_for_recalc(self, request, queryset):
        """Mark selected tournaments for recalculation"""
        count = queryset.update(needs_recalc=True, recalc_retry_count=0)
        self.message_user(request, f"Marked {count} tournaments for recalculation")

    mark_for_recalc.short_description = "Mark selected tournaments for recalculation"
    _conditions.short_description = "Battle Conditions"

    filter_horizontal = ("conditions",)

    actions = [
        "mark_for_recalc",
        publicize,
        restart_public_site,
        restart_hidden_site,
        restart_admin_site,
        restart_thetower_bot,
        restart_import_results,
        restart_get_results,
        restart_generate_live_bracket_cache,
        restart_recalc_worker,
        generate_summary,
        regenerate_battle_conditions,
    ]

    def regenerate_bcs_button(self, obj):
        """Display a button to regenerate battle conditions for this tournament."""
        if not TOWERBCS_AVAILABLE:
            return "thetower-bcs not available"

        url = f"{self.get_admin_url('regenerate_bcs', obj.pk)}"
        return format_html(
            '<a class="button" href="{}" style="background: #417690; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px;">Regenerate BCs</a>',
            url,
        )

    regenerate_bcs_button.short_description = "Regenerate Battle Conditions"
    regenerate_bcs_button.allow_tags = True

    readonly_fields = ("regenerate_bcs_button",)

    fieldsets = (
        (None, {"fields": ("league", "date", "result_file", "public", "conditions", "overview")}),
        (
            "Recalculation",
            {
                "fields": ("needs_recalc", "last_recalc_at", "recalc_retry_count", "regenerate_bcs_button"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_admin_url(self, action, obj_id):
        """Generate admin URL for custom actions."""
        from django.urls import reverse

        return reverse(f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_{action}", args=[obj_id])

    def get_urls(self):
        """Add custom URLs for admin actions."""
        from django.urls import path

        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/regenerate_bcs/",
                self.admin_site.admin_view(self.regenerate_bcs_view),
                name=f"{self.model._meta.app_label}_{self.model._meta.model_name}_regenerate_bcs",
            ),
        ]
        return custom_urls + urls

    def regenerate_bcs_view(self, request, object_id):
        """View to regenerate battle conditions for a single tournament."""
        obj = self.get_object(request, object_id)

        if not TOWERBCS_AVAILABLE:
            self.message_user(request, "Error: thetower-bcs package is not available.", level="ERROR")
            return self.response_change(request, obj)

        try:
            # Get tournament ID from date
            from datetime import datetime
            from zoneinfo import ZoneInfo

            tourney_date = datetime.combine(obj.date, datetime.min.time()).replace(tzinfo=ZoneInfo("UTC"))
            tourney_id, _, _, _ = TournamentPredictor.get_tournament_info(tourney_date)

            # Predict conditions
            predicted_conditions = set(predict_future_tournament(tourney_id, obj.league))

            # Update tournament conditions
            if not predicted_conditions or predicted_conditions == {"None"} or predicted_conditions == {"none"}:
                obj.conditions.clear()
            else:
                condition_ids = BattleCondition.objects.filter(name__in=predicted_conditions).values_list("id", flat=True)
                obj.conditions.set(condition_ids)

            obj.save()
            self.message_user(request, "Battle conditions regenerated successfully.")

        except Exception as e:
            logger.error(f"Error regenerating battle conditions for {obj}: {e}")
            self.message_user(request, f"Error regenerating battle conditions: {e}", level="ERROR")

        return self.response_change(request, obj)


@admin.register(PatchNew)
class PatchNewAdmin(SimpleHistoryAdmin):
    list_display = (
        "version_minor",
        "version_patch",
        "interim",
        "start_date",
        "end_date",
    )

    search_fields = (
        "version_minor",
        "version_patch",
        "start_date",
        "end_date",
    )


@admin.register(Role)
class RoleAdmin(SimpleHistoryAdmin):
    def _color_preview(self, obj):
        return mark_safe(f"""<div style="width: 120px; height: 40px; background: {obj.color};">&nbsp;</div>""")

    _color_preview.short_description = "Color"

    list_display = (
        "wave_bottom",
        "wave_top",
        "patch",
        "league",
        "_color_preview",
        "color",
    )

    search_fields = (
        "wave_bottom",
        "wave_top",
        "patch",
        "league",
        "color",
    )

    list_filter = ["patch", "wave_bottom", "wave_top", "color", "league"]


@admin.register(PositionRole)
class PositionRoleAdmin(SimpleHistoryAdmin):
    def _color_preview(self, obj):
        return mark_safe(f"""<div style="width: 120px; height: 40px; background: {obj.color};">&nbsp;</div>""")

    _color_preview.short_description = "Color"

    list_display = (
        "position",
        "patch",
        "league",
        "_color_preview",
        "color",
    )

    search_fields = (
        "position",
        "patch",
        "league",
        "color",
    )

    list_filter = ["patch", "position", "color", "league"]


@admin.register(BattleCondition)
class BattleConditionAdmin(SimpleHistoryAdmin):
    list_display = (
        "name",
        "shortcut",
    )

    search_fields = (
        "name",
        "shortcut",
    )


@admin.register(NameDayWinner)
class NameDayWinnerAdmin(SimpleHistoryAdmin):
    list_display = (
        "winner",
        "winner_discord_accounts",
        "tourney",
        "winning_nickname",
        "nameday_theme",
    )

    search_fields = (
        "winning_nickname",
        "winner__name",
        "winner__linked_accounts__account_id",
        "nameday_theme",
    )

    def winner_discord_accounts(self, obj):
        """Display Discord accounts for the winner."""
        from thetower.backend.sus.models import LinkedAccount

        discord_accounts = obj.winner.linked_accounts.filter(platform=LinkedAccount.Platform.DISCORD)
        if discord_accounts:
            accounts_str = ", ".join([f"{acc.account_id}{' ✓' if acc.verified else ''}" for acc in discord_accounts])
            return accounts_str
        return "No Discord accounts"

    winner_discord_accounts.short_description = "Discord Accounts"


@admin.register(Injection)
class InjectionAdmin(SimpleHistoryAdmin):
    list_display = (
        "text",
        "user",
    )

    search_fields = (
        "text",
        "user",
    )


@admin.register(PromptTemplate)
class PromptTemplateAdmin(SimpleHistoryAdmin):
    list_display = ("text",)
    search_fields = ("text",)


@admin.register(RainPeriod)
class RainPeriodAdmin(SimpleHistoryAdmin):
    list_display = (
        "emoji",
        "start_date",
        "end_date",
        "enabled",
        "description",
    )

    search_fields = (
        "emoji",
        "description",
    )

    list_filter = ["enabled", "start_date", "end_date"]
