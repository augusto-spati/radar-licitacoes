import {describe, expect, it} from 'vitest';
import pacote from '../package.json';

// Lista explícita do que pode ir para o bundle. Dependência de runtime nova só
// entra editando esta lista de propósito (e com pedido explícito).
const permitidas = [
  '@astryxdesign/core',
  '@astryxdesign/theme-neutral',
  '@stylexjs/stylex',
  'lucide-react',
  'react',
  'react-dom',
  'react-router',
];

describe('dependências de runtime', () => {
  it('estão todas na lista permitida', () => {
    const fora = Object.keys(pacote.dependencies).filter(nome => !permitidas.includes(nome));
    expect(fora).toEqual([]);
  });
});
