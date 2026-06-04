from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("people", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao legado")),
                ("preferred_unit_id", models.IntegerField(blank=True, null=True, verbose_name="unidade preferencial legado")),
                ("internal_code", models.CharField(blank=True, max_length=80, verbose_name="codigo interno")),
                ("name", models.CharField(max_length=240, verbose_name="nome")),
                ("normalized_name", models.CharField(db_index=True, max_length=240, verbose_name="nome normalizado")),
                ("social_name", models.CharField(blank=True, max_length=240, verbose_name="nome social")),
                ("cpf", models.CharField(blank=True, db_index=True, max_length=32, verbose_name="cpf")),
                ("rg", models.CharField(blank=True, max_length=64, verbose_name="rg")),
                ("birth_date", models.DateField(blank=True, null=True, verbose_name="data de nascimento")),
                ("birth_date_raw", models.CharField(blank=True, max_length=32, verbose_name="data de nascimento bruta")),
                ("sex", models.CharField(blank=True, max_length=32, verbose_name="sexo")),
                ("marital_status", models.CharField(blank=True, max_length=64, verbose_name="estado civil")),
                ("primary_email", models.CharField(blank=True, db_index=True, max_length=240, verbose_name="e-mail principal")),
                ("normalized_email", models.CharField(blank=True, db_index=True, max_length=240, verbose_name="e-mail normalizado")),
                ("primary_phone", models.CharField(blank=True, max_length=64, verbose_name="telefone principal")),
                ("primary_whatsapp", models.CharField(blank=True, max_length=64, verbose_name="whatsapp principal")),
                ("status", models.CharField(blank=True, db_index=True, max_length=64, verbose_name="status")),
                ("is_archived", models.BooleanField(db_index=True, default=False, verbose_name="arquivo morto")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="ativo")),
                ("notes", models.TextField(blank=True, verbose_name="observacoes")),
                ("import_lot_id", models.IntegerField(blank=True, null=True, verbose_name="lote de importacao legado")),
                ("created_at_legacy", models.DateTimeField(blank=True, null=True, verbose_name="criado em legado")),
                ("updated_at_legacy", models.DateTimeField(blank=True, null=True, verbose_name="atualizado em legado")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
            ],
            options={
                "verbose_name": "espelho de pessoa",
                "verbose_name_plural": "espelhos de pessoas",
                "ordering": ["normalized_name", "legacy_id"],
            },
        ),
        migrations.CreateModel(
            name="PersonRelationshipSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao legado")),
                ("relationship_type", models.CharField(db_index=True, max_length=80, verbose_name="tipo de relacionamento")),
                ("notes", models.TextField(blank=True, verbose_name="observacoes")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="ativo")),
                ("created_at_legacy", models.DateTimeField(blank=True, null=True, verbose_name="criado em legado")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
                ("person", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="outgoing_relationships", to="people.personsnapshot")),
                ("related_person", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="incoming_relationships", to="people.personsnapshot")),
            ],
            options={
                "verbose_name": "espelho de relacionamento",
                "verbose_name_plural": "espelhos de relacionamentos",
                "ordering": ["person_id", "relationship_type", "related_person_id", "legacy_id"],
            },
        ),
        migrations.CreateModel(
            name="PersonContactSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao legado")),
                ("contact_type", models.CharField(db_index=True, max_length=64, verbose_name="tipo")),
                ("value", models.CharField(max_length=240, verbose_name="valor")),
                ("normalized_value", models.CharField(db_index=True, max_length=240, verbose_name="valor normalizado")),
                ("is_primary", models.BooleanField(db_index=True, default=False, verbose_name="principal")),
                ("notes", models.TextField(blank=True, verbose_name="observacoes")),
                ("created_at_legacy", models.DateTimeField(blank=True, null=True, verbose_name="criado em legado")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
                ("person", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="contacts", to="people.personsnapshot")),
            ],
            options={
                "verbose_name": "espelho de contato",
                "verbose_name_plural": "espelhos de contatos",
                "ordering": ["person_id", "-is_primary", "contact_type", "legacy_id"],
            },
        ),
        migrations.CreateModel(
            name="PersonAddressSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao legado")),
                ("address_type", models.CharField(db_index=True, max_length=64, verbose_name="tipo")),
                ("cep", models.CharField(blank=True, db_index=True, max_length=16, verbose_name="cep")),
                ("street", models.CharField(blank=True, max_length=240, verbose_name="logradouro")),
                ("number", models.CharField(blank=True, max_length=32, verbose_name="numero")),
                ("complement", models.CharField(blank=True, max_length=240, verbose_name="complemento")),
                ("neighborhood", models.CharField(blank=True, max_length=160, verbose_name="bairro")),
                ("city", models.CharField(blank=True, db_index=True, max_length=160, verbose_name="cidade")),
                ("state", models.CharField(blank=True, max_length=8, verbose_name="uf")),
                ("is_primary", models.BooleanField(db_index=True, default=False, verbose_name="principal")),
                ("normalized_address", models.CharField(blank=True, db_index=True, max_length=500, verbose_name="endereco normalizado")),
                ("created_at_legacy", models.DateTimeField(blank=True, null=True, verbose_name="criado em legado")),
                ("updated_at_legacy", models.DateTimeField(blank=True, null=True, verbose_name="atualizado em legado")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
                ("person", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="addresses", to="people.personsnapshot")),
            ],
            options={
                "verbose_name": "espelho de endereco",
                "verbose_name_plural": "espelhos de enderecos",
                "ordering": ["person_id", "-is_primary", "legacy_id"],
            },
        ),
    ]
