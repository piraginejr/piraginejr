from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("contributions", "0008_nativeenvelopeprofileupdate"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="nativeenvelopeprofileupdate",
            new_name="contributio_envelop_5f9632_idx",
            old_name="contributio_envelop_8e09ba_idx",
        ),
        migrations.RenameIndex(
            model_name="nativeenvelopeprofileupdate",
            new_name="contributio_person__bb10a8_idx",
            old_name="contributio_person_l_4b5ff7_idx",
        ),
        migrations.RenameIndex(
            model_name="nativeenvelopeprofileupdate",
            new_name="contributio_organiz_8c6e02_idx",
            old_name="contributio_organiz_0c7348_idx",
        ),
    ]
