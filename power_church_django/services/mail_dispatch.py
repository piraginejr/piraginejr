from __future__ import annotations

import base64
import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail import EmailMessage

from power_church_core.normalization import normalize_query


class MailDispatchError(RuntimeError):
    """Raised when the configured mail provider cannot send the message."""


@dataclass
class MailAttachment:
    filename: str
    content: bytes
    content_type: str


@dataclass
class MailDispatchResult:
    provider: str
    accepted: bool
    external_id: str = ""
    metadata: dict[str, Any] | None = None


def configured_provider() -> str:
    return normalize_query(getattr(settings, "POWER_CHURCH_EMAIL_PROVIDER", "smtp")).lower() or "smtp"


def graph_sender_user() -> str:
    return normalize_query(getattr(settings, "POWER_CHURCH_GRAPH_SENDER_USER", ""))


def graph_config_snapshot() -> dict[str, Any]:
    return {
        "provider": configured_provider(),
        "tenant_id": normalize_query(getattr(settings, "POWER_CHURCH_GRAPH_TENANT_ID", "")),
        "client_id": normalize_query(getattr(settings, "POWER_CHURCH_GRAPH_CLIENT_ID", "")),
        "has_client_secret": bool(normalize_query(getattr(settings, "POWER_CHURCH_GRAPH_CLIENT_SECRET", ""))),
        "sender_user": graph_sender_user(),
        "scope": normalize_query(getattr(settings, "POWER_CHURCH_GRAPH_SCOPE", "https://graph.microsoft.com/.default")),
        "base_url": normalize_query(getattr(settings, "POWER_CHURCH_GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")),
        "timeout_seconds": int(getattr(settings, "POWER_CHURCH_GRAPH_TIMEOUT_SECONDS", 30) or 30),
    }


def send_email_message(
    *,
    subject: str,
    body: str,
    from_email: str,
    to_emails: list[str],
    reply_to: list[str] | None = None,
    attachments: list[MailAttachment] | None = None,
    save_to_sent_items: bool = True,
    dry_run: bool = False,
) -> MailDispatchResult:
    provider = configured_provider()
    if provider == "smtp":
        return _send_via_smtp(
            subject=subject,
            body=body,
            from_email=from_email,
            to_emails=to_emails,
            reply_to=reply_to or [],
            attachments=attachments or [],
            dry_run=dry_run,
        )
    if provider == "microsoft_graph":
        return _send_via_microsoft_graph(
            subject=subject,
            body=body,
            from_email=from_email,
            to_emails=to_emails,
            reply_to=reply_to or [],
            attachments=attachments or [],
            save_to_sent_items=save_to_sent_items,
            dry_run=dry_run,
        )
    raise MailDispatchError(f"Provedor de e-mail nao suportado: {provider}")


def _send_via_smtp(
    *,
    subject: str,
    body: str,
    from_email: str,
    to_emails: list[str],
    reply_to: list[str],
    attachments: list[MailAttachment],
    dry_run: bool,
) -> MailDispatchResult:
    if dry_run:
        return MailDispatchResult(
            provider="smtp",
            accepted=True,
            metadata={
                "dry_run": True,
                "to": to_emails,
                "subject": subject,
                "attachment_count": len(attachments),
            },
        )
    message = EmailMessage(subject, body, from_email, to_emails, reply_to=reply_to)
    for attachment in attachments:
        message.attach(attachment.filename, attachment.content, attachment.content_type)
    sent_count = int(message.send(fail_silently=False) or 0)
    if sent_count <= 0:
        raise MailDispatchError("Backend SMTP nao confirmou entrega.")
    return MailDispatchResult(
        provider="smtp",
        accepted=True,
        metadata={"to": to_emails, "attachment_count": len(attachments)},
    )


def _graph_required_settings() -> tuple[str, str, str, str, str, str, int]:
    tenant_id = normalize_query(getattr(settings, "POWER_CHURCH_GRAPH_TENANT_ID", ""))
    client_id = normalize_query(getattr(settings, "POWER_CHURCH_GRAPH_CLIENT_ID", ""))
    client_secret = normalize_query(getattr(settings, "POWER_CHURCH_GRAPH_CLIENT_SECRET", ""))
    sender_user = graph_sender_user()
    scope = normalize_query(getattr(settings, "POWER_CHURCH_GRAPH_SCOPE", "https://graph.microsoft.com/.default"))
    base_url = normalize_query(getattr(settings, "POWER_CHURCH_GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0"))
    timeout_seconds = int(getattr(settings, "POWER_CHURCH_GRAPH_TIMEOUT_SECONDS", 30) or 30)
    missing = []
    if not tenant_id:
        missing.append("POWER_CHURCH_GRAPH_TENANT_ID")
    if not client_id:
        missing.append("POWER_CHURCH_GRAPH_CLIENT_ID")
    if not client_secret:
        missing.append("POWER_CHURCH_GRAPH_CLIENT_SECRET")
    if not sender_user:
        missing.append("POWER_CHURCH_GRAPH_SENDER_USER")
    if missing:
        raise MailDispatchError("Configuracao Microsoft Graph incompleta: " + ", ".join(missing))
    return tenant_id, client_id, client_secret, sender_user, scope, base_url, timeout_seconds


def _graph_access_token(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    scope: str,
    timeout_seconds: int,
) -> tuple[str, dict[str, Any]]:
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    payload = urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        }
    ).encode("utf-8")
    request = Request(token_url, data=payload, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urlopen(request, timeout=timeout_seconds, context=ssl.create_default_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise MailDispatchError(f"Microsoft Graph token falhou ({exc.code}): {raw}") from exc
    except URLError as exc:
        raise MailDispatchError(f"Microsoft Graph token nao alcancou o servidor: {exc}") from exc
    token = normalize_query(data.get("access_token"))
    if not token:
        raise MailDispatchError("Resposta do token Microsoft Graph nao trouxe access_token.")
    return token, data


def _graph_message_payload(
    *,
    subject: str,
    body: str,
    from_email: str,
    to_emails: list[str],
    reply_to: list[str],
    attachments: list[MailAttachment],
    save_to_sent_items: bool,
) -> dict[str, Any]:
    sender = graph_sender_user() or from_email
    message: dict[str, Any] = {
        "subject": subject,
        "body": {
            "contentType": "Text",
            "content": body,
        },
        "toRecipients": [{"emailAddress": {"address": value}} for value in to_emails if normalize_query(value)],
        "from": {"emailAddress": {"address": sender}},
    }
    if reply_to:
        message["replyTo"] = [{"emailAddress": {"address": value}} for value in reply_to if normalize_query(value)]
    if attachments:
        message["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": item.filename,
                "contentType": item.content_type,
                "contentBytes": base64.b64encode(item.content).decode("ascii"),
            }
            for item in attachments
        ]
    return {
        "message": message,
        "saveToSentItems": bool(save_to_sent_items),
    }


def _send_via_microsoft_graph(
    *,
    subject: str,
    body: str,
    from_email: str,
    to_emails: list[str],
    reply_to: list[str],
    attachments: list[MailAttachment],
    save_to_sent_items: bool,
    dry_run: bool,
) -> MailDispatchResult:
    sender_user = graph_sender_user() or normalize_query(from_email) or "recebimento@localhost"
    scope = normalize_query(getattr(settings, "POWER_CHURCH_GRAPH_SCOPE", "https://graph.microsoft.com/.default"))
    base_url = normalize_query(getattr(settings, "POWER_CHURCH_GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0"))
    timeout_seconds = int(getattr(settings, "POWER_CHURCH_GRAPH_TIMEOUT_SECONDS", 30) or 30)
    payload = _graph_message_payload(
        subject=subject,
        body=body,
        from_email=from_email,
        to_emails=to_emails,
        reply_to=reply_to,
        attachments=attachments,
        save_to_sent_items=save_to_sent_items,
    )
    if dry_run:
        return MailDispatchResult(
            provider="microsoft_graph",
            accepted=True,
            metadata={
                "dry_run": True,
                "sender_user": sender_user,
                "to": to_emails,
                "subject": subject,
                "attachment_count": len(attachments),
                "payload_preview": payload,
            },
        )
    tenant_id, client_id, client_secret, sender_user, scope, base_url, timeout_seconds = _graph_required_settings()
    token, token_payload = _graph_access_token(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        scope=scope,
        timeout_seconds=timeout_seconds,
    )
    request = Request(
        f"{base_url}/users/{sender_user}/sendMail",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=timeout_seconds, context=ssl.create_default_context()) as response:
            status_code = int(getattr(response, "status", 202) or 202)
            raw_body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise MailDispatchError(f"Microsoft Graph sendMail falhou ({exc.code}): {raw}") from exc
    except URLError as exc:
        raise MailDispatchError(f"Microsoft Graph sendMail nao alcancou o servidor: {exc}") from exc
    if status_code not in {200, 201, 202}:
        raise MailDispatchError(f"Microsoft Graph sendMail retornou status inesperado: {status_code}")
    return MailDispatchResult(
        provider="microsoft_graph",
        accepted=True,
        metadata={
            "sender_user": sender_user,
            "status_code": status_code,
            "token_type": token_payload.get("token_type") or "",
            "expires_in": token_payload.get("expires_in"),
            "response_body": raw_body,
        },
    )
