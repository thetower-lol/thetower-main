from django.db import migrations


def upgrade(apps, schema_editor):
    # Models as they were at this migration: importing the live models breaks a fresh DB once later migrations change it
    SusPerson = apps.get_model("sus", "SusPerson")
    HistoricalSusPerson = apps.get_model("sus", "HistoricalSusPerson")

    for sus in SusPerson.objects.all():
        history = list(HistoricalSusPerson.objects.filter(player_id=sus.player_id))
        created = [hist for hist in history if hist.history_type == "+"]

        # update(), not save(): modified is auto_now, which would overwrite the value taken from history
        SusPerson.objects.filter(pk=sus.pk).update(
            created=created[0].history_date,
            modified=max([sus_entry.history_date for sus_entry in history]),
        )


class Migration(migrations.Migration):
    dependencies = [("sus", "0012_historicalsusperson_created_and_more")]
    operations = [migrations.RunPython(upgrade)]
