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
});
