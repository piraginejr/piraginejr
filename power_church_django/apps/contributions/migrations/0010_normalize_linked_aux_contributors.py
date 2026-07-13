from django.db import migrations


def normalize_linked_aux_contributors(apps, schema_editor):
    NativeAuxContributor = apps.get_model("contributions", "NativeAuxContributor")
    NativeContribution = apps.get_model("contributions", "NativeContribution")
    NativeEnvelope = apps.get_model("contributions", "NativeEnvelope")
    NativeEnvelopeItem = apps.get_model("contributions", "NativeEnvelopeItem")
    PersonContributionSnapshot = apps.get_model("people", "PersonContributionSnapshot")
    PersonContributorSnapshot = apps.get_model("people", "PersonContributorSnapshot")
    PersonSnapshot = apps.get_model("people", "PersonSnapshot")

    def append_merge_note(existing, merge_from_ids):
        note = f"Consolidado automaticamente a partir dos auxiliares {', '.join(str(value) for value in merge_from_ids)}."
        base = str(existing or "").strip()
        return f"{base}\n{note}".strip() if base else note

    processed_person_ids = set()
    linked_aux_qs = NativeAuxContributor.objects.exclude(person_legacy_id__isnull=True).exclude(person_legacy_id=0)
    for contributor in linked_aux_qs.order_by("-legacy_reference_id", "id").iterator():
        person_legacy_id = int(contributor.person_legacy_id or 0)
        if not person_legacy_id or person_legacy_id in processed_person_ids:
            continue
        processed_person_ids.add(person_legacy_id)
        person = PersonSnapshot.objects.filter(legacy_id=person_legacy_id, is_active=True).first()
        if person is None:
            continue
        linked_contributors = list(
            NativeAuxContributor.objects.filter(
                organization_id=int(contributor.organization_id or person.organization_id or 0),
                person_legacy_id=person_legacy_id,
                is_active=True,
            ).order_by("-legacy_reference_id", "id")
        )
        if not linked_contributors:
            continue
        canonical_aux = linked_contributors[0]
        duplicate_aux_ids = [int(row.pk or 0) for row in linked_contributors[1:]]
        canonical_contributor_id = (
            PersonContributorSnapshot.objects.filter(person=person, is_active=True)
            .order_by("legacy_id")
            .values_list("legacy_id", flat=True)
            .first()
        )
        canonical_contributor_id = int(canonical_contributor_id or canonical_aux.legacy_reference_id or 0) or None
        canonical_name = str(person.name or canonical_aux.name or "")
        canonical_document = str(person.cpf or canonical_aux.primary_document or "")
        contribution_ids = list(
            NativeContribution.objects.filter(
                native_aux_contributor_id__in=[int(row.pk or 0) for row in linked_contributors],
                is_active=True,
            ).values_list("legacy_id", flat=True)
        )
        if contribution_ids:
            NativeContribution.objects.filter(legacy_id__in=contribution_ids).update(
                person_legacy_id=int(person.legacy_id or 0),
                contributor_legacy_id=canonical_contributor_id,
                native_aux_contributor_id=None,
                contributor_source="person_snapshot",
                contributor_name=canonical_name,
                contributor_document=canonical_document,
                contributor_type="pf" if canonical_name else "",
            )
            PersonContributionSnapshot.objects.filter(legacy_id__in=contribution_ids).update(
                person=person,
                contributor_legacy_id=canonical_contributor_id,
            )
        NativeEnvelope.objects.filter(
            native_aux_contributor_id__in=[int(row.pk or 0) for row in linked_contributors],
            is_active=True,
        ).update(
            person_legacy_id=int(person.legacy_id or 0),
            contributor_legacy_id=canonical_contributor_id,
            native_aux_contributor_id=None,
        )
        NativeEnvelopeItem.objects.filter(
            native_aux_contributor_id__in=[int(row.pk or 0) for row in linked_contributors],
            is_active=True,
        ).update(
            person_legacy_id=int(person.legacy_id or 0),
            contributor_legacy_id=canonical_contributor_id,
            native_aux_contributor_id=None,
            contributor_name=canonical_name,
            contributor_document=canonical_document,
        )
        if duplicate_aux_ids:
            for duplicate in linked_contributors[1:]:
                duplicate.is_active = False
                duplicate.notes = append_merge_note(duplicate.notes, [int(canonical_aux.pk or 0)])
                duplicate.save(update_fields=["is_active", "notes", "updated_at"])
            canonical_aux.notes = append_merge_note(canonical_aux.notes, duplicate_aux_ids)
            canonical_aux.save(update_fields=["notes", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("contributions", "0009_rename_contributio_envelop_8e09ba_idx_contributio_envelop_5f9632_idx_and_more"),
        ("people", "0002_people_snapshots"),
    ]

    operations = [
        migrations.RunPython(normalize_linked_aux_contributors, migrations.RunPython.noop),
    ]
