from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contributions", "0003_receiptsnapshot_receiptitemsnapshot"),
    ]

    operations = [
        migrations.CreateModel(
            name="ContributionTypeSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao legado")),
                ("code", models.CharField(blank=True, db_index=True, max_length=80, verbose_name="codigo")),
                ("name", models.CharField(max_length=160, verbose_name="nome")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="ativo")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
            ],
            options={
                "verbose_name": "espelho de tipo de contribuicao",
                "verbose_name_plural": "espelhos de tipos de contribuicao",
                "ordering": ["organization_id", "name", "legacy_id"],
            },
        ),
        migrations.AddIndex(
            model_name="contributiontypesnapshot",
            index=models.Index(fields=["organization_id", "is_active", "name"], name="contributio_org_idx"),
        ),
    ]
