# Generated by Django 4.2.1 on 2023-05-30 05:00

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("demo", "0005_remove_issue_milestone_not_null"),
    ]

    operations = [
        migrations.CreateModel(
            name="Favorite",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=32)),
                (
                    "issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="favorite_set",
                        to="demo.issue",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="favorite_set",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]