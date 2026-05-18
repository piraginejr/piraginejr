"""Nucleo reutilizavel do Power Church.

Este pacote nasce para separar regras puras do prototipo web atual. A regra de
transicao e simples: primeiro extraimos funcoes sem efeito colateral, mantendo o
app existente como consumidor; depois os proximos alvos, como Django, reutilizam
este mesmo nucleo.
"""

