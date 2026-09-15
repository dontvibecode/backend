from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0023_create_cache_table"),
    ]

    operations = [
        migrations.AlterField(
            model_name="preferences",
            name="tab_size",
            field=models.IntegerField(default=4),
        ),
    ]
