import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {ESLint} from 'eslint';
import {describe, expect, it} from 'vitest';

// Roda o ESLint com a configuração real do projeto sobre código em memória.
// Cada amostra simula uma deriva que o contrato tem de barrar.
const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const eslint = new ESLint({cwd: raiz});

async function regrasVioladas(arquivo: string, codigo: string): Promise<string[]> {
  const [resultado] = await eslint.lintText(codigo, {filePath: path.join(raiz, arquivo)});
  return resultado.messages.filter(m => m.severity === 2).map(m => m.ruleId ?? '(sem regra)');
}

describe('contrato do ESLint', () => {
  it('barra página importando outra página', async () => {
    const codigo = [
      "import {Detalhe} from './Detalhe';",
      'export function Amostra() {',
      '  return <Detalhe />;',
      '}',
    ].join('\n');
    expect(await regrasVioladas('src/paginas/Amostra.tsx', codigo)).toContain(
      'boundaries/dependencies',
    );
  });

  it('barra componente importando página', async () => {
    const codigo = [
      "import {Lista} from '../paginas/Lista';",
      'export function Amostra() {',
      '  return <Lista />;',
      '}',
    ].join('\n');
    expect(await regrasVioladas('src/componentes/Amostra.tsx', codigo)).toContain(
      'boundaries/dependencies',
    );
  });

  it('barra qualquer arquivo fora de rotas.tsx importando página', async () => {
    const codigo = "import {Lista} from './paginas/Lista';\nexport const amostra = Lista;\n";
    expect(await regrasVioladas('src/Amostra.tsx', codigo)).toContain('boundaries/dependencies');
  });

  it('barra <button> solto em src/paginas', async () => {
    const codigo = 'export function Amostra() {\n  return <button>Ok</button>;\n}\n';
    expect(await regrasVioladas('src/paginas/Amostra.tsx', codigo)).toContain(
      'react/forbid-elements',
    );
  });

  it('barra <div> solto em src/componentes', async () => {
    const codigo = 'export function Amostra() {\n  return <div>Ok</div>;\n}\n';
    expect(await regrasVioladas('src/componentes/Amostra.tsx', codigo)).toContain(
      'react/forbid-elements',
    );
  });

  it('barra import de @mui/material', async () => {
    const codigo = "import {Button} from '@mui/material';\nexport const amostra = Button;\n";
    expect(await regrasVioladas('src/paginas/Amostra.tsx', codigo)).toContain(
      'no-restricted-imports',
    );
  });

  it('barra CSS importado fora de main.tsx', async () => {
    const codigo = "import '@astryxdesign/core/astryx.css';\nexport const amostra = 1;\n";
    expect(await regrasVioladas('src/componentes/Amostra.tsx', codigo)).toContain(
      'no-restricted-imports',
    );
  });

  it('aceita <div> dentro de src/componentes/base e CSS em main.tsx', async () => {
    const base = 'export function Amostra() {\n  return <div>Ok</div>;\n}\n';
    expect(await regrasVioladas('src/componentes/base/Amostra.tsx', base)).toEqual([]);
    const main = "import '@astryxdesign/core/astryx.css';\nexport const amostra = 1;\n";
    expect(await regrasVioladas('src/main.tsx', main)).toEqual([]);
  });

  it('aceita o teste de uma página importando a própria página', async () => {
    const codigo = "import {Lista} from './Lista';\nexport const amostra = Lista;\n";
    expect(await regrasVioladas('src/paginas/Lista.test.tsx', codigo)).toEqual([]);
  });

  it('barra código de produção importando arquivo de teste', async () => {
    const codigo = "import '../rotas.test';\nexport const amostra = 1;\n";
    expect(await regrasVioladas('src/componentes/Amostra.tsx', codigo)).toContain(
      'boundaries/dependencies',
    );
  });

  it('barra página importando src/rotas.tsx, mas não o componente', async () => {
    const codigo = "import {rotas} from '../rotas';\nexport const amostra = rotas;\n";
    expect(await regrasVioladas('src/paginas/Amostra.tsx', codigo)).toContain(
      'boundaries/dependencies',
    );
    expect(await regrasVioladas('src/componentes/Amostra.tsx', codigo)).toEqual([]);
  });

  it.each(['./x.scss', './x.sass', './x.less', './y.css?inline', '@astryxdesign/core/astryx.css?url'])(
    'barra folha de estilo %s fora de main.tsx',
    async fonte => {
      const codigo = `import '${fonte}';\nexport const amostra = 1;\n`;
      expect(await regrasVioladas('src/componentes/Amostra.tsx', codigo)).toContain(
        'no-restricted-imports',
      );
    },
  );

  it.each([
    'antd',
    'tailwindcss',
    'bootstrap/dist/js/bootstrap',
    'styled-components',
    '@emotion/react',
    '@mantine/core',
    '@radix-ui/react-dialog',
    '@headlessui/react',
    '@chakra-ui/react',
    'primereact/button',
    'semantic-ui-react',
    '@fluentui/react',
    'daisyui',
    '@nextui-org/react',
    '@heroui/react',
    '@shadcn/ui',
  ])('barra a biblioteca de UI %s', async fonte => {
    const codigo = `import '${fonte}';\nexport const amostra = 1;\n`;
    expect(await regrasVioladas('src/paginas/Amostra.tsx', codigo)).toContain(
      'no-restricted-imports',
    );
  });

  it('aceita o Astryx e pacotes de nome parecido com os proibidos', async () => {
    const codigo = [
      "import {Button} from '@astryxdesign/core/Button';",
      "import 'bootstrap-icons-falso';",
      'export const amostra = Button;',
    ].join('\n');
    expect(await regrasVioladas('src/paginas/Amostra.tsx', codigo)).toEqual([]);
  });

  it.each(['textarea', 'form', 'label', 'img', 'section', 'p', 'ul', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])(
    'barra <%s> solto em src/paginas',
    async elemento => {
      const codigo = `export function Amostra() {\n  return <${elemento} />;\n}\n`;
      expect(await regrasVioladas('src/paginas/Amostra.tsx', codigo)).toContain(
        'react/forbid-elements',
      );
    },
  );

  it.each(['style={{color: "red"}}', 'className="vermelho"'])(
    'barra %s em elemento do DOM fora de base',
    async atributo => {
      const codigo = `export function Amostra() {\n  return <svg ${atributo} />;\n}\n`;
      expect(await regrasVioladas('src/paginas/Amostra.tsx', codigo)).toContain(
        'react/forbid-dom-props',
      );
      expect(await regrasVioladas('src/componentes/base/Amostra.tsx', codigo)).toEqual([]);
    },
  );

  it('aceita className como prop de componente do Astryx', async () => {
    const codigo = [
      "import {Heading} from '@astryxdesign/core/Heading';",
      'export function Amostra() {',
      '  return <Heading level={1} className="titulo">Ok</Heading>;',
      '}',
    ].join('\n');
    expect(await regrasVioladas('src/paginas/Amostra.tsx', codigo)).toEqual([]);
  });

  it('barra produção importando o apoio de testes; teste pode importar', async () => {
    // src/testes/ é apoio de teste (puxa @testing-library): fora do bundle.
    const codigo = "import {renderizar} from '../testes/apoio';\nexport const amostra = renderizar;\n";
    expect(await regrasVioladas('src/paginas/Amostra.tsx', codigo)).toContain(
      'boundaries/dependencies',
    );
    expect(await regrasVioladas('src/componentes/Amostra.tsx', codigo)).toContain(
      'boundaries/dependencies',
    );
    expect(await regrasVioladas('src/paginas/Lista.test.tsx', codigo)).toEqual([]);
  });

  // Montado em partes: o texto literal da diretiva é proibido em todo o front
  // (scripts/verificar-contrato.mjs), inclusive neste teste.
  const diretiva = (resto: string) => ['eslint', resto].join('-');

  it.each([
    `/* ${diretiva('disable')} */`,
    `/* ${diretiva('disable')} react/forbid-elements */`,
    `// ${diretiva('disable-next-line')} react/forbid-elements`,
    ['/*', 'eslint react/forbid-elements: "off"', '*/'].join(' '),
  ])('comentário %s não desliga o contrato', async comentario => {
    const codigo = [comentario, 'export function Amostra() {', '  return <div>Ok</div>;', '}'].join(
      '\n',
    );
    expect(await regrasVioladas('src/paginas/Amostra.tsx', codigo)).toContain(
      'react/forbid-elements',
    );
  });

  it.each([
    ['src/paginas/Amostra.tsx', '../../e2e/relogio'],
    ['src/paginas/Amostra.tsx', '../../vite.config'],
    ['src/componentes/Amostra.tsx', '../../../frontend/e2e/relogio'],
    ['src/componentes/base/Amostra.tsx', '../../../e2e/relogio'],
    ['src/Amostra.tsx', '../e2e/relogio'],
  ])('barra %s importando %s, fora de src/', async (arquivo, fonte) => {
    const codigo = `import {congelarRelogio} from '${fonte}';\nexport const amostra = congelarRelogio;\n`;
    expect(await regrasVioladas(arquivo, codigo)).toContain('boundaries/no-unknown-dependencies');
  });

  it('só teste lê o package.json', async () => {
    const codigo = "import pacote from '../../package.json';\nexport const amostra = pacote;\n";
    expect(await regrasVioladas('src/paginas/Amostra.tsx', codigo)).toContain(
      'boundaries/dependencies',
    );
    expect(await regrasVioladas('src/paginas/Amostra.test.ts', codigo)).toEqual([]);
  });

  it('barra import absoluto (a partir da raiz do projeto)', async () => {
    const codigo = "import {Botao} from '/ui/Botao';\nexport const amostra = Botao;\n";
    expect(await regrasVioladas('src/paginas/Amostra.tsx', codigo)).toContain(
      'no-restricted-imports',
    );
  });

  it('as regras de UI valem também fora de src/ (ex.: frontend/ui/)', async () => {
    const html = 'export function Botao() {\n  return <button className="x">Ok</button>;\n}\n';
    const violadas = await regrasVioladas('ui/Botao.tsx', html);
    expect(violadas).toContain('react/forbid-elements');
    expect(violadas).toContain('react/forbid-dom-props');
    const mui = "import {Button} from '@mui/material';\nexport const Botao = Button;\n";
    expect(await regrasVioladas('ui/Botao.tsx', mui)).toContain('no-restricted-imports');
  });
});
