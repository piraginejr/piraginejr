from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("people", "0002_people_snapshots"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonProfileSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao legado")),
                ("profile", models.CharField(db_index=True, max_length=120, verbose_name="perfil")),
                ("start_date_raw", models.CharField(blank=True, max_length=32, verbose_name="data inicio bruta")),
                ("end_date_raw", models.CharField(blank=True, max_length=32, verbose_name="data fim bruta")),
                ("notes", models.TextField(blank=True, verbose_name="observacoes")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="ativo")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
                ("person", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="profiles", to="people.personsnapshot")),
            ],
            options={
                "verbose_name": "espelho de perfil da pessoa",
                "verbose_name_plural": "espelhos de perfis da pessoa",
                "ordering": ["person_id", "profile", "legacy_id"],
                "db_table": "people_person_profile_snapshot",
            },
        ),
        migrations.CreateModel(
            name="PersonHistorySnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao legado")),
                ("event_type", models.CharField(blank=True, db_index=True, max_length=120, verbose_name="tipo de evento")),
                ("event_date_raw", models.CharField(blank=True, max_length=32, verbose_name="data do evento bruta")),
                ("title", models.CharField(blank=True, max_length=240, verbose_name="titulo")),
                ("description", models.TextField(blank=True, verbose_name="descricao")),
                ("origin", models.CharField(blank=True, max_length=240, verbose_name="origem")),
                ("destination", models.CharField(blank=True, max_length=240, verbose_name="destino")),
                ("created_at_legacy", models.DateTimeField(blank=True, null=True, verbose_name="criado em legado")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
                ("person", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="history_entries", to="people.personsnapshot")),
            ],
            options={
                "verbose_name": "espelho de historico da pessoa",
                "verbose_name_plural": "espelhos de historico da pessoa",
                "ordering": ["person_id", "-created_at_legacy", "-legacy_id"],
                "db_table": "people_person_history_snapshot",
            },
        ),
        migrations.CreateModel(
            name="PersonContributorSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao legado")),
                ("name", models.CharField(max_length=240, verbose_name="nome")),
                ("contributor_type", models.CharField(blank=True, db_index=True, max_length=64, verbose_name="tipo")),
                ("primary_document", models.CharField(blank=True, max_length=64, verbose_name="documento principal")),
                ("document_type", models.CharField(blank=True, max_length=32, verbose_name="tipo do documento")),
                ("origin", models.CharField(blank=True, max_length=120, verbose_name="origem")),
                ("quality", models.CharField(blank=True, max_length=120, verbose_name="qualidade")),
                ("status", models.CharField(blank=True, max_length=64, verbose_name="status")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="ativo")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
                ("person", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="contributors", to="people.personsnapshot")),
            ],
            options={
                "verbose_name": "espelho de contribuinte vinculado",
                "verbose_name_plural": "espelhos de contribuintes vinculados",
                "ordering": ["person_id", "name", "legacy_id"],
                "db_table": "people_person_contributor_snapshot",
            },
        ),
        migrations.CreateModel(
            name="PersonIdentifierSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao legado")),
                ("contributor_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="contribuinte legado")),
                ("identifier_type", models.CharField(db_index=True, max_length=64, verbose_name="tipo")),
                ("value", models.CharField(max_length=240, verbose_name="valor")),
                ("is_primary", models.BooleanField(db_index=True, default=False, verbose_name="principal")),
                ("notes", models.TextField(blank=True, verbose_name="observacoes")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="ativo")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
                ("person", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="identifiers", to="people.personsnapshot")),
            ],
            options={
                "verbose_name": "espelho de identificador financeiro",
                "verbose_name_plural": "espelhos de identificadores financeiros",
                "ordering": ["person_id", "-is_primary", "identifier_type", "legacy_id"],
                "db_table": "people_person_identifier_snapshot",
            },
        ),
    ]
