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


class PersonProfileSnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    person = models.ForeignKey(PersonSnapshot, on_delete=models.CASCADE, related_name="profiles")
    profile = models.CharField("perfil", max_length=120, db_index=True)
    start_date_raw = models.CharField("data inicio bruta", max_length=32, blank=True)
    end_date_raw = models.CharField("data fim bruta", max_length=32, blank=True)
    notes = models.TextField("observacoes", blank=True)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "espelho de perfil da pessoa"
        verbose_name_plural = "espelhos de perfis da pessoa"
        ordering = ["person_id", "profile", "legacy_id"]
        db_table = "people_personprofilesnapshot"

    def __str__(self) -> str:
        return f"{self.person_id}:{self.profile}"


class PersonHistorySnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    person = models.ForeignKey(PersonSnapshot, on_delete=models.CASCADE, related_name="history_entries")
    event_type = models.CharField("tipo de evento", max_length=120, blank=True, db_index=True)
    event_date_raw = models.CharField("data do evento bruta", max_length=32, blank=True)
    title = models.CharField("titulo", max_length=240, blank=True)
    description = models.TextField("descricao", blank=True)
    origin = models.CharField("origem", max_length=240, blank=True)
    destination = models.CharField("destino", max_length=240, blank=True)
    created_at_legacy = models.DateTimeField("criado em legado", null=True, blank=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "espelho de historico da pessoa"
        verbose_name_plural = "espelhos de historico da pessoa"
        ordering = ["person_id", "-created_at_legacy", "-legacy_id"]
        db_table = "people_personhistorysnapshot"

    def __str__(self) -> str:
        return self.title or f"Historico #{self.legacy_id}"


class PersonContributorSnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    person = models.ForeignKey(PersonSnapshot, on_delete=models.CASCADE, related_name="contributors")
    name = models.CharField("nome", max_length=240)
    contributor_type = models.CharField("tipo", max_length=64, blank=True, db_index=True)
    primary_document = models.CharField("documento principal", max_length=64, blank=True)
    document_type = models.CharField("tipo do documento", max_length=32, blank=True)
    origin = models.CharField("origem", max_length=120, blank=True)
    quality = models.CharField("qualidade", max_length=120, blank=True)
    status = models.CharField("status", max_length=64, blank=True)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "espelho de contribuinte vinculado"
        verbose_name_plural = "espelhos de contribuintes vinculados"
        ordering = ["person_id", "name", "legacy_id"]
        db_table = "people_personcontributorsnapshot"

    def __str__(self) -> str:
        return self.name


class PersonIdentifierSnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    person = models.ForeignKey(PersonSnapshot, on_delete=models.CASCADE, related_name="identifiers")
    contributor_legacy_id = models.IntegerField("contribuinte legado", null=True, blank=True, db_index=True)
    identifier_type = models.CharField("tipo", max_length=64, db_index=True)
    value = models.CharField("valor", max_length=240)
    is_primary = models.BooleanField("principal", default=False, db_index=True)
    notes = models.TextField("observacoes", blank=True)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "espelho de identificador financeiro"
        verbose_name_plural = "espelhos de identificadores financeiros"
        ordering = ["person_id", "-is_primary", "identifier_type", "legacy_id"]
        db_table = "people_personidentifiersnapshot"

    def __str__(self) -> str:
        return f"{self.identifier_type}:{self.value}"


class FinancialIdentityLookup(models.Model):
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    person = models.ForeignKey(PersonSnapshot, on_delete=models.CASCADE, related_name="financial_identity_lookups")
    lookup_kind = models.CharField("tipo de consulta", max_length=64, db_index=True)
    value = models.CharField("valor original", max_length=240)
    normalized_value = models.CharField("valor normalizado", max_length=240, db_index=True)
    source = models.CharField("origem", max_length=120, blank=True)
    priority = models.IntegerField("prioridade", default=0, db_index=True)
    notes = models.TextField("observacoes", blank=True)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "consulta de identidade financeira"
        verbose_name_plural = "consultas de identidade financeira"
        ordering = ["person_id", "-priority", "lookup_kind", "normalized_value", "id"]
        db_table = "people_financialidentitylookup"
        indexes = [
            models.Index(fields=["organization_id", "is_active", "normalized_value"]),
            models.Index(fields=["person", "is_active", "lookup_kind"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["person", "lookup_kind", "normalized_value", "source"],
                name="people_financialidentitylookup_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.person_id}:{self.lookup_kind}:{self.value}"


class PersonContributionSnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    person = models.ForeignKey(PersonSnapshot, on_delete=models.CASCADE, related_name="contributions")
    contributor_legacy_id = models.IntegerField("contribuinte legado", null=True, blank=True, db_index=True)
    received_at = models.DateField("data de recebimento", null=True, blank=True, db_index=True)
    received_at_raw = models.CharField("data de recebimento bruta", max_length=32, blank=True)
    competence = models.CharField("competencia", max_length=32, blank=True, db_index=True)
    competence_order = models.IntegerField("ordem da competencia", default=0, db_index=True)
    amount = models.DecimalField("valor", max_digits=14, decimal_places=2, default=0)
    operational_status = models.CharField("status operacional", max_length=64, blank=True, db_index=True)
    contribution_type_name = models.CharField("tipo", max_length=160, blank=True)
    receipt_method_name = models.CharField("forma", max_length=160, blank=True)
    source_name = models.CharField("origem", max_length=240, blank=True)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "espelho de contribuicao da pessoa"
        verbose_name_plural = "espelhos de contribuicoes da pessoa"
        ordering = ["person_id", "-competence_order", "-received_at", "-legacy_id"]
        db_table = "people_personcontributionsnapshot"

    def __str__(self) -> str:
        return f"{self.person_id}:{self.amount}"


class PersonSecureTrashSnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    person_legacy_id = models.IntegerField("pessoa legada", db_index=True)
    person_name = models.CharField("nome da pessoa", max_length=240, blank=True)
    person_cpf = models.CharField("cpf da pessoa", max_length=32, blank=True)
    original_status = models.CharField("status original", max_length=64, blank=True)
    original_code = models.CharField("codigo original", max_length=80, blank=True)
    reason = models.TextField("motivo", blank=True)
    operator = models.CharField("operador", max_length=160, blank=True)
    snapshot_data = models.JSONField("snapshot", default=dict, blank=True)
    restored = models.BooleanField("restaurado", default=False, db_index=True)
    restored_at = models.DateTimeField("restaurado em", null=True, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "lixeira segura de pessoa"
        verbose_name_plural = "lixeira segura de pessoas"
        ordering = ["-created_at", "-legacy_id"]
        db_table = "people_personsecuretrashsnapshot"

    def __str__(self) -> str:
        return self.person_name or f"lixeira #{self.legacy_id}"


class PersonSecurePurgeSnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    person_legacy_id = models.IntegerField("pessoa legada", db_index=True)
    trash_legacy_id = models.IntegerField("lixeira legada", db_index=True)
    name_hash = models.CharField("hash do nome", max_length=128, blank=True)
    cpf_hash = models.CharField("hash do cpf", max_length=128, blank=True)
    reason = models.TextField("motivo", blank=True)
    operator = models.CharField("operador", max_length=160, blank=True)
    tombstone_data = models.JSONField("tombstone", default=dict, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "purga segura de pessoa"
        verbose_name_plural = "purgas seguras de pessoas"
        ordering = ["-created_at", "-legacy_id"]
        db_table = "people_personsecurepurgesnapshot"

    def __str__(self) -> str:
        return f"purga #{self.legacy_id}"


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


class NativePeopleImportLot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    import_type = models.CharField("tipo", max_length=80, db_index=True)
    file_name = models.CharField("arquivo", max_length=260, blank=True)
    file_hash = models.CharField("hash do arquivo", max_length=128, blank=True)
    status = models.CharField("status", max_length=64, blank=True, db_index=True)
    total_lines = models.IntegerField("linhas totais", default=0)
    imported_lines = models.IntegerField("linhas importadas", default=0)
    ignored_lines = models.IntegerField("linhas ignoradas", default=0)
    error_lines = models.IntegerField("linhas com erro", default=0)
    open_pendencies = models.IntegerField("pendencias abertas", default=0)
    active_people = models.IntegerField("pessoas ativas", default=0)
    without_name = models.IntegerField("fichas sem nome", default=0)
    review_mappings = models.IntegerField("campos para revisar", default=0)
    created_at_display = models.CharField("criado em exibicao", max_length=80, blank=True)
    confirmed_at_display = models.CharField("confirmado em exibicao", max_length=80, blank=True)
    status_rows_json = models.JSONField("resumo por status", default=list, blank=True)
    mapping_rows_json = models.JSONField("mapeamentos", default=list, blank=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "lote nativo de importacao de pessoas"
        verbose_name_plural = "lotes nativos de importacao de pessoas"
        ordering = ["-legacy_id"]
        db_table = "people_nativepeopleimportlot"

    def __str__(self) -> str:
        return f"lote #{self.legacy_id}"


class NativePeopleImportPending(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    lot = models.ForeignKey(NativePeopleImportLot, on_delete=models.CASCADE, related_name="pendings")
    line_number = models.IntegerField("numero da linha", default=0, db_index=True)
    severity = models.CharField("severidade", max_length=32, blank=True, db_index=True)
    issue_type = models.CharField("tipo", max_length=120, blank=True, db_index=True)
    description = models.TextField("descricao", blank=True)
    suggested_action = models.TextField("acao sugerida", blank=True)
    resolved = models.BooleanField("resolvido", default=False, db_index=True)
    person_name = models.CharField("nome da pessoa", max_length=240, blank=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "pendencia nativa de importacao de pessoas"
        verbose_name_plural = "pendencias nativas de importacao de pessoas"
        ordering = ["resolved", "-severity", "line_number", "legacy_id"]
        db_table = "people_nativepeopleimportpending"

    def __str__(self) -> str:
        return f"pendencia #{self.legacy_id}"


class NativePeopleImportLine(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    lot = models.ForeignKey(NativePeopleImportLot, on_delete=models.CASCADE, related_name="lines")
    line_number = models.IntegerField("numero da linha", default=0, db_index=True)
    status = models.CharField("status", max_length=64, blank=True, db_index=True)
    original_name = models.CharField("nome original", max_length=240, blank=True)
    normalized_action = models.CharField("acao normalizada", max_length=160, blank=True)
    person_legacy_id = models.IntegerField("pessoa legada", null=True, blank=True, db_index=True)
    person_name = models.CharField("nome da ficha", max_length=240, blank=True)
    person_cpf = models.CharField("cpf da ficha", max_length=32, blank=True)
    person_status = models.CharField("status da ficha", max_length=64, blank=True)
    person_active = models.BooleanField("ficha ativa", default=False, db_index=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "linha nativa de importacao de pessoas"
        verbose_name_plural = "linhas nativas de importacao de pessoas"
        ordering = ["line_number", "legacy_id"]
        db_table = "people_nativepeopleimportline"

    def __str__(self) -> str:
        return f"linha #{self.line_number}"
