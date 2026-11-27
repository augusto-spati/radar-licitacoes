# Etapa 7b.1: preço unitário de referência pelo TCE-PE

O preço unitário (R$/m de rede, R$/ligação, R$/m³ de escavação) é o número que o dono da empresa mais entende e compara com o próprio custo. Esta etapa traz, dos dados abertos do Tribunal de Contas de Pernambuco, o preço **estimado** pelo órgão e o preço **contratado**, item a item, e mostra na ficha do edital os "Preços de referência" das linhas de serviço dele. Desenho: `docs/superpowers/specs/2026-11-27-etapa-07b-precos-design.md`; plano: `docs/superpowers/plans/2026-11-27-etapa-07b1-tcepe.md`.

## Por que o TCE-PE

- No PNCP o preço unitário **contratado** de obra não aparece: em 0 de 11 obras da amostra o contrato traz a planilha do vencedor (cita a proposta e não a anexa). O **estimado** está na planilha do edital em cerca de metade das obras, em PDF (fica para a 7b.2, com IA).
- O TCE-PE tem as duas coisas **estruturadas**: `Remessa_ProcessoContratacaoOrcamento` (estimado por item, com BDI, fonte e código de referência e data-base) e `Remessa_InstrumentoJuridicoItens` (contratado por item, mesma descrição e quantidade). A soma dos itens do contrato bate com o homologado. Cobre prefeituras de PE e a **Compesa**, que não publica no PNCP.
- A pesquisa dizia que o servidor podia recusar IP estrangeiro. Antes de construir, a sonda `radar tcepe sondar` rodou numa task do Fargate em Ohio: HTTP 200 em 2,5 s.

## O que foi feito

- **Cliente** (`src/radar/coleta/tcepe.py`): ISO-8859-1, pausa de cortesia, tentativas, e duas defesas contra armadilhas da API: parâmetro inválido devolve 100 mil registros em silêncio (o cliente confere que cada registro respeita o filtro) e o número do processo é casado como **substring** ("44" traz "144"; o cliente descarta o que não bate exato).
- **Serviços-padrão** (`config/servicos.toml`, `src/radar/precos/servicos.py`): oito serviços comparáveis. Rede de água e de esgoto por **material, DN e escopo** (só o tubo, só o assentamento, ou fornecimento e assentamento); escavação mecânica de vala por **faixa de profundidade** (até 1,5 m, 1,5 a 3 m, 3 a 6 m); ligações, hidrômetro, recomposição asfáltica e pesquisa de vazamento. A classificação é conservadora: na dúvida o item **não** é gravado (transporte, remanejamento, conserto de ramal, escavação com reaterro, linha que cita água e esgoto juntos ficam de fora). Unidades convertidas para a canônica.
- **BDI**: no TCE-PE o `ValorUnitario` **já inclui o BDI** (em Pedra a soma dos itens dá exatamente o valor global); o campo `BDI` vem como fração na Compesa (0,2467) e como percentual em Pedra (20,97) e é guardado só como informação, em fração.
- **Contrato ↔ processo**: o contrato não aponta para o processo; é escolhido pelo valor (±2% do homologado, pela soma dos itens ou pelo total) e cada item casa por descrição normalizada, quantidade e unidade. Grupo repetido com preços diferentes (o mesmo serviço em dois lotes de vencedores diferentes) fica **sem** preço contratado: preço errado é pior que nenhum. Razão contratado ÷ estimado fora de [0,3; 1,5] marca o item como suspeito.
- **Coleta** (`radar tcepe atualizar`, domingo às 04:00): processos do ano corrente e do anterior com termos próprios de saneamento (os da 7a pegavam 14 processos em 2026; os do TCE-PE pegam 170, 120 homologados), commit por processo, disjuntor após 5 falhas seguidas.
- **Referências** (`GET /api/precos/{id}`): por serviço das linhas do edital, mediana do estimado e do contratado (com o estimado dos mesmos itens ao lado), número de processos e de itens e período da data-base. Caso é processo: a mediana é a das medianas por processo, e os cortes contam processos (UF do edital com 5+, rede com 3+, "poucos casos" abaixo de 3). Com menos de 5 processos na UF do edital, usa as UFs que têm dados e diz quais (`ufs`; hoje só PE, a base é o TCE-PE), nunca "Brasil".
- **Tela**: seção "Preços de referência" na ficha do edital.

## Números da verificação real (9 processos homologados de PE)

- 1.797 itens de orçamento; 79 classificados: escavação 34 (29 até 1,5 m), rede de água 29, rede de esgoto 14, ligação de esgoto 2.
- 16 itens com preço contratado; Compesa (manutenção de esgoto GNR Sertão) 73/73, Pedra (rede de esgoto) 20/20 e Camocim (sistema de água) 79/79 itens do contrato casados; o contrato de Palmares foi recusado pelo valor (R$ 372,8 mil contra R$ 10,3 mi homologados).
- 0 suspeitos.

## O que fica para depois

- **7b.2**: preço estimado das planilhas dos editais do PNCP (fora de PE), com IA.
- Ligar os processos da Compesa ao "Histórico do órgão" da 7a.
- Rever as listas de termos com mais dados reais (a primeira rodada semanal).

## Perguntas de entrevista

1. Por que testar o TCE-PE a partir da AWS antes de escrever a coleta?
2. Como você sabe se o preço unitário já inclui o BDI, e o que acontece se errar?
3. Por que um preço contratado errado é pior que nenhum, e como o código evita atribuir o preço do lote errado?
4. Por que a escavação precisa da faixa de profundidade na chave, e a rede precisa do escopo?
5. Que armadilhas da API do TCE-PE viraram teste (filtro ignorado, número por substring, encoding)?
