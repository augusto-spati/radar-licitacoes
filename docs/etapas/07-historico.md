# Etapa 7a: histórico de resultados (vencedores, valores e desconto)

Esta etapa traz o resultado das licitações já encerradas do recorte do radar (quem venceu, por quanto, com que desconto sobre o estimado) e mostra, na ficha de cada edital, o histórico do órgão e a faixa de desconto dos vencedores. O desenho está em `docs/superpowers/specs/2026-11-27-etapa-07-historico-design.md` e o plano em `docs/superpowers/plans/2026-11-27-etapa-07a-historico.md`. O pedido do Augusto foi "ir atrás do histórico de preços e vencedores"; o caminho escolhido (B) inclui o preço unitário, que fica para a etapa 7b.

## Por que

O dono de uma empresa de saneamento decide, nesta ordem: **entrar ou não** numa licitação (proposta de obra custa dias de engenharia), **quanto de desconto oferecer** e **onde concentrar esforço**. Para isso ele precisa ver, por órgão, quantas licitações deram em contrato e quantas fracassaram, quem costuma vencer e com que desconto, e uma faixa de referência de desconto para o tipo de serviço, o estado e o porte da obra. A faixa é referência, não "o preço que ganha": a tela sempre mostra quantos casos ela tem e a linha de inexequibilidade da lei (abaixo de 75% do estimado, ou 70% nas estatais da Lei 13.303).

## O que foi feito

- **Carga histórica** (`radar historico carregar --meses 12`). Percorre a consulta de publicações do PNCP mês a mês, por UF (o parâmetro `uf` reduz Pernambuco de 6.257 para 189 concorrências em jun/2026) e por modalidade do filtro, e grava só o que casa com algum termo das linhas de serviço. O Neon grátis tem 0,5 GB: 12 meses do PNCP inteiro estourariam o banco. Checkpoint por (UF, modalidade, mês) na tabela `historico_carga`, com commit por página: cair no meio e rodar de novo retoma da página seguinte. Uma trava (`historico`) impede duas cargas ao mesmo tempo. Roda uma vez, como task avulsa do Fargate (~33 h a uma página a cada ~30 s, ~US$ 0,50).
- **Resultados** (`radar resultados atualizar`, todo dia às 07:30). Para os editais do PNCP que casam com o filtro, com prazo já passado (até 180 dias), busca os itens e o resultado de cada item na `pncp-api` (rápida: 0,1–0,35 s), uma requisição por segundo. Grava em `resultado_item` (um por item) e resume em `resultado_busca` (situação, desconto, vencedor). Rebusca em 7 dias o que ainda está `parcial`, `em_andamento` ou `sem_itens`.
- **Regras do número central.** Desconto da contratação = `1 − Σ homologado ÷ Σ estimado`, só dos itens homologados com estimado conhecido (sem orçamento sigiloso e estimado > 0). O campo `percentualDesconto` do PNCP é ignorado: veio 0 em 129 de 132 resultados da amostra. Contratação com item ainda em andamento fica `parcial` e não entra na régua nem na mediana do vencedor (entra como vitória). Vitória conta uma por contratação: o fornecedor com o maior valor homologado nela. Vencedor pessoa física aparece como "Pessoa física", sem CPF.
- **Resposta instável do PNCP não apaga dado bom.** Lista de itens vazia com itens já gravados não apaga nada (só atualiza a data da busca); item homologado que volta sem resultado mantém o vencedor gravado; item repetido no mesmo lote é desduplicado.
- **Histórico do órgão** (`GET /api/historicos/{id}`). Para o órgão do edital (pelo CNPJ; Sanepar e Compesa pelos pregões delas no PNCP): contagens por situação, últimas 10 contratações, top 5 vencedores e uma **régua por linha de serviço do edital**, na UF e faixa de valor dele (até R$ 1 mi, R$ 1–5 mi, acima de R$ 5 mi), com p25, mediana e p75; com menos de 5 casos vem marcada "poucos casos" e acompanhada da régua só da linha. O próprio edital não entra na régua dele. A rota é separada de `/api/editais/{id}` porque o conversor `path` engoliria um sufixo `/historico`.
- **Tela.** Seção "Histórico do órgão" na ficha do edital, abaixo de "Aderência à FR", carregada à parte (se falhar, o edital continua na tela).

## Números da pesquisa (amostra de 56 contratações de saneamento, jun–set/2026)

- 66% têm resultado por item no PNCP; 11% fecharam sem vencedor; 23% ainda sem resultado 2 a 6 meses depois.
- Mediana do desconto sobre o estimado: 24,5% (por item e no global).
- O resultado chega a uma mediana de 34 dias da publicação.
- Volume do recorte: ~50 contratações por mês.

## O que fica para depois

- **7b, preço unitário.** No PNCP o preço unitário do vencedor de obra não aparece (0 de 11 obras da amostra); o preço estimado do órgão está na planilha do edital (≥ 9 de 37) e o TCE-PE tem o preço contratado por item, estruturado. A 7b extrai o estimado com IA e mostra o "estimado com o desconto do vencedor" como aproximação; em Pernambuco, o contratado real pelo TCE-PE.
- **TCE-PE para a Compesa** (resultados das obras dela).
- **Contratos, aditivos e perdedores** (cobertura baixa ou atraso grande).
- **DAE Jundiaí** como fonte nova (7c).

## Perguntas de entrevista

1. Por que a carga histórica não reaproveitou as janelas da etapa 4b, se elas já sabem coletar o PNCP? (Gravariam cada página bruta: ~800 MB num banco de 0,5 GB.)
2. Por que calcular o desconto em vez de usar o `percentualDesconto` do PNCP? O que muda se só uma parte dos itens foi homologada?
3. Como uma resposta vazia do PNCP poderia apagar resultados bons, e como o código evita isso?
4. Por que a faixa de desconto mostra o número de casos e a linha de inexequibilidade? O que seria enganoso mostrar?
5. Por que o próprio edital fica fora da régua dele, mas dentro das contagens do órgão?
6. Como o pacote `editais` faz a consulta que junta edital e resultado sem violar a independência entre `coleta` e `editais`?
