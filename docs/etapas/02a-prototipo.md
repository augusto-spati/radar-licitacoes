# Etapa 2a: API local e protótipo do front

Primeira metade da etapa 2 (a segunda, 2b, leva o radar para a AWS). Fechada em 2026-11-26, com uma pendência (ver "O que ficou pendente").

Pela decisão D7, o front fica fora da trilha de estudo: foi feito majoritariamente com IA e não conta como argumento de competência. O que você precisa saber defender nesta etapa é o **contrato da API** e os **mecanismos que impedem a IA de desmontar o front**. É nisso que esta nota se concentra.

## O que foi feito

1. **API JSON só de leitura** (`src/radar/api/`, FastAPI). Três rotas: lista de editais filtrados, detalhe de um edital e status (quando foi a última atualização). Usa só o que o módulo `editais` expõe (`filtrar`, `EditalResumo`, `EditalDetalhe`). Sobe com `radar api`.
2. **Contrato OpenAPI versionado.** `frontend/openapi.json` é gerado pela própria API (`src/radar/api/contrato.py`) e commitado. Um teste (`tests/unit/test_openapi_atualizado.py`) falha se o arquivo ficar diferente do que a API gera.
3. **Dados de exemplo reais.** `frontend/src/dados/exemplo.json` foi gerado a partir da carga de 30 dias (`src/radar/api/exemplo.py`): 40 editais, 35 com linhas de serviço. O protótipo funciona sem backend.
4. **Front em React, Vite e Astryx** (`frontend/`). Três telas: lista, detalhe e entrar (login só visual), com os estados de carregando, vazio, erro e desatualizado. Uma camada de dados trocável (`src/dados/fonte.ts`): `VITE_FONTE=api` usa a API, qualquer outro valor usa o exemplo.
5. **O contrato do front**, em camadas (seção seguinte).
6. **Testes:** unitários e de página (Vitest), e Playwright com screenshot do shell em 390 px e 1280 px, teste da gaveta de navegação no celular e teste de que o botão Voltar mantém o filtro.
7. **CI:** jobs `qualidade`, `front`, `contrato-guardado` e `e2e` (`.github/workflows/ci.yml`).

O contrato da API é o que importa defender: o front só conhece a API pelos tipos gerados do `openapi.json`. Datas sem fuso são horário de Brasília; valores monetários saem como texto (`valor_estimado` é `str`) para não perder precisão; `link_origem` só é devolvido se for http(s) (`link_http` em `src/radar/api/modelos.py`); `Linha` e `UF` são enums vindos do `config/filtro.toml`.

## Como o contrato do front funciona

### O problema

Um agente de IA que mexe no front tende a "melhorar" o que não foi pedido: cria um componente parecido com outro que já existe, importa uma biblioteca de UI mais familiar, troca o menu, desliga uma regra incômoda do lint para o teste passar. Cada passo parece razoável; o somatório é um front que ninguém mais controla.

### Por que um arquivo de instruções não basta

O `frontend/CLAUDE.md` descreve o contrato, mas é só um pedido por escrito. Ele depende de o agente lê-lo, lembrar dele e obedecer, e nenhuma dessas três coisas é garantida: o contexto da conversa enche, a instrução se perde, um agente decide que "dessa vez é justificável". Um texto não reprova nada.

Cada mecanismo abaixo é uma **máquina que falha de forma determinística**, não importa quem escreveu o código nem o que ele "entendeu". O texto diz o que fazer; o CI impede o que não pode ser feito. As duas coisas são complementares, e a segunda é a que sobrevive ao esquecimento.

### As camadas

**1. Instruções e skill do Astryx** (`frontend/CLAUDE.md` e o bloco gerenciado pelo `astryx init`). Dizem como trabalhar: partir de um template, procurar o componente antes de criar. É a camada fraca, e as demais existem porque ela é fraca.

**2. Rotas num arquivo só.** `src/rotas.tsx` lista as rotas da v1 e também gera o menu. `src/rotas.test.ts` confere que a lista é exatamente `['/', '/edital/:id', '/entrar']` e que só `/` aparece no menu. Violação barrada: adicionar uma página `/configuracoes` falha no teste, porque a lista fechada mudou.

**3. ESLint com fronteiras** (`frontend/eslint.config.js`, `eslint-plugin-boundaries`). Exemplos reais de violações que o lint reprova:
- **HTML cru.** `react/forbid-elements` proíbe `button`, `input`, `table`, `div`, `a`, `p` e outros fora de `src/componentes/base/`. A mensagem diz: "use o componente do Astryx".
- **Estilo solto.** `react/forbid-dom-props` proíbe `style` e `className` em elemento do DOM, o que barra cor e medida fixas.
- **Biblioteca de UI paralela.** `no-restricted-imports` barra `@mui/*`, `antd`, `@chakra-ui/*`, `tailwindcss`, `styled-components`, `@radix-ui/*`, `shadcn` e outras.
- **CSS espalhado.** Importar `.css`, `.scss` ou `.less` (inclusive `?inline`) só é permitido em `src/main.tsx`.
- **Página importando página.** `boundaries/dependencies` com `checkInternals`: só `src/rotas.tsx` importa páginas. Componente importar página também reprova, e página importar `src/rotas.tsx` (ciclo) também.
- **Código de produção importando arquivo de teste ou o apoio de teste** (`src/testes/`), o que levaria `@testing-library` para o bundle.
- **Pasta paralela.** `boundaries/no-unknown-dependencies`: código de `src/` só importa arquivos conhecidos de `src/`. Um `frontend/ui/Botao.tsx` importado com qualquer número de `../` reprova, e o import absoluto (`/ui/Botao`) também.

**4. O contrato não se desliga por comentário.** O ESLint roda com `noInlineConfig` (comentário de config não tem efeito) e `--max-warnings 0`. E `npm run contrato:verificar` (`scripts/verificar-contrato.mjs`) reprova o próprio texto de uma diretiva de desligar regra, de config inline do ESLint e do marcador de ignorar do jscpd, em qualquer arquivo de código do front. Sem isso, bastava um comentário de desligar a regra acima da linha infratora.

**5. Listas fechadas, provadas por teste.**
- `src/componentes.test.ts`: a lista de componentes próprios e de pastas de `src/`. Componente novo exige editar esse teste, que é arquivo guardado.
- `src/dependencias.test.ts`: as dependências de runtime permitidas (Astryx, StyleX, lucide-react, React, React Router). Dependência nova sem editar a lista reprova.
- `src/eslint-contrato.test.ts`: testa as próprias regras do ESLint, com trechos de código que devem ser barrados (`<button>` em página, `<div>` em componente, `@mui/material`, CSS fora do `main.tsx`, página importando página) e trechos que devem passar (`<div>` dentro de `componentes/base`, `className` como prop de componente do Astryx). Sem esse teste, uma regra poderia ser afrouxada em silêncio.

**6. Duplicação.** `npm run duplicacao` roda o jscpd com `threshold: 0` e `minTokens: 50` no front inteiro. Se a IA copia um componente em vez de reaproveitar, o CI falha.

**7. Tipos que acompanham a API.** `src/dados/api.d.ts` é gerado do `openapi.json` (`npm run contrato`), e o CI roda `npm run contrato && git diff --exit-code src/dados/api.d.ts`: se o arquivo commitado não for o gerado, reprova. `Linha` e `UF` vêm desse arquivo, e `LINHAS` e `UFS` em `src/dados/formatos.ts` não compilam se forem diferentes dos enums da API. Assim, o front não consegue divergir da API sem o compilador ou o CI avisarem.

**8. Playwright.** O screenshot do shell (390 px e 1280 px) é comparado com a referência aprovada, e as referências são geradas no Linux do CI, nunca no Windows. Um teste confere que nenhum elemento passa da largura da tela. Se a IA mudar o cabeçalho ou a navegação, a imagem muda e o teste falha.

**9. Guarda dos arquivos do contrato.** Todos os arquivos acima (as configurações, os testes de contrato, o Shell, o `e2e/`, os `scripts/`, o `ci.yml`, o `CODEOWNERS` e o próprio `CLAUDE.md`) só podem mudar com um commit no intervalo que traga o trailer `Contrato-Front: aprovado`. O job `contrato-guardado` (`frontend/scripts/contrato-guardado.mjs`) confere isso, e o `.github/CODEOWNERS` pede a sua revisão nos mesmos caminhos. Sem essa camada, a saída mais curta para a IA seria alterar a regra em vez do código.

### O que o `eslint-plugin-boundaries` impede que um texto de instruções não impede

O texto pede "página não importa página". O plugin **calcula** o grafo de imports de cada arquivo e reprova o CI se houver uma aresta proibida. A diferença é de natureza: o texto depende de quem o lê; o plugin não depende de ninguém. Ele também vê o que o texto não prevê, como um import com `../../..` que escapa de `src/` ou um ciclo de rotas.

## As brechas que as revisões acharam

Cada uma foi uma forma de o contrato ser contornado ou de o front divergir da API, e cada uma virou teste:

| Brecha | Como foi fechada | Commit |
|---|---|---|
| Teste e regras só olhavam parte do front; faltava barrar deriva em páginas, CSS, bibliotecas, DOM e rotas | Testes de contrato mais fortes | `a66247a` |
| Botão de link do edital abria um endereço vazio ou inválido | A API só devolve `link_origem` se for http(s); o front cai no PNCP quando vem nulo | `12a954b`, `300706d` |
| `Linha` e `UF` escritos à mão no front podiam divergir da API | Viraram enums do OpenAPI; `LINHAS` e `UFS` deixam de compilar se divergirem | `12a954b`, `300706d` |
| Comentário `eslint-disable` desligava o contrato | `noInlineConfig` e `contrato:verificar` | `3b13867` |
| Código fora de `src/` (uma pasta `frontend/ui/`) escapava das regras de fronteira | `boundaries/no-unknown-dependencies` e regras de UI valendo no front todo | `3b13867` |
| Tipos do front podiam divergir da API | `api.d.ts` gerado do `openapi.json` e conferido no CI | `44fb252`, `3ae340c` |
| Arquivos de guarda editáveis sem aprovação | Job `contrato-guardado` com o trailer e `CODEOWNERS` | `3ae340c` |

## O que ficou pendente

**A guarda por `pull_request_target` só vale depois do merge.** O script `contrato-guardado.mjs` já aceita o evento `pull_request_target`, mas o `ci.yml` que está em `main` ainda roda o job com `pull_request`. Isso tem uma consequência: no evento `pull_request`, o workflow e o script que rodam são os **do próprio PR**. Um PR mal-intencionado (ou um agente apressado) pode editar o script para passar. A versão com `pull_request_target` roda o workflow e o script a partir da **base** (o que já foi aprovado e está em `main`), então o PR não consegue reescrever a própria fiscalização. Como o workflow de `pull_request_target` é lido da base, a mudança não tem efeito sobre o PR que a introduz: ela só passa a valer depois do merge. Até lá, a guarda existe, mas a proteção contra quem edita o próprio script depende de você revisar os arquivos guardados no PR (ajudado pelo `CODEOWNERS`).

Outros pontos em aberto:
- **Publicar o protótipo como link** (Task 7 do plano): a publicação como artefato e a conferência no celular ficam para o momento da revisão com você. Não foi testado se um build do Vite abre como artefato; se não abrir, vale o plano B do desenho (S3 e CloudFront no começo da 2b).
- **Revisão pelo Front-End-Checklist** (acessibilidade, segurança, performance): a ser registrada aqui quando feita. Este documento não afirma nenhum resultado dela.
- **Login e a API na nuvem** são da 2b.

## Perguntas de entrevista

**1. Por que gerar os tipos do front a partir do OpenAPI, em vez de escrevê-los à mão?**

Resposta:

**2. Por que o front usa `HashRouter` e `base: './'`?**

Resposta:

**3. O que o `eslint-plugin-boundaries` impede que um texto de instruções, como o `CLAUDE.md`, não impede?**

Resposta:

**4. Por que a guarda dos arquivos do contrato deve rodar o script da base (via `pull_request_target`) e não o do próprio PR?**

Resposta:

**5. Por que o contrato proíbe desligar regra por comentário, e que brecha isso fecha?**

Resposta:
