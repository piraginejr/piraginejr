from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0006_personcontributionsnapshot"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonSecureTrashSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao legado")),
                ("person_legacy_id", models.IntegerField(db_index=True, verbose_name="pessoa legada")),
                ("person_name", models.CharField(blank=True, max_length=240, verbose_name="nome da pessoa")),
                ("person_cpf", models.CharField(blank=True, max_length=32, verbose_name="cpf da pessoa")),
                ("original_status", models.CharField(blank=True, max_length=64, verbose_name="status original")),
                ("original_code", models.CharField(blank=True, max_length=80, verbose_name="codigo original")),
                ("reason", models.TextField(blank=True, verbose_name="motivo")),
                ("operator", models.CharField(blank=True, max_length=160, verbose_name="operador")),
                ("snapshot_data", models.JSONField(blank=True, default=dict, verbose_name="snapshot")),
                ("restored", models.BooleanField(db_index=True, default=False, verbose_name="restaurado")),
                ("restored_at", models.DateTimeField(blank=True, null=True, verbose_name="restaurado em")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="criado em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="atualizado em")),
            ],
            options={
                "verbose_name": "lixeira segura de pessoa",
                "verbose_name_plural": "lixeira segura de pessoas",
                "ordering": ["-created_at", "-legacy_id"],
                "db_table": "people_personsecuretrashsnapshot",
            },
        ),
        migrations.CreateModel(
            name="PersonSecurePurgeSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao legado")),
                ("person_legacy_id", models.IntegerField(db_index=True, verbose_name="pessoa legada")),
                ("trash_legacy_id", models.IntegerField(db_index=True, verbose_name="lixeira legada")),
                ("name_hash", models.CharField(blank=True, max_length=128, verbose_name="hash do nome")),
                ("cpf_hash", models.CharField(blank=True, max_length=128, verbose_name="hash do cpf")),
                ("reason", models.TextField(blank=True, verbose_name="motivo")),
                ("operator", models.CharField(blank=True, max_length=160, verbose_name="operador")),
                ("tombstone_data", models.JSONField(blank=True, default=dict, verbose_name="tombstone")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="criado em")),
            ],
            options={
                "verbose_name": "purga segura de pessoa",
                "verbose_name_plural": "purgas seguras de pessoas",
                "ordering": ["-created_at", "-legacy_id"],
                "db_table": "people_personsecurepurgesnapshot",
            },
        ),
    ]
