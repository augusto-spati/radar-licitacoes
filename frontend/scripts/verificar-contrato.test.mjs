import {spawnSync} from 'node:child_process';
import {copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {afterEach, describe, expect, it} from 'vitest';
import {arquivosDeCodigo, verificar, violacoesNoTexto} from './verificar-contrato.mjs';

// Os textos proibidos são montados em partes: o próprio verificador reprovaria
// este arquivo se eles aparecessem por extenso.
const p = (...partes) => partes.join('');
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

describe('verificar-contrato', () => {
  it.each([
    p('/* eslint', '-disable */'),
    p('// eslint', '-disable-next-line react/forbid-elements'),
    p('/* eslint', '-disable-line */'),
    p('/* eslint', '-enable */'),
    p('/* eslint', ' react/forbid-elements: "off" */'),
    p('/* eslint', '-env node */'),
    p('/* glo', 'bal fetch */'),
    p('// jscpd', ':ignore-start'),
    p('/* JSCPD', ':IGNORE-END */'),
  ])('reprova %s', amostra => {
    expect(violacoesNoTexto(`${amostra}\nexport const x = 1;\n`)).not.toEqual([]);
  });

  it('aceita código comum, inclusive nomes parecidos', () => {
    const codigo = [
      "import boundaries from 'eslint-plugin-boundaries';",
      "const linterOptions = {noInlineConfig: true, reportUnusedDisableDirectives: 'error'};",
      '// O ESLint roda no CI; o jscpd procura duplicação.',
      'export {boundaries, linterOptions};',
    ].join('\n');
    expect(violacoesNoTexto(codigo)).toEqual([]);
  });

  describe('numa pasta', () => {
    let pasta = '';
    afterEach(() => rmSync(pasta, {recursive: true, force: true}));

    it('acha a diretiva em qualquer pasta de código, mas não em node_modules nem dist', () => {
      pasta = mkdtempSync(path.join(tmpdir(), 'contrato-'));
      for (const sub of ['src/paginas', 'ui', 'node_modules/pacote', 'dist']) {
        mkdirSync(path.join(pasta, sub), {recursive: true});
      }
      const desligar = p('/* eslint', '-disable */\n');
      writeFileSync(path.join(pasta, 'src/paginas/Limpa.tsx'), 'export const a = 1;\n');
      writeFileSync(path.join(pasta, 'ui/Botao.tsx'), desligar);
      writeFileSync(path.join(pasta, 'node_modules/pacote/index.js'), desligar);
      writeFileSync(path.join(pasta, 'dist/app.js'), desligar);

      expect(verificar(pasta)).toEqual([
        {arquivo: 'ui/Botao.tsx', motivos: ['diretiva que desliga regra do ESLint']},
      ]);
    });
  });

  it('o front de verdade está limpo e o varrimento inclui src/, e2e/ e as configs', () => {
    const arquivos = arquivosDeCodigo(frontend);
    expect(arquivos).toContain('src/componentes/Shell.tsx');
    expect(arquivos).toContain('e2e/shell.spec.ts');
    expect(arquivos).toContain('eslint.config.js');
    expect(arquivos.some(a => a.startsWith('node_modules/'))).toBe(false);
    expect(verificar(frontend)).toEqual([]);
  });
});

// A duplicação vale para o front inteiro, não só src/: uma cópia numa pasta
// nova (ex.: frontend/ui/) reprova o `npm run duplicacao`.
describe('contrato do jscpd', () => {
  const jscpd = path.join(frontend, 'node_modules', 'jscpd', 'run-jscpd.js');
  const rodar = () =>
    spawnSync(process.execPath, [jscpd, '--config', '.jscpd.json', '--silent'], {cwd: frontend});

  it('reprova uma cópia de página numa pasta fora de src/', () => {
    // Controle: sem a cópia, o front passa (o erro abaixo é mesmo a duplicação).
    expect(rodar().status).toBe(0);
    const pasta = path.join(frontend, `ui-amostra-${process.pid}`);
    mkdirSync(pasta, {recursive: true});
    try {
      copyFileSync(path.join(frontend, 'src/paginas/Detalhe.tsx'), path.join(pasta, 'Copia.tsx'));
      expect(rodar().status).not.toBe(0);
    } finally {
      rmSync(pasta, {recursive: true, force: true});
    }
  }, 60_000);

  it('a configuração varre a raiz do front e só pula o que é gerado', () => {
    const config = JSON.parse(readFileSync(path.join(frontend, '.jscpd.json'), 'utf8'));
    expect(config.threshold).toBe(0);
    expect(config.minTokens).toBe(50);
    expect(config.path).toEqual(['.']);
    expect(config.ignore).toEqual([
      '**/node_modules/**',
      '**/dist/**',
      '**/playwright-report/**',
      '**/test-results/**',
      '**/e2e/__screenshots__/**',
      '**/src/dados/api.d.ts',
      '**/*.json',
    ]);
  });
});
