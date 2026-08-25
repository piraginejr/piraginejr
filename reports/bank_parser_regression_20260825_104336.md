# Bank Parser Regression Audit

Gerado em: 2026-08-25T10:43:36-03:00
Resultado: OK

Esta auditoria protege contra regressao em que o total do extrato bate, mas o valor e atribuido a pessoa errada.

| Banco | Layout | Check | Status | Detalhe |
| --- | --- | --- | --- | --- |
| Sicoob | SICOOB_CONTA_CORRENTE | Sicoob mantem valor com a pessoa visual correta | OK | esperado={'Patricia Gomes de Andrade Brandao': 60.0, 'ODILON GUIMARAES JUNIOR': 1020.0, 'MARIA JOSE DE SOUZA PONCE RIBEIRO': 200.0}; observado={'Patricia Gomes de Andrade Brandao': 60.0, 'ODILON GUIMARAES JUNIOR': 1020.0, 'MARIA JOSE DE SOUZA PONCE RIBEIRO': 200.0}; linhas=3 |
| Sicoob | SICOOB_CONTA_CORRENTE | PDF real Sicoob junho - Odilon/Patricia | OK | arquivo=/app/data/statement_uploads/2026-06 Sicoob - Créditos.pdf; observado={'ODILON GUIMARAES JUNIOR': [1020.0], 'Patricia Gomes de Andrade Brandao': [60.0]} |
