# Front do Radar de Licitações

React + Vite + Astryx 0.7.17 (versão exata em core, tema e cli). Texto da interface em pt-BR.

## Contrato (não negociável)

Antes de criar um componente: `astryx search`, depois `src/componentes/`; componente novo só com pedido explícito. A lista de componentes (e de pastas de `src/`) é fechada em `src/componentes.test.ts`.

- **Rotas (lista fechada da v1)**, todas em `src/rotas.tsx`, que também gera o menu:
  - `/`: painel (primeiro no menu)
  - `/editais`: lista de editais (no menu)
  - `/edital/:id`: detalhe
  - `/entrar`: login por código no e-mail (Cognito, `src/dados/sessao.ts`); com a fonte de exemplo é só visual
  - `*`: 404 ("Página não encontrada", com link para a lista (`/editais`)); captura o resto, fica por último e fora do menu

  Rota nova, página nova ou item de menu novo só com pedido explícito. O roteador é `HashRouter` e o build usa `base: './'`.
- **Shell fixo**: `src/componentes/Shell.tsx` (template `shell-top-nav`). Não mude a moldura, o cabeçalho nem a navegação; o menu sai de `rotas`.
- **Fronteiras** (`npm run lint`, eslint-plugin-boundaries): só `src/rotas.tsx` importa páginas (além do teste da própria página); páginas não importam páginas; componentes não importam páginas; ninguém importa arquivo `*.test.*`.
- Só o Shell e o App leem `rotas`; página não importa `src/rotas.tsx`.
- **Sem HTML cru** (`react/forbid-elements`): nada de `button`, `input`, `select`, `textarea`, `form`, `label`, `table`, `a`, `img`, `div`, `span`, `section`, `p`, `ul`, `li`, `h1`–`h6` fora de `src/componentes/base/`. Também nada de `style` ou `className` em elemento do DOM (`react/forbid-dom-props`).
- **Sem outra biblioteca de UI nem CSS** (`no-restricted-imports`): MUI, antd, Chakra, Bootstrap, Tailwind, styled-components, Emotion, Mantine, Radix, Headless UI, PrimeReact, Semantic UI, Fluent UI, daisyUI, NextUI/HeroUI e shadcn são proibidos. CSS/SCSS/Sass/Less (inclusive `?inline`) só em `src/main.tsx`. Nada de cor ou medida fixa: props, `xstyle` e tokens.
- **Dependências de runtime**: só as de `src/dependencias.test.ts`. Dependência nova só com pedido explícito, editando essa lista.
- **Nada de fora de `src/`** (`boundaries/no-unknown-dependencies`): código de `src/` só importa arquivos de `src/` (e pacotes); uma pasta paralela como `frontend/ui/` reprova, com qualquer número de `../`. Import absoluto (`/ui/X`) também. As regras de HTML cru, `style`/`className`, bibliotecas e CSS valem em todo o front, não só em `src/`.
- **Sem duplicação** (`npm run duplicacao`: jscpd com threshold 0 e minTokens 50 no front inteiro, menos `node_modules`, `dist`, relatórios, screenshots, `api.d.ts` e JSON): reaproveite em vez de copiar.
- **Regra não se desliga por comentário**: o ESLint roda com `noInlineConfig` e `--max-warnings 0`, e `npm run contrato:verificar` (`scripts/verificar-contrato.mjs`) reprova qualquer diretiva de desligar regra do ESLint, config inline ou marcador de ignorar do jscpd em qualquer arquivo de código do front.
- **Tipos da API**: `src/dados/api.d.ts` é gerado (`npm run contrato`) do `openapi.json`; o CI reprova se divergir. `Linha` e `UF` vêm do `config/filtro.toml` por esse caminho: `LINHAS` e `UFS` em `src/dados/formatos.ts` não compilam se ficarem diferentes.
- Nunca afrouxe uma regra do `eslint.config.js` ou do `.jscpd.json` para algo passar. `src/eslint-contrato.test.ts` prova que as regras pegam a deriva.

### Arquivos de guarda e o trailer `Contrato-Front: aprovado`

Estes arquivos fazem o contrato valer e só mudam com aprovação explícita do Augusto: `eslint.config.*`, `.jscpd.json`, `package.json`, `package-lock.json`, `index.html`, `vite.config.*`, `vitest.config.*`, `tsconfig*.json`, `playwright.config.ts`, `.gitignore` (da raiz e do front), este `CLAUDE.md`, `src/rotas.test.ts`, `src/dependencias.test.ts`, `src/componentes.test.ts`, `src/eslint-contrato.test.ts`, `src/componentes/Shell.tsx`, `e2e/` (inclusive os screenshots), `scripts/`, `.github/workflows/ci.yml`, `.github/workflows/guarda-base.yml` e `.github/CODEOWNERS` (lista em `scripts/contrato-guardado.mjs`).

A guarda é só o job `guarda-base` (`.github/workflows/guarda-base.yml`) e reprova o PR em que algum deles mudou sem que pelo menos um commit do PR tenha, no bloco final da mensagem, o trailer:

```
Contrato-Front: aprovado
```

O job roda por `pull_request_target`, ou seja, a definição do workflow vem da branch base e um PR não consegue editá-la. Ele faz checkout da base, busca `refs/pull/N/head` só como dados e roda o `scripts/contrato-guardado.mjs` da base com git; não executa código do PR. A lista de arquivos guardados também é a da base.

Por que só ele: o job antigo `contrato-guardado` do `ci.yml` rodava a definição da cabeça do PR, então um PR podia neutralizá-lo editando o próprio `ci.yml`; por isso foi removido. A guarda pressupõe o fluxo por PR, obrigatório na `main` (PR exigido, checks qualidade/front/guarda-base/e2e, `enforce_admins`); push direto à `main` não passa por ela e a esvazia.

Agente nenhum põe esse trailer por conta própria: só quando o Augusto pediu a mudança no contrato. Se a mudança não foi pedida, desfaça-a.

Antes de commitar, dentro de `frontend/`: `npm test`, `npm run lint`, `npm run duplicacao`, `npm run contrato:verificar`, `npm run build` e `npx tsc --noEmit`.

## Astryx (bloco gerenciado pelo `npx astryx init`; não edite à mão)

<!-- ASTRYX:START -->
Astryx v0.7.17 · 174 components
CLI: run every command as `npx astryx <cmd>` (shown below as `astryx ...`).

SETUP (once, in app entry e.g. main.tsx) — without these, components render unstyled:
  import "@astryxdesign/core/reset.css";
  import "@astryxdesign/core/astryx.css";

WORKFLOW — start every page from a template, never from scratch:
1. `astryx build "<idea>"` — names the template to scaffold from and the parts it lacks.
2. `astryx template <name> <path>` — scaffold it; keep its frame, gap and padding; replace the content.
3. `astryx component <Name>` to read props before using one; `astryx template <Block>` prints a block example to read for a part it lacks. Astryx names differ from other libraries: buttons, badges, tokens and tabs take their text in `label` (Text, Heading and Link take children), booleans start with `is` or `has` (`isOpen`, `isDisabled`), and every input takes `value` and `onChange`.
Changing an existing page? Keep it: skip step 2 and add components inside its sections; read a block example when it helps.
Before you deliver a build, run `astryx doctor --built <dir>`; don't deliver it if it fails.

RULES:
- No <div> — components handle all layout and spacing.
- Read `astryx docs layout` before changing a template's frame.
- Dense data = rows (Table, List/Item), not Card-wrapped lists. Badge = counts only.
- Style with component props first, then the xstyle prop / StyleX tokens. No raw hex/px.
- THEMES: palettes and custom colors go through the theme, never :root overrides; so does a denser or roomier app (`editor set <name> spacing tight`), never gap and padding changes page by page. A change to one kind of part (the sidebar, its selected item, the gaps in a form) is a theme change too, not props on one page: `editor how "<ask>"` names the `set … --on <part>` line. No app yet: `astryx experimental theme preview app <dir>` makes a starter app that shows the theme. To make or change a theme (colors, fonts, corners, spacing, a look from a picture), prefer the experimental theme editor over hand-written theme files: `astryx experimental theme editor new <name> --like neutral`, then `editor set <name> <thing> <value>` or `editor how "<what you want>"`; wire it with `astryx theme add <name>`; after each change look at it with `astryx experimental theme editor show <name> --png` (takes pictures of real components) and open the PNGs it writes; a theme change is not done until you have looked at the pictures. In code, extend an imported theme to customize it; `theme eject <slug>` only forks source. See `astryx docs theme`.
- SELF-CHECK: re-read the file; replace any className=, style={{…}}, raw <div>/<span>, imported .css/@apply, or hardcoded #hex/px with the component or the xstyle prop + a token.

`astryx help` lists every command. Key ones beyond the workflow:
  search "<query>"   find any component / hook / doc / template
  discover <words>   integrations you could add
  docs <topic>       getting-started, principles, tokens, theme … (`astryx docs` lists all)
  docs cli           commands and API reference
  docs authoring     build an integration package, and every field it holds
  upgrade --from <old version> --apply   run after a dependency bump
To remove this block, run `astryx init --remove-agents`, which removes it from the agent files at the project root and in the folder you run it in; from any other file it leaves, delete everything from ASTRYX:START to ASTRYX:END.
<!-- ASTRYX:END -->
