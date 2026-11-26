// Contrato contra a deriva: estas regras fazem `npm run lint` falhar quando
// alguém (gente ou agente) cria biblioteca de UI paralela, HTML solto ou
// muda a estrutura de páginas. Não afrouxe uma regra para o lint passar.
import js from '@eslint/js';
import boundaries from 'eslint-plugin-boundaries';
import react from 'eslint-plugin-react';
import globals from 'globals';
import tseslint from 'typescript-eslint';

const bibliotecasProibidas = {
  group: [
    '@mui/*',
    '@mui/**',
    'antd',
    'antd/**',
    '@chakra-ui/*',
    '@chakra-ui/**',
    'react-bootstrap',
    'react-bootstrap/**',
    'bootstrap',
    'bootstrap/**',
    'tailwindcss',
    'tailwindcss/**',
  ],
  message: 'A UI é só Astryx. Procure o componente com `npx astryx search`.',
};

const cssProibido = {
  group: ['*.css', '**/*.css'],
  message: 'CSS só entra em src/main.tsx. Estilo vem de props, xstyle e tokens do Astryx.',
};

const elementosProibidos = ['button', 'input', 'select', 'table', 'a', 'div', 'span'].map(
  element => ({
    element,
    message: `<${element}> solto é proibido aqui: use o componente do Astryx (ou um de src/componentes/base/).`,
  }),
);

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
          ],
        },
      ],
      'no-restricted-imports': ['error', {patterns: [bibliotecasProibidas, cssProibido]}],
      'react/forbid-elements': ['error', {forbid: elementosProibidos}],
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
);
