from __future__ import annotations

from django.db import models


class PersonSnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    preferred_unit_id = models.IntegerField("unidade preferencial legado", null=True, blank=True)
    internal_code = models.CharField("codigo interno", max_length=80, blank=True)
    name = models.CharField("nome", max_length=240)
    normalized_name = models.CharField("nome normalizado", max_length=240, db_index=True)
    social_name = models.CharField("nome social", max_length=240, blank=True)
    cpf = models.CharField("cpf", max_length=32, blank=True, db_index=True)
    rg = models.CharField("rg", max_length=64, blank=True)
    birth_date = models.DateField("data de nascimento", null=True, blank=True)
    birth_date_raw = models.CharField("data de nascimento bruta", max_length=32, blank=True)
    sex = models.CharField("sexo", max_length=32, blank=True)
    marital_status = models.CharField("estado civil", max_length=64, blank=True)
    primary_email = models.CharField("e-mail principal", max_length=240, blank=True, db_index=True)
    normalized_email = models.CharField("e-mail normalizado", max_length=240, blank=True, db_index=True)
    primary_phone = models.CharField("telefone principal", max_length=64, blank=True)
    primary_whatsapp = models.CharField("whatsapp principal", max_length=64, blank=True)
    status = models.CharField("status", max_length=64, blank=True, db_index=True)
    is_archived = models.BooleanField("arquivo morto", default=False, db_index=True)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    notes = models.TextField("observacoes", blank=True)
    import_lot_id = models.IntegerField("lote de importacao legado", null=True, blank=True)
    created_at_legacy = models.DateTimeField("criado em legado", null=True, blank=True)
    updated_at_legacy = models.DateTimeField("atualizado em legado", null=True, blank=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "espelho de pessoa"
        verbose_name_plural = "espelhos de pessoas"
        ordering = ["normalized_name", "legacy_id"]

    def __str__(self) -> str:
        return self.name


class PersonContactSnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    person = models.ForeignKey(PersonSnapshot, on_delete=models.CASCADE, related_name="contacts")
    contact_type = models.CharField("tipo", max_length=64, db_index=True)
    value = models.CharField("valor", max_length=240)
    normalized_value = models.CharField("valor normalizado", max_length=240, db_index=True)
    is_primary = models.BooleanField("principal", default=False, db_index=True)
    notes = models.TextField("observacoes", blank=True)
    created_at_legacy = models.DateTimeField("criado em legado", null=True, blank=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "espelho de contato"
        verbose_name_plural = "espelhos de contatos"
        ordering = ["person_id", "-is_primary", "contact_type", "legacy_id"]

    def __str__(self) -> str:
        return f"{self.contact_type}: {self.value}"


class PersonAddressSnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    person = models.ForeignKey(PersonSnapshot, on_delete=models.CASCADE, related_name="addresses")
    address_type = models.CharField("tipo", max_length=64, db_index=True)
    cep = models.CharField("cep", max_length=16, blank=True, db_index=True)
    street = models.CharField("logradouro", max_length=240, blank=True)
    number = models.CharField("numero", max_length=32, blank=True)
    complement = models.CharField("complemento", max_length=240, blank=True)
    neighborhood = models.CharField("bairro", max_length=160, blank=True)
    city = models.CharField("cidade", max_length=160, blank=True, db_index=True)
    state = models.CharField("uf", max_length=8, blank=True)
    is_primary = models.BooleanField("principal", default=False, db_index=True)
    normalized_address = models.CharField("endereco normalizado", max_length=500, blank=True, db_index=True)
    created_at_legacy = models.DateTimeField("criado em legado", null=True, blank=True)
    updated_at_legacy = models.DateTimeField("atualizado em legado", null=True, blank=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "espelho de endereco"
        verbose_name_plural = "espelhos de enderecos"
        ordering = ["person_id", "-is_primary", "legacy_id"]

    def __str__(self) -> str:
        return self.street or f"Endereco #{self.legacy_id}"


class PersonRelationshipSnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    person = models.ForeignKey(PersonSnapshot, on_delete=models.CASCADE, related_name="outgoing_relationships")
    related_person = models.ForeignKey(
        PersonSnapshot,
        on_delete=models.CASCADE,
        related_name="incoming_relationships",
    )
    relationship_type = models.CharField("tipo de relacionamento", max_length=80, db_index=True)
    notes = models.TextField("observacoes", blank=True)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    created_at_legacy = models.DateTimeField("criado em legado", null=True, blank=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "espelho de relacionamento"
        verbose_name_plural = "espelhos de relacionamentos"
        ordering = ["person_id", "relationship_type", "related_person_id", "legacy_id"]

    def __str__(self) -> str:
        return f"{self.person_id}->{self.related_person_id} ({self.relationship_type})"


class HouseholdProfile(models.Model):
    signature = models.CharField("assinatura do nucleo", max_length=500, unique=True)
    head_person_id = models.IntegerField("cabeca da familia", null=True, blank=True, db_index=True)
    display_name_override = models.CharField("nome de guerra", max_length=240, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "perfil de familia domiciliar"
        verbose_name_plural = "perfis de familias domiciliares"
        ordering = ["signature"]

    def __str__(self) -> str:
        return self.display_name_override or self.signature
