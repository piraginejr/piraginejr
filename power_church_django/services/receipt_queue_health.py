from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone

from power_church_django.apps.contributions.models import ReceiptDispatch


def receipt_queue_health_snapshot(
    *,
    max_pending_age_minutes: int = 20,
    max_failed_age_minutes: int = 120,
) -> dict[str, Any]:
    now = timezone.now()
    pending_window = max(1, int(max_pending_age_minutes or 20))
    failed_window = max(1, int(max_failed_age_minutes or 120))
    pending_cutoff = now - timedelta(minutes=pending_window)
    failed_cutoff = now - timedelta(minutes=failed_window)

    queue_qs = ReceiptDispatch.objects.all()
    pending_qs = queue_qs.filter(status=ReceiptDispatch.Status.PENDING)
    failed_qs = queue_qs.filter(status=ReceiptDispatch.Status.FAILED)
    sent_qs = queue_qs.filter(status=ReceiptDispatch.Status.SENT)

    pending_count = int(pending_qs.count())
    failed_count = int(failed_qs.count())
    sent_count = int(sent_qs.count())
    automatic_pending_count = int(pending_qs.filter(trigger=ReceiptDispatch.Trigger.AUTOMATIC).count())
    pending_without_attempts_count = int(pending_qs.filter(send_attempts=0).count())

    oldest_pending = pending_qs.order_by("created_at", "id").first()
    oldest_failed = failed_qs.order_by("created_at", "id").first()
    latest_attempt = queue_qs.exclude(last_attempt_at__isnull=True).order_by("-last_attempt_at", "-id").first()
    latest_sent = sent_qs.exclude(sent_at__isnull=True).order_by("-sent_at", "-id").first()

    recent_activity = bool(
        queue_qs.filter(last_attempt_at__gte=pending_cutoff).exists()
        or sent_qs.filter(sent_at__gte=pending_cutoff).exists()
    )
    stale_pending = bool(
        pending_count
        and oldest_pending is not None
        and oldest_pending.created_at <= pending_cutoff
        and not recent_activity
    )
    stale_failed = bool(
        failed_count
        and oldest_failed is not None
        and (oldest_failed.last_attempt_at or oldest_failed.created_at) <= failed_cutoff
    )

    severity = "ok"
    issues: list[str] = []
    if stale_pending:
        severity = "critical"
        issues.append("Fila pendente sem atividade recente.")
    if stale_failed:
        severity = "critical" if severity == "ok" else severity
        issues.append("Falhas antigas seguem sem tratamento.")
    if severity == "ok" and (pending_count or failed_count):
        severity = "warn"
    if failed_count and "Falhas presentes na fila." not in issues:
        issues.append("Falhas presentes na fila.")
    if pending_count and "Pendencias aguardando envio." not in issues:
        issues.append("Pendencias aguardando envio.")

    if severity == "ok":
        summary = "Fila de recibos sem bloqueio operacional detectado."
    elif severity == "warn":
        summary = "Fila de recibos com pendencias ou falhas, mas com sinais de atividade recente."
    else:
        summary = "Fila de recibos parada ou envelhecida, exigindo acao imediata."

    return {
        "severity": severity,
        "summary": summary,
        "issues": issues,
        "checked_at": timezone.localtime(now).strftime("%d/%m/%Y %H:%M"),
        "pending_count": pending_count,
        "failed_count": failed_count,
        "sent_count": sent_count,
        "automatic_pending_count": automatic_pending_count,
        "pending_without_attempts_count": pending_without_attempts_count,
        "recent_activity": recent_activity,
        "stale_pending": stale_pending,
        "stale_failed": stale_failed,
        "pending_window_minutes": pending_window,
        "failed_window_minutes": failed_window,
        "oldest_pending_at": timezone.localtime(oldest_pending.created_at).strftime("%d/%m/%Y %H:%M") if oldest_pending and oldest_pending.created_at else "",
        "oldest_failed_at": timezone.localtime((oldest_failed.last_attempt_at or oldest_failed.created_at)).strftime("%d/%m/%Y %H:%M") if oldest_failed and (oldest_failed.last_attempt_at or oldest_failed.created_at) else "",
        "latest_attempt_at": timezone.localtime(latest_attempt.last_attempt_at).strftime("%d/%m/%Y %H:%M") if latest_attempt and latest_attempt.last_attempt_at else "",
        "latest_sent_at": timezone.localtime(latest_sent.sent_at).strftime("%d/%m/%Y %H:%M") if latest_sent and latest_sent.sent_at else "",
        "delivery_note": "Status enviado significa aceito pelo provedor de e-mail; confirmacao de chegada depende da caixa do destinatario.",
    }
