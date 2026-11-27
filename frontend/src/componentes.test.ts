import {readdirSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {describe, expect, it} from 'vitest';

// Lista fechada de componentes próprios (e das pastas de src/). Antes de criar
// um componente, procure no Astryx (`npx astryx search "<o que precisa>"`) e
// nos componentes que já existem aqui: quase sempre um deles serve.
// Mudar estas listas é mudar o contrato do front: o commit precisa do trailer
// `Contrato-Front: aprovado`, que só o Augusto dá (veja frontend/CLAUDE.md).
const AVISO =
  'Componente novo é mudança de contrato. Procure primeiro no Astryx (`npx astryx search`) ' +
  'e em src/componentes/; se ainda assim precisar, edite src/componentes.test.ts num commit ' +
  'com o trailer `Contrato-Front: aprovado` (só com pedido explícito do Augusto).';

const src = path.dirname(fileURLToPath(import.meta.url));

const COMPONENTES = [
  'Aderencia.tsx',
  'AvisoDesatualizado.tsx',
  'CartaoEdital.tsx',
  'EstadoCarga.tsx',
  'FiltrosEditais.tsx',
  'HistoricoOrgao.tsx',
  'ReguaDesconto.tsx',
  'SeloFonte.tsx',
  'Shell.tsx',
  'TokensLinhas.tsx',
  'base/README.md',
];

const PASTAS_DE_SRC = ['componentes', 'componentes/base', 'dados', 'paginas', 'testes'];

/** Arquivos (sem os testes) sob `pasta`, recursivo, com caminho relativo e "/". */
function arquivos(pasta: string): string[] {
  return readdirSync(pasta, {recursive: true, withFileTypes: true})
    .filter(e => e.isFile() && !/\.test\.[jt]sx?$/.test(e.name))
    .map(e => path.relative(pasta, path.join(e.parentPath, e.name)).split(path.sep).join('/'))
    .sort();
}

function pastas(pasta: string): string[] {
  return readdirSync(pasta, {recursive: true, withFileTypes: true})
    .filter(e => e.isDirectory())
    .map(e => path.relative(pasta, path.join(e.parentPath, e.name)).split(path.sep).join('/'))
    .sort();
}

describe('componentes (lista fechada)', () => {
  it('src/componentes/ e src/componentes/base/ têm exatamente estes arquivos', () => {
    expect(arquivos(path.join(src, 'componentes')), AVISO).toEqual(COMPONENTES);
  });

  it('src/ não ganha pasta nova (uma biblioteca paralela de UI, por exemplo)', () => {
    expect(pastas(src), AVISO).toEqual(PASTAS_DE_SRC);
  });
});
