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
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {globals: {...globals.browser, ...globals.node}},
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: {boundaries, react},
    settings: {
      'import/resolver': {node: {extensions: ['.ts', '.tsx', '.js', '.jsx']}},
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
        {category: 'src', pattern: 'src/**'},
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
              // Ciclo: a página que precisa de outra passa por rotas, não importa rotas.
              from: {element: {type: 'paginas'}},
              disallow: {to: {file: {categories: 'rotas'}}},
              message: 'Página não importa src/rotas.tsx; só o Shell e o App leem rotas.',
            },
          ],
        },
      ],
      'no-restricted-imports': ['error', {patterns: [bibliotecasProibidas, cssProibido]}],
      'react/forbid-elements': ['error', {forbid: elementosProibidos}],
      // Sem cor ou medida fixa no DOM. Props de componente do Astryx não são afetadas.
      'react/forbid-dom-props': ['error', {forbid: ['style', 'className']}],
    },
  },
  {
    // O único lugar onde CSS entra: a ordem dos imports do Astryx em main.tsx.
    files: ['src/main.tsx'],
    rules: {
      'no-restricted-imports': ['error', {patterns: [bibliotecasProibidas]}],
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
