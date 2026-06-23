from __future__ import annotations


class LegacyDatabaseError(RuntimeError):
    """Erro de leitura em trilhas operacionais agora atendidas pelo runtime Postgres."""


class LegacyWriteError(RuntimeError):
    """Erro de validacao/escrita nas trilhas operacionais do runtime atual."""
