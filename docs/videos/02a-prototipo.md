# Vídeo 2a: um front feito por IA, preso por um contrato que o CI faz valer

Roteiro em tópicos. Material de apoio: `docs/etapas/02a-prototipo.md`, `frontend/CLAUDE.md` e `frontend/`. Lembrar da decisão D7: o front é feito majoritariamente com IA e não entra como argumento de competência. O assunto do vídeo é o que impede a IA de desmontá-lo.

## Gancho (30 s)

- Pergunta: "Se a IA escreve o front inteiro, quem garante que ele continua sendo o front que você aprovou?"
- Mostrar uma tentativa de deriva sendo barrada: um `<button>` cru numa página e o lint reprovando com "use o componente do Astryx".
- Promessa: no fim, o PR que tenta editar a própria fiscalização e não passa.

## A API em 2 minutos

- Três rotas de leitura (`src/radar/api/`, FastAPI): lista de editais filtrados, detalhe e status.
- O contrato OpenAPI é gerado pela própria API e commitado em `frontend/openapi.json`. Um teste falha se o arquivo divergir do que a API gera.
- Detalhes que mostram critério: datas sem fuso são horário de Brasília, `valor_estimado` sai como texto para não perder precisão, `link_origem` só volta se for http(s).
- O front só conhece a API pelos tipos gerados desse arquivo (`api.d.ts`). `Linha` e `UF` são enums vindos do `config/filtro.toml`.

## O protótipo (2 min)

- Três telas: lista, detalhe e entrar (login só visual), com os estados carregando, vazio, erro e desatualizado.
- Dados de exemplo reais: 40 editais gerados da carga de 30 dias. O protótipo funciona sem backend.
- A camada de dados trocável em `src/dados/fonte.ts`: `VITE_FONTE=api` usa a API, qualquer outro valor usa o exemplo.

## O problema da deriva (2 min)

- Um agente tende a "melhorar" o que não foi pedido: cria um componente parecido com outro, importa uma biblioteca de UI mais familiar, troca o menu, desliga uma regra de lint para o teste passar.
- Cada passo parece razoável. O somatório é um front que ninguém mais controla.
- Por que o `CLAUDE.md` não basta: é um pedido por escrito. Depende de ser lido, lembrado e obedecido, e o contexto da conversa enche. Um texto não reprova nada.
- Ideia central: o texto diz o que fazer, o CI impede o que não pode ser feito. A segunda camada sobrevive ao esquecimento.

## As camadas, uma por uma (6 min)

Em cada uma, mostrar a violação, o erro e a regra que a barra.

1. **Rotas numa lista fechada.** `src/rotas.tsx` gera o menu, e `src/rotas.test.ts` confere que a lista é exatamente `/`, `/edital/:id` e `/entrar`. Criar `/configuracoes` falha no teste.
2. **Lint de fronteiras** (`frontend/eslint.config.js`, `eslint-plugin-boundaries`).
   - HTML cru, `style` e `className` em elemento do DOM, bibliotecas de UI paralelas (`@mui/*`, `antd`, `tailwindcss` e outras), CSS fora de `main.tsx`.
   - Página importando página. O plugin calcula o grafo de imports, então também pega o `../../..` que escapa de `src/` e o ciclo de rotas, coisas que o texto não prevê.
   - Pasta paralela: um `frontend/ui/Botao.tsx` importado de qualquer jeito reprova.
3. **Sem config inline.** `noInlineConfig` e `--max-warnings 0`, mais o `npm run contrato:verificar`, que reprova o próprio texto de uma diretiva de desligar regra. Mostrar o comentário de desligar a regra sendo recusado.
4. **Listas fechadas provadas por teste.** Componentes (`componentes.test.ts`), dependências de runtime (`dependencias.test.ts`) e um teste das próprias regras do lint (`eslint-contrato.test.ts`), com trechos que devem ser barrados e trechos que devem passar. Sem esse último, uma regra poderia ser afrouxada em silêncio.
5. **Duplicação.** jscpd com `threshold: 0`: copiar um componente em vez de reaproveitar derruba o CI.
6. **Tipos que acompanham a API.** O CI roda `npm run contrato && git diff --exit-code src/dados/api.d.ts`.
7. **Screenshots que quebram quando a navegação muda.** Playwright compara o shell em 390 px e 1280 px, e as referências são geradas no Linux do CI, nunca no Windows. Mostrar a diferença quando o cabeçalho muda.

## Quem guarda os guardas (4 min)

- A saída mais curta para a IA é mudar a regra em vez do código. Então os arquivos do contrato (configurações, testes de contrato, Shell, `e2e/`, `scripts/`, `ci.yml`, `CODEOWNERS`, o `CLAUDE.md`) só mudam com um commit que traga o trailer `Contrato-Front: aprovado`.
- O job `contrato-guardado` (`frontend/scripts/contrato-guardado.mjs`) confere o trailer, e o `CODEOWNERS` pede a revisão do dono nos mesmos caminhos.
- A brecha que sobra: no evento `pull_request`, o workflow e o script que rodam são os do próprio PR. Quem edita o PR edita a fiscalização.
- A correção: `.github/workflows/guarda-base.yml`, o job `guarda-base` com `pull_request_target`. A definição do workflow vem da base, o checkout é do SHA da base, e a cabeça do PR só é buscada como dado com `git fetch`. Nada do PR é executado.
- Demo: abrir um PR de teste que altera `contrato-guardado.mjs` sem o trailer e mostrar o job da base reprovando.
- Ser honesto: essa guarda só passou a valer depois do merge que a introduziu, porque o workflow é lido da base. Conferir no GitHub que o job aparece antes de gravar.

## As brechas que as revisões acharam (2 min)

- Contar três: o comentário que desligava o lint, a pasta `frontend/ui/` fora de `src/` que escapava das fronteiras e os tipos escritos à mão que divergiam da API. Cada uma virou teste (commits `3b13867`, `44fb252`, `12a954b`).
- Ponto a fixar: um contrato se mede pelas brechas que já foram achadas e fechadas, não pela lista de regras.

## Perguntas (1 min)

Deixar na tela, uma por vez, e responder no vídeo (as mesmas de `docs/etapas/02a-prototipo.md`):

1. Por que gerar os tipos do front a partir do OpenAPI em vez de escrevê-los à mão?
2. Por que o front usa `HashRouter` e `base: './'`?
3. O que o `eslint-plugin-boundaries` impede que o `CLAUDE.md` não impede?
4. Por que a guarda deve rodar o script da base e não o do próprio PR?
5. Por que o contrato proíbe desligar regra por comentário?

## Fecho

- Entregue: uma API com contrato versionado e um front que a IA edita dentro de limites que o CI faz valer.
- Pendente: revisão pelo Front-End-Checklist (acessibilidade, segurança, performance), ainda sem resultado.
- Próximo: o radar na AWS (etapa 2b).
