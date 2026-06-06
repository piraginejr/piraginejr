from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("people", "0003_people_detail_snapshots"),
    ]

    operations = [
        migrations.AlterModelTable(
            name="personprofilesnapshot",
            table="people_person_profile_snapshot",
        ),
        migrations.AlterModelTable(
            name="personhistorysnapshot",
            table="people_person_history_snapshot",
        ),
        migrations.AlterModelTable(
            name="personcontributorsnapshot",
            table="people_person_contributor_snapshot",
        ),
        migrations.AlterModelTable(
            name="personidentifiersnapshot",
            table="people_person_identifier_snapshot",
        ),
    ]
