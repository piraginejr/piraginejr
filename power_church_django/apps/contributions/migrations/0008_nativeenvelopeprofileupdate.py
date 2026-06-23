from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("contributions", "0007_rename_contributio_org_idx_contributio_organiz_1094a0_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="NativeEnvelopeProfileUpdate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao")),
                ("person_legacy_id", models.IntegerField(db_index=True, verbose_name="pessoa legada")),
                ("field_name", models.CharField(db_index=True, max_length=64, verbose_name="campo")),
                ("current_value", models.TextField(blank=True, verbose_name="valor da ficha")),
                ("envelope_value", models.TextField(blank=True, verbose_name="valor do envelope")),
                ("status", models.CharField(choices=[("pendente", "Pendente"), ("aplicado", "Aplicado"), ("ignorado", "Ignorado")], db_index=True, default="pendente", max_length=24, verbose_name="status")),
                ("notes", models.TextField(blank=True, verbose_name="observacoes")),
                ("created_by", models.CharField(blank=True, max_length=160, verbose_name="criado por")),
                ("updated_by", models.CharField(blank=True, max_length=160, verbose_name="atualizado por")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="criado em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="atualizado em")),
                ("envelope", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="profile_updates", to="contributions.nativeenvelope")),
            ],
            options={
                "verbose_name": "pendencia cadastral de envelope",
                "verbose_name_plural": "pendencias cadastrais de envelope",
                "ordering": ["envelope_id", "status", "field_name", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="nativeenvelopeprofileupdate",
            index=models.Index(fields=["envelope", "status"], name="contributio_envelop_8e09ba_idx"),
        ),
        migrations.AddIndex(
            model_name="nativeenvelopeprofileupdate",
            index=models.Index(fields=["person_legacy_id", "status", "field_name"], name="contributio_person_l_4b5ff7_idx"),
        ),
        migrations.AddIndex(
            model_name="nativeenvelopeprofileupdate",
            index=models.Index(fields=["organization_id", "status"], name="contributio_organiz_0c7348_idx"),
        ),
    ]
