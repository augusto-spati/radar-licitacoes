# Etapa 7b: preço unitário de referência

Data: 2026-11-27. Continuação da etapa 7 (caminho B escolhido pelo Augusto: histórico estruturado + preço unitário). Autônomo por pedido dele; decisões sem ele marcadas **[decisão do Claude]** e registradas em `~/carreira/projetos/radar-decisoes-madrugada.md`. Base: `~/carreira/projetos/radar-pesquisa-precos-dados.md`, seções 2.2 e 5.

## Por que

O preço unitário (R$/m de rede, R$/ligação, R$/hidrômetro) é o número que o dono da empresa mais entende e compara com o próprio custo. A régua de desconto da 7a diz "quanto abaixo do estimado os vencedores foram"; o preço unitário diz "quanto custa o metro de rede nos editais parecidos", e com isso ele confere se o estimado do edital que está olhando é realista.

## O que a medição mostrou

- **PNCP**: o preço unitário **contratado** de obra não aparece (0 de 11 obras; o contrato cita a proposta e não a anexa). O **estimado** está na planilha orçamentária do edital em ~metade das obras (≥ 9 de 37 contratações), em PDF com texto (às vezes XLSX), com códigos SINAPI/CPOS/SICRO. Para material, o estruturado do PNCP basta.
- **TCE-PE** (dados abertos, sem login, 0,3–4 s por chamada): `Remessa_ProcessoContratacaoOrcamento` traz o **estimado por item** (descrição, unidade, quantidade, valor unitário, BDI, fonte e código de referência); `Remessa_InstrumentoJuridicoItens` traz o **contratado por item**, com a mesma descrição e quantidade, somando exatamente o homologado (5 de 5 conferidos). Cobre prefeituras de PE e a **Compesa** (310 processos em 2026), que não publica no PNCP. O servidor respondeu 200 de nós nos EUA, Reino Unido e Canadá (check-host.net); da AWS ainda não testado.
- Desconto nem sempre é linear: em parte das obras todos os itens têm a mesma razão contratado ÷ estimado (0,750), em outras varia de 0,51 a 1,00 item a item. Aplicar o desconto global à planilha estimada é aproximação.

## Objetivos

1. **7b.1 TCE-PE (estruturado, sem IA).** Estimado e contratado por item dos processos de saneamento de PE (Compesa e prefeituras) dos últimos 12 meses, e o resultado (vencedor, homologado) dos processos da Compesa.
2. **7b.2 Planilha do edital (IA).** Para contratações homologadas do PNCP nas linhas `redes` e `perdas`, extrair da planilha orçamentária do edital as linhas "descrição | unidade | quantidade | preço unitário estimado" e calcular o "estimado com o desconto do vencedor" como **aproximação**.
3. **Serviços-padrão.** Normalizar itens em poucos serviços comparáveis, por regra no código.
4. **Tela.** Na ficha do edital, "Preços de referência" para as linhas do edital: por serviço-padrão, mediana do estimado e (onde houver) do contratado, com número de casos e a origem.

## Não objetivos

- Preço de material (o PNCP estruturado já tem; fica para depois se pedir).
- AUDESP (TCE-SP, 8+ meses de atraso, 1,2 GB por mês) e SICOM-MG (reCAPTCHA).
- Recomendar preço de proposta.

## Anti-objetivos

- Misturar estimado com contratado no mesmo número.
- Comparar itens de unidades diferentes (m com m³) ou de diâmetros diferentes como se fossem o mesmo serviço.
- Mostrar o "estimado com desconto" sem dizer que é aproximação.

## Restrições

As da 7a (Neon 0,5 GB, cortesia, import-linter, contrato do front, sem métrica nova: 10/10). Teto da IA US$ 2/mês: a 7b.2 entra no mesmo teto e no mesmo cache; se a estimativa passar do teto, para e pergunta ao Augusto.

## Desenho

### Serviços-padrão **[decisão do Claude]**

Lista inicial em `config/servicos.toml`, cada um com nome, unidade canônica, linha e regra de casamento (termos sem acento + unidade aceita + extração de diâmetro quando fizer sentido):

| Serviço | Unidade | Linha | Casamento |
|---|---|---|---|
| Rede de água, tubo PVC/PEAD/FoFo, por DN | m | redes | "rede"/"tubo"/"assentamento" + "agua" + DN extraído (DN 50–600) |
| Rede de esgoto, tubo PVC/manilha, por DN | m | redes | idem com "esgoto" |
| Ligação predial de água | un | redes | "ligacao predial"/"ramal predial" + "agua" |
| Ligação predial de esgoto | un | redes | idem com "esgoto" |
| Escavação mecânica de vala | m³ | redes | "escavacao" + "vala" |
| Recomposição de pavimento asfáltico | m² | redes | "recomposicao"/"reposicao" + "pavimento"/"asfalt" |
| Hidrômetro, fornecimento e instalação | un | perdas | "hidrometro" + ("fornecimento"/"instalacao"/"substituicao") |
| Pesquisa de vazamento não visível | km | perdas | "pesquisa"/"deteccao" + "vazamento" |

Item que não casa com nenhum fica guardado sem serviço (para revisar a lista depois). Unidades convertidas (km → m quando a canônica é m, etc.).

### 7b.1 TCE-PE

- Comando `radar tcepe atualizar` (task do Fargate, agenda semanal aos domingos 04:00): `Remessa_ProcessoContratacao` dos últimos 12 meses das UJs de interesse (Compesa e prefeituras de PE), filtrado em memória pelo objeto com as mesmas regras do pré-filtro da 7a; para cada processo homologado de saneamento: `Remessa_ProcessoContratacaoOrcamento` (estimado) e, via `Remessa_InstrumentoJuridico` + `Remessa_InstrumentoJuridicoItens`, o contratado; casar contrato ↔ processo conferindo o valor (o filtro por número não é exato).
- Grava em `preco_item` (tabela nova): origem `tce_pe`, processo (UJ, número, ano), órgão (nome, CNPJ quando houver), UF `PE`, município, data, item (descrição, unidade, quantidade, BDI, fonte/código de referência), **preço estimado**, **preço contratado** (quando o contrato casa), serviço-padrão e unidade canônica, quantidade e preço convertidos. Razão contratado ÷ estimado fora de [0,3; 1,5] → item marcado `suspeito` e fora das medianas (o caso de 89×).
- Encoding: `Remessa_*` vem com U+FFFD no lugar de acento; a normalização remove acento e trata `�` como curinga de uma letra no casamento de termos.
- Parâmetro inválido devolve 100 mil registros em silêncio: o cliente confere que a resposta respeita o filtro (ex.: todo registro com a UJ pedida) e falha se não.
- Compesa: o resultado (vencedor, homologado, desconto) dos processos dela também vai para a 7a, ligado ao edital `compesa~<lc_chave>` quando o número da licitação casar; o que não casar fica só em `preco_item`. **[decisão do Claude]**

### 7b.2 Planilha do edital (IA)

- Alvos: contratações do PNCP `homologada` (7a) nas linhas `redes`/`perdas`, sem `preco_item` de origem `planilha_edital` ainda, até N por rodada (config, padrão 10).
- Documento: entre os arquivos do edital (mesma API de arquivos da etapa 6, abrindo ZIP), o que tem nome de planilha ("planilha", "orcament", "custo", "estimativa") ou, sem nome, o de mais linhas de preço no texto; XLSX lido direto (openpyxl já é dependência? se não, só PDF/DOCX na v1).
- Extração: Nova Lite, saída estruturada `linhas: [{descricao, unidade, quantidade, preco_unitario, codigo_referencia, trecho}]`, em trechos como na etapa 6, com ancoragem (o trecho e o preço precisam existir no texto); linhas quebradas em duas são o caso esperado.
- Grava em `preco_item` com origem `planilha_edital`, preço estimado, e o **estimado com desconto** = estimado × (1 − desconto global da contratação), marcado como aproximação.
- Evals: 5 a 8 planilhas com gabarito rascunho (`conferido: false`, só o Augusto confere), precisão/recall por linha (descrição casada + preço com tolerância de 1%).

### API e tela

- `GET /api/precos/{id_publico:path}`: para as linhas do edital, por serviço-padrão: casos e mediana do estimado (todas as origens), mediana do contratado (TCE-PE), mediana do "estimado com desconto" (planilha, aproximado), na UF do edital com fallback nacional quando < 5 casos.
- Seção "Preços de referência" abaixo do histórico, no mesmo padrão (carga à parte, vazio com frase própria). Texto: "Preço contratado: só Pernambuco (TCE-PE). 'Estimado com desconto' é aproximação: aplica o desconto global do vencedor à planilha do órgão."

## Ordem

7b.1 (TCE-PE: cliente, coleta, `preco_item`, serviços-padrão) → teste da AWS → API e tela com o que houver → 7b.2 (planilha por IA, evals) → docs.

## Respostas à revisão do construtor **[decisões do Claude]**

1. **TCE-PE e a AWS: testar antes de construir.** A primeira tarefa é uma sonda (`radar tcepe sondar`, uma chamada pequena a `Remessa_ProcessoContratacao` filtrada pela UJ da Compesa) rodada numa task avulsa do Fargate. Se a AWS for recusada, a 7b.1 fica fora (registrado, sem contornar) e o plano segue só com a 7b.2.
2. **BDI: compara-se sempre o preço COM BDI** (o que o órgão paga). `preco_item` guarda `preco_sem_bdi`, `bdi` e `preco_com_bdi`; quando a fonte traz só o sem BDI e o percentual, `com = sem × (1 + bdi)`; quando traz só o unitário e não diz, assume-se com BDI e marca `bdi_desconhecido` (fora das medianas se o serviço tiver casos suficientes sem essa marca). A sonda confere no TCE-PE se `ValorUnitario × Quantidade ≈ ValorTotal` e se a soma bate com o `valorGlobal` (com ou sem BDI).
3. **Chave do serviço de rede = (serviço, material, DN, com fornecimento?).** PVC, PEAD e FoFo separados; "assentamento" (sem fornecimento do tubo) separado de "fornecimento e assentamento". Item de rede sem DN ou material reconhecível fica sem serviço.
4. **Tela:** serviços sem DN agregados; para rede, só os (material, DN, fornecimento) com 3+ casos na UF ou no fallback nacional, ordenados por casos. Sem extrair a planilha do edital aberto na v1.
5. **Filtro do TCE-PE:** processos pelo pré-filtro amplo da 7a (≈ 85 por ano em PE), mas **só os itens que casam com algum serviço-padrão** vão para `preco_item` (o processo de 1.412 itens não entra inteiro). O número de itens gravados entra no log da rodada.
6. **Compesa no histórico da 7a: fora desta etapa.** O histórico do órgão olha só editais `pncp~`; ligar os processos da Compesa a ele pede mudança no recorte da 7a. Fica registrado como próxima etapa. A 7b.1 guarda os itens da Compesa em `preco_item` normalmente.

## O que fica com o construtor

Nomes de colunas, formato da resposta, paginação, regras finas de casamento dos serviços (com testes de exemplo reais do TCE-PE e das planilhas da pesquisa), layout com componentes já usados.
