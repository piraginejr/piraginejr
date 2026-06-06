from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("people", "0004_people_detail_snapshot_tables"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterModelTable(
                    name="personprofilesnapshot",
                    table="people_personprofilesnapshot",
                ),
                migrations.AlterModelTable(
                    name="personhistorysnapshot",
                    table="people_personhistorysnapshot",
                ),
                migrations.AlterModelTable(
                    name="personcontributorsnapshot",
                    table="people_personcontributorsnapshot",
                ),
                migrations.AlterModelTable(
                    name="personidentifiersnapshot",
                    table="people_personidentifiersnapshot",
                ),
            ],
        ),
    ]
