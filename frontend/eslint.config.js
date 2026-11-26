// Contrato contra a deriva: estas regras fazem `npm run lint` falhar quando
// alguém (gente ou agente) cria biblioteca de UI paralela, HTML solto ou
// muda a estrutura de páginas. Não afrouxe uma regra para o lint passar.
import js from '@eslint/js';
import boundaries from 'eslint-plugin-boundaries';
import react from 'eslint-plugin-react';
import globals from 'globals';
import tseslint from 'typescript-eslint';

// Bibliotecas de UI/estilo paralelas ao Astryx (o pacote e qualquer subcaminho).
const bibliotecasProibidas = {
  regex:
    '^(?:@(?:mui|chakra-ui|emotion|mantine|radix-ui|headlessui|fluentui|nextui-org|heroui|shadcn)/' +
    '|(?:antd|react-bootstrap|bootstrap|tailwindcss|styled-components|primereact|semantic-ui-react|daisyui)(?:/|$))',
  message: 'A UI é só Astryx. Procure o componente com `npx astryx search`.',
};

// Folhas de estilo, inclusive com sufixo de consulta do Vite (?inline, ?url...).
const cssProibido = {
  regex: String.raw`\.(?:css|scss|sass|less)(?:\?.*)?$`,
  message: 'CSS só entra em src/main.tsx. Estilo vem de props, xstyle e tokens do Astryx.',
};

// Import absoluto ("/ui/X"): o Vite resolve pela raiz do projeto, por fora de src/.
const importAbsoluto = {
  regex: '^/',
  message: 'Import absoluto não: use caminho relativo dentro de src/.',
};

const elementosProibidos = [
  'button',
  'input',
  'select',
  'textarea',
  'form',
  'label',
  'table',
  'a',
  'img',
  'div',
  'span',
  'section',
  'p',
  'ul',
  'li',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
].map(element => ({
  element,
  message: `<${element}> solto é proibido aqui: use o componente do Astryx (ou um de src/componentes/base/).`,
}));

export default tseslint.config(
  {ignores: ['dist', 'node_modules', 'playwright-report', 'test-results']},
  {
    // Comentário no código não desliga regra: nada de diretiva de desligar nem
    // de config inline. Diretiva que sobrar vira erro (e o verificar-contrato
    // reprova o texto dela em qualquer arquivo do front).
    linterOptions: {noInlineConfig: true, reportUnusedDisableDirectives: 'error'},
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx,js,jsx,mjs,cjs}'],
    languageOptions: {globals: {...globals.browser, ...globals.node}},
  },
  {
    // As regras de UI valem em todo o front, não só em src/: um frontend/ui/X.tsx
    // também não pode ter HTML cru, style/className, outra biblioteca nem CSS.
    files: ['**/*.{ts,tsx,js,jsx,mjs,cjs}'],
    plugins: {react},
    rules: {
      'no-restricted-imports': ['error', {patterns: [bibliotecasProibidas, cssProibido, importAbsoluto]}],
      'react/forbid-elements': ['error', {forbid: elementosProibidos}],
      // Sem cor ou medida fixa no DOM. Props de componente do Astryx não são afetadas.
      'react/forbid-dom-props': ['error', {forbid: ['style', 'className']}],
    },
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: {boundaries},
    settings: {
      'import/resolver': {node: {extensions: ['.ts', '.tsx', '.d.ts', '.js', '.jsx']}},
      // Elementos são pastas (boundaries v7). O rotas.tsx é arquivo, então vira
      // categoria de arquivo; 'src' marca todo o resto de src/ como conhecido,
      // para que nenhum arquivo escape da checagem (ex.: App.tsx importando página).
      'boundaries/elements': [
        {type: 'paginas', pattern: 'src/paginas', partialMatch: false},
        {type: 'componentes-base', pattern: 'src/componentes/base', partialMatch: false},
        {type: 'componentes', pattern: 'src/componentes', partialMatch: false},
        {type: 'dados', pattern: 'src/dados', partialMatch: false},
      ],
      'boundaries/elements-single-match': true,
      'boundaries/files': [
        {category: 'rotas', pattern: 'src/rotas.tsx'},
        {category: 'teste', pattern: 'src/**/*.test.{ts,tsx}'},
        // Apoio dos testes (fonte falsa, render com @testing-library): só teste importa.
        {category: 'apoio-teste', pattern: 'src/testes/**'},
        {category: 'src', pattern: 'src/**'},
        // O teste de dependências lê o package.json; é o único arquivo fora de src/ conhecido.
        {category: 'pacote', pattern: 'package.json'},
      ],
    },
    rules: {
      // Páginas só entram pelo src/rotas.tsx: nenhum outro arquivo importa uma página.
      'boundaries/dependencies': [
        'error',
        {
          default: 'allow',
          // Sem isto, página importando página (mesmo elemento) passaria batido.
          checkInternals: true,
          policies: [
            {
              disallow: {to: {element: {type: 'paginas'}}},
              message: 'Só src/rotas.tsx importa páginas.',
            },
            {
              from: {file: {categories: 'rotas'}},
              allow: {to: {element: {type: 'paginas'}}},
            },
            {
              // O teste de uma página precisa importar a página.
              from: {file: {categories: 'teste'}},
              allow: {to: {element: {type: 'paginas'}}},
            },
            {
              // Sem porta dos fundos: código de produção não importa arquivo de teste.
              disallow: {to: {file: {categories: 'teste'}}},
              message: 'Arquivo de teste não é importado por ninguém.',
            },
            {
              // O apoio de src/testes/ puxa @testing-library: nunca entra no bundle.
              disallow: {to: {file: {categories: 'apoio-teste'}}},
              message: 'Só arquivo de teste importa o apoio de src/testes/.',
            },
            {
              from: {file: {categories: 'teste'}},
              allow: {to: {file: {categories: 'apoio-teste'}}},
            },
            {
              // O package.json só é lido pelo teste de dependências.
              disallow: {to: {file: {categories: 'pacote'}}},
              message: 'Só arquivo de teste lê o package.json.',
            },
            {
              from: {file: {categories: 'teste'}},
              allow: {to: {file: {categories: 'pacote'}}},
            },
            {
              // Ciclo: a página que precisa de outra passa por rotas, não importa rotas.
              from: {element: {type: 'paginas'}},
              disallow: {to: {file: {categories: 'rotas'}}},
              message: 'Página não importa src/rotas.tsx; só o Shell e o App leem rotas.',
            },
          ],
        },
      ],
      // Nada fora de src/ entra no app (ex.: uma pasta frontend/ui/ paralela): todo
      // import local tem de cair num elemento ou arquivo conhecido de src/. Vale para
      // qualquer profundidade de ../, porque a checagem é sobre o caminho resolvido.
      'boundaries/no-unknown-dependencies': ['error', {require: 'any'}],
    },
  },
  {
    // O único lugar onde CSS entra: a ordem dos imports do Astryx em main.tsx.
    files: ['src/main.tsx'],
    rules: {
      'no-restricted-imports': ['error', {patterns: [bibliotecasProibidas, importAbsoluto]}],
    },
  },
  {
    // Os blocos de base podem usar HTML cru; testes também.
    files: ['src/componentes/base/**', 'src/**/*.test.{ts,tsx}'],
    rules: {'react/forbid-elements': 'off'},
  },
  {
    // Só os blocos de base podem pôr style/className num elemento do DOM.
    files: ['src/componentes/base/**'],
    rules: {'react/forbid-dom-props': 'off'},
  },
);
