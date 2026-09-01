"""Record the nickname search index in migration state without touching the database.

Migration 0041 replaced the plain ``nickname`` index on ``tourneyrow`` with a functional
``upper(nickname)`` index using raw SQL, so Django's model state never knew about it. On
SQLite every ``AlterField`` rebuilds the table and recreates only the indexes in state,
which would have silently dropped the functional index (and brought the plain one back)
in the next rebuild. This migration aligns state with what is on disk: no plain index on
``nickname`` and a functional index on ``tourneyrow``, declared on the model so every
future rebuild recreates it.

The index already exists (under 0041's longer name); the rebuild in 0044 recreates it
under the model's name, so no database operation is needed here.
"""

import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tourney_results", "0042_alter_historicaltourneyresult_league_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="historicaltourneyrow",
                    name="nickname",
                    field=models.CharField(help_text="Tourney name", max_length=32),
                ),
                migrations.AlterField(
                    model_name="tourneyrow",
                    name="nickname",
                    field=models.CharField(help_text="Tourney name", max_length=32),
                ),
                migrations.AddIndex(
                    model_name="tourneyrow",
                    index=models.Index(django.db.models.functions.text.Upper("nickname"), name="idx_tourneyrow_nickname_upper"),
                ),
            ],
            database_operations=[],
        ),
    ]
