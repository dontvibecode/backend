from django.core.management import call_command
from django.db import migrations

CACHE_TABLE = "django_cache_table"


def create_cache_table(apps, schema_editor):
    """
    Creates the table backing CACHES["default"].

    Django's cache table is created by a management command rather than by the
    ORM, so it is wrapped in a migration here to keep `manage.py migrate` the
    single step a deploy has to run.
    """
    call_command(
        "createcachetable",
        CACHE_TABLE,
        database=schema_editor.connection.alias,
        verbosity=0,
    )


def drop_cache_table(apps, schema_editor):
    schema_editor.execute(f"DROP TABLE IF EXISTS {CACHE_TABLE}")


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0022_processedstripeevent_alter_user_token_limit"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]
