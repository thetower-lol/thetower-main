# The starting Creator Codes categories; staff curate them in the admin from here on.

from django.db import migrations

CATEGORIES = [
    "Moderating",
    "YouTube",
    "Streaming",
    "Sheets & Tools",
    "Apps & Websites",
    "Discord Bots",
    "Guides & Infographics",
    "Mentoring",
    "Community & Events",
]


def seed(apps, schema_editor):
    CreatorCategory = apps.get_model("sus", "CreatorCategory")
    for order, name in enumerate(CATEGORIES, start=1):
        CreatorCategory.objects.get_or_create(name=name, defaults={"sort_order": order * 10})


def unseed(apps, schema_editor):
    apps.get_model("sus", "CreatorCategory").objects.filter(name__in=CATEGORIES, creators__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("sus", "0037_creator_categories_links"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
