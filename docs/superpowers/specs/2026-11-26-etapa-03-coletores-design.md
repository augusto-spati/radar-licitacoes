# Radar de licitações: desenho da etapa 3 (coletores Sanepar e Compesa)

Escrito em 2026-11-26, a partir da conversa com o Augusto e da pesquisa em `~/carreira/projetos/radar-portais/` (resumo, `sanepar-prerequisitos.md`, `compesa-portal-tecnico.md` e as amostras de HTML).

## Intenção

Até aqui o radar só lê o PNCP. As companhias estaduais das UFs da FR quase não publicam lá. Das quatro, só duas ainda têm licitação pública coletável:
- a **Sanepar**, com aderência alta: pesquisa de vazamentos e redes;
- a **Compesa**, que depois da concessão de 2026 ficou só com obras e água por atacado.

A etapa adiciona as duas como fontes. Isso exige mudar o modelo para **um edital, várias fontes**. É conteúdo de sistemas de dados: migração de chave, coleta por retrato em vez de janela, e detecção de mudança por hash quando a fonte não informa retificação.

Ordem combinada: Sanepar primeiro, depois Compesa. A etapa vem antes da 2b (AWS).

## Metas, não metas e o que seria fracasso

- **Metas:**
  - editais de Obras e Engenharia da Sanepar e todas as licitações da Compesa, no mesmo banco, filtro, API e tela que os do PNCP;
  - coleta diária com cortesia;
  - detecção de alteração;
  - o PNCP continua funcionando sem regressão.
- **Não metas:**
  - Copasa (portal fora do ar depois da privatização);
  - Sabesp e as concessionárias de PE (não têm lista pública);
  - baixar e ler os PDFs dos editais (tema 4);
  - Bens e Serviços da Sanepar, que já chegam pelo PNCP.
- **Seria fracasso:**
  - duplicar edital entre fontes;
  - um coletor que derruba ou bloqueia o site da fonte;
  - uma mudança de layout da fonte que passa em silêncio, com o coletor gravando zero editais como se fosse sucesso;
  - quebrar o contrato do front.

## Decisões

| # | Decisão | Quem | Por quê |
|---|---|---|---|
| E3-1 | Opção 1: tabela `edital` única com `fonte` + `id_na_fonte`; cada fonte num submódulo de `coleta` que entrega um edital normalizado a `editais.registrar` | Augusto | Uma tabela por fonte espalha o filtro e a busca; um módulo por fonte repete retrato, bruto, falha e auditoria |
| E3-2 | Sanepar só pela série Obras e Engenharia (OE) | Claude, pela pesquisa | Os pregões de Bens e Serviços já estão no PNCP (198 de ~210 em 3 meses). OE não está em nenhum. Assim não há duplicata entre fontes |
| E3-3 | Compesa: todas as licitações da lista | Claude | A Compesa quase não aparece no PNCP (3 editais em 30 dias) |
| E3-4 | A primeira tarefa da etapa fecha as três brechas da guarda do contrato do front, achadas na revisão final da 2a | Claude | (a) o job roda o script da base e não o do commit; (b) guardar `frontend/tsconfig.json` e `frontend/vitest.config.*`/`vite.config.*` por prefixo; (c) documentar que a guarda depende do fluxo por PR, já obrigatório na `main` |

## Modelo de dados

- **Tabela `edital`:**
  - chave primária composta `(fonte, id_na_fonte)`, com `fonte` em `pncp | sanepar | compesa`;
  - a migração preenche os editais existentes com `fonte='pncp'` e `id_na_fonte=numero_controle_pncp`;
  - `numero_controle_pncp` deixa de ser chave.
- **Campos normalizados:**
  - `modalidade_nome` (texto). O `modalidade_id` vira opcional e só existe no PNCP;
  - `situacao_normalizada` (`aberta | em_julgamento | encerrada | outra`) mais a situação original;
  - `uf`;
  - `municipio`: na Compesa vem da fonte; na Sanepar fica nulo, porque a lista não traz;
  - `valor_estimado`: vem na Compesa (custo estimado), não vem na Sanepar;
  - as datas (publicação; abertura ou sessão; encerramento das propostas);
  - `link_origem`;
  - `bruto`;
  - `hash_conteudo`.
- **Regra de "aberto":** passa a ser `situacao_normalizada = 'aberta' AND data_encerramento_proposta > agora`. Hoje ela depende de `situacao_id = 1` do PNCP. O filtro de modalidades do `filtro.toml` vale só para o PNCP.
- **Busca de texto e índices:** a coluna `busca` e o índice GIN continuam iguais. Medir com `EXPLAIN` antes e depois da migração de chave.
- **Coleta por retrato:** uma tabela `coleta_retrato` (fonte, iniciado_em, concluido_em, status, páginas, registros, erro), ao lado de `coleta_janela`. A página bruta passa a apontar para janela **ou** retrato e passa a guardar HTML como texto. A ida dos brutos para o S3 continua na 2b.
- **Falha nunca vira vazio:** um retrato que termina com zero editais, numa fonte que nunca tem zero, é tratado como falha. O sinal típico é mudança de layout: os seletores não acham nada. O limite mínimo fica por fonte, na configuração.
- **Detecção de alteração:** hash dos campos normalizados e da lista de anexos. A versão nova substitui a anterior quando o hash muda, e o log registra a alteração.

## Coletores

### Sanepar
- **Lista:** `https://www.sanepar.com.br/licitacoes`, com os filtros de tipo OE e situações Abertura e Julgamento, paginação começando em `page=0` e 20 linhas por página.
- **Detalhe:** `/licitacao/...`, para os anexos e os campos completos. É buscado só quando o edital é novo ou mudou na lista.
- **Chave:** `OE-<numero>/<ano2d>`.
- **Origem:** HTML vindo do servidor, lido com `requests` e um parser de HTML; os seletores estão em `sanepar-prerequisitos.md`.
- **Cortesia:**
  - User-Agent `radar-licitacoes-estudo (github.com/augusto-spati/radar-licitacoes)`;
  - 3 s entre requisições e ETag/If-Modified-Since quando houver;
  - uma rodada por dia;
  - para tudo e registra falha em 403/429 ou se aparecer captcha.
- **Restrições da fonte:** não há termos de uso, e o robots.txt não bloqueia.

### Compesa
- **Fluxo:** cookie jar, `form.jsp` → `openform.do` → `gridEdit.do` (100 por página) → `navigate.do?param=next` → `gridEdit.do?param=refreshpage`.
- **Resposta:** texto JavaScript (`data_817140 = [...]`), lido sem executar JS.
- **Datas:** vêm `dd/MM/yyyy` na primeira página e `MM/dd/yyyy` nas seguintes, então são normalizadas por página, com teste.
- **Chave:** `lc_chave`, por exemplo `2026LC213CEL-1`.
- **Campos extras:** município, região, órgão solicitante, classificação, custo estimado.
- **Cortesia:** a mesma da Sanepar. O firewall bloqueou um POST do `executeRule.do`, então o coletor não usa POST.
- **Link para o tio:** aponta para a página de licitações da Compesa. O link direto para o processo fica a pesquisar.

### Comum
- `radar atualizar` passa a rodar PNCP, Sanepar e Compesa. Cada fonte falha sozinha, sem derrubar as outras.
- `radar auditar` passa a mostrar o último retrato de cada fonte.
- **Testes dos parsers:** rodam contra as amostras públicas salvas na pesquisa, copiadas para `tests/dados/`. A amostra da Compesa é reduzida, porque tem cerca de 1 MB.
- **Teste contra a fonte real:** um por fonte, com a marca `rede`, fora do CI.

## Front e API

- O id público do edital passa a ser `<fonte>~<id_na_fonte>`. A rota `/edital/:id` não muda, porque a lista de rotas é fechada. O id é codificado num segmento só.
- O resumo e o detalhe ganham `fonte`. O cartão mostra um selo com o nome da fonte ("PNCP", "Sanepar", "Compesa").
- O link de reserva depende da fonte:
  - PNCP: o link montado do edital;
  - Sanepar: a página do detalhe;
  - Compesa: a página de licitações.
- O filtro da tela ganha "Fonte" só se o Augusto pedir. Por padrão, não.
- O commit que mexer nos arquivos guardados leva `Contrato-Front: aprovado`.
- Os screenshots são regerados só se o shell mudar (não deve mudar).

## Saídas da etapa

- O código.
- `docs/etapas/03-coletores.md`, com perguntas de entrevista sobre:
  - migração de chave primária em produção;
  - retrato contra janela;
  - detectar mudança sem data de atualização;
  - cortesia com a fonte;
  - por que zero resultados é falha.
- O roteiro do vídeo.
- Itens no checklist de sistemas de dados.

## Respostas à revisão de quem vai construir (escolhas do Claude)

1. **Prazo e "aberto" nas fontes novas.**
   - `data_encerramento_proposta` recebe:
     - na Sanepar, o campo "Propostas" do detalhe; se ele faltar, a data e hora de abertura da sessão;
     - na Compesa, ABERTURA mais HORA.
   - Em licitação, a proposta vale até a abertura da sessão, então essa data é o prazo útil para o tio.
   - Prazo nulo nunca conta como aberto. O edital continua consultável por `todos`.
2. **Edital que sai do retrato.**
   - Coluna `visto_em`. A Sanepar sai da lista quando o edital deixa a situação Abertura ou Julgamento.
   - Quando um retrato **completo e bem-sucedido** não traz mais um edital que estava `aberta` ou `em_julgamento`, ele vira `encerrada`, com a situação original `"saiu da lista"`.
   - Retrato com qualquer página em falha não reconcilia nada.
   - A Compesa é coletada pela lista inteira, com todas as situações, então a situação vem da própria fonte.
3. **Mapa de situações.**

   | Fonte | aberta | em_julgamento | encerrada | outra |
   |---|---|---|---|---|
   | PNCP | `situacao_id` 1 (Divulgada) | — | 2 (Revogada), 3 (Anulada) | 4 (Suspensa) |
   | Sanepar | Abertura | Julgamento | ao sair da lista | — |
   | Compesa | Abertura | Habilitação | Homologação, Contrato, Fracassada, Deserta, Revogada | Suspensa e qualquer valor novo (registrado em log) |

4. **Migração.**
   - Só existe o banco local, sem produção.
   - A migração do Alembic é reversível: preenche a fonte e o id, troca a chave e tem `downgrade`.
   - Ids antigos não precisam continuar valendo. O protótipo publicado é regerado.
5. **Hash e anexos.**
   - A cada rodada, o detalhe de **todo edital aberto ou em julgamento** da Sanepar é buscado de novo, cerca de 60 requisições (uns 3 min com 3 s entre elas). Sem isso, um anexo novo de retificação passaria despercebido.
   - O hash cobre os campos normalizados mais a lista de anexos (nome e id). Não entram campos voláteis, como o horário da coleta.
6. **Filtros nas fontes novas.**
   - Valem para as três fontes os filtros de UF, de texto (linhas de serviço) e de aberto.
   - O filtro de modalidades vale só para o PNCP.
   - Sanepar (PR) e Compesa (PE) já estão nas UFs do `filtro.toml`.
7. **Duplicata Sanepar OE × PNCP.** Conferido em 2026-11-26 pela API do PNCP com o CNPJ 76484013000145: nenhuma concorrência (modalidades 4 e 5) de junho a novembro de 2026. A Sanepar fica só com OE, sem chave de dedupe entre fontes.
8. **Limite mínimo por fonte.**
   - Um retrato abaixo do limite, ou com página em falha, termina com status `falha`. Ele não grava editais e não reconcilia ausências.
   - Uma queda brusca sem chegar ao limite (menos da metade do retrato anterior) gera o aviso `retrato_encolheu` no log e grava normalmente.
   - O valor de cada limite sai das amostras.

## O que fica para quem construir

- Nomes de tabelas e colunas novas, além dos citados.
- A biblioteca de parser HTML (`selectolax` ou `beautifulsoup4`). A escolha vem com uma linha de justificativa.
- O limite mínimo de registros por fonte, que vem das amostras.
- O formato exato do log de alteração.
