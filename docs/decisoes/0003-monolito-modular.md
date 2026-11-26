# 0003. Monolito modular

## Contexto
O sistema terá coleta, editais e análise. No tema 2 entram filas entre essas partes, e é preciso saber onde ficam as fronteiras.

## Opções
- **Monolito modular**, um processo com módulos de fronteira verificada.
- Processos separados desde o início.
- Microsserviços.

## Escolha
Monolito modular: `coleta`, `editais`, `analise` e `comum`, com as regras de fronteira checadas pelo import-linter no CI.

## Trade-off
Segue o princípio *Monolith First*: separar processos cedo traz custo operacional sem aprendizado de dados. As fronteiras entre módulos são exatamente onde as filas entram no tema 2 (em memória, depois pgmq, depois outbox e SQS). A pesquisa de repositórios (python-ddd, cosmicpython) confirmou o padrão. O custo é depender de disciplina, e por isso as regras ficam no CI e não só na convenção.
