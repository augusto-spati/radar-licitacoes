import {execFileSync} from 'node:child_process';
import {copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {describe, expect, it} from 'vitest';
import {GUARDADOS, decidir, ehGuardado, escolherIntervalo} from './contrato-guardado.mjs';

describe('contrato-guardado: o que é guardado', () => {
  it.each([
    'frontend/eslint.config.js',
    'frontend/.jscpd.json',
    'frontend/src/rotas.test.ts',
    'frontend/src/dependencias.test.ts',
    'frontend/src/componentes.test.ts',
    'frontend/src/eslint-contrato.test.ts',
    'frontend/src/componentes/Shell.tsx',
    'frontend/e2e/shell.spec.ts',
    'frontend/e2e/__screenshots__/desktop/shell.spec.ts/shell.png',
    'frontend/playwright.config.ts',
    'frontend/CLAUDE.md',
    'frontend/scripts/verificar-contrato.mjs',
    'frontend/scripts/contrato-guardado.mjs',
    'frontend/package.json',
    'frontend/package-lock.json',
    'frontend/index.html',
    '.github/workflows/guarda-base.yml',
    '.github/workflows/ci.yml',
    '.github/CODEOWNERS',
    'frontend/tsconfig.json',
    'frontend/tsconfig.app.json',
    'frontend/tsconfig.node.json',
    'frontend/vitest.config.ts',
    'frontend/vitest.config.mts',
    'frontend/vite.config.ts',
    'frontend/vite.config.mjs',
    'frontend/eslint.config.mjs',
    '.gitignore',
    'frontend/.gitignore',
  ])('%s é guardado', caminho => {
    expect(ehGuardado(caminho)).toBe(true);
  });

  it.each([
    'frontend/src/paginas/Lista.tsx',
    'frontend/src/componentes/CartaoEdital.tsx',
    'frontend/src/dados/formatos.ts',
    'frontend/e2e-outro/x.ts',
    'src/radar/api/app.py',
    'frontend/src/componentes/Shell.test.tsx',
    'frontend/src/tsconfig.json',
    'frontend/tsconfig-extra.txt',
    'frontend/vitest.setup.ts',
    'src/.gitignore',
    'frontend/src/.gitignore',
    'gitignore',
  ])('%s não é guardado', caminho => {
    expect(ehGuardado(caminho)).toBe(false);
  });
});

describe('contrato-guardado: decisão', () => {
  const guardado = 'frontend/eslint.config.js';
  const comum = 'frontend/src/paginas/Lista.tsx';

  it('sem arquivo guardado no intervalo passa, com ou sem trailer', () => {
    expect(decidir([comum], ['']).ok).toBe(true);
    expect(decidir([], []).ok).toBe(true);
  });

  it('arquivo guardado sem trailer em nenhum commit reprova e diz qual', () => {
    expect(decidir([comum, guardado], ['', ''])).toEqual({
      ok: false,
      guardados: [guardado],
      aprovado: false,
    });
  });

  it('basta um commit do intervalo com "Contrato-Front: aprovado"', () => {
    expect(decidir([guardado], ['', 'aprovado', '']).ok).toBe(true);
    expect(decidir([guardado], [' Aprovado ']).ok).toBe(true);
    expect(decidir([guardado], ['outro,aprovado']).ok).toBe(true);
  });

  it('outro valor do trailer não aprova', () => {
    expect(decidir([guardado], ['nao', 'aprovado-depois', 'pendente']).ok).toBe(false);
  });
});

describe('contrato-guardado: quem decide é a base', () => {
  it.each(['frontend/tsconfig.json', 'frontend/vitest.config.ts', '.gitignore'])(
    'mudar %s sem o trailer reprova',
    arquivo => {
      expect(decidir([arquivo], ['']).ok).toBe(false);
      expect(decidir([arquivo], ['aprovado']).ok).toBe(true);
    },
  );

  it('editar o próprio script sem trailer reprova na decisão da base', () => {
    const r = decidir(['frontend/scripts/contrato-guardado.mjs'], ['']);
    expect(r.ok).toBe(false);
    expect(r.guardados).toEqual(['frontend/scripts/contrato-guardado.mjs']);
  });

  it('script editado para sempre aprovar, rodando a versão da base, continua reprovado', () => {
    const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
    const tmp = mkdtempSync(path.join(os.tmpdir(), 'guarda-'));
    try {
      const alvo = path.join(tmp, 'frontend', 'scripts', 'contrato-guardado.mjs');
      const g = (...args) =>
        execFileSync(
          'git',
          ['-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', ...args],
          {cwd: tmp, encoding: 'utf8'},
        ).trim();
      g('init', '-q');
      mkdirSync(path.dirname(alvo), {recursive: true});
      copyFileSync(path.join(raiz, 'frontend', 'scripts', 'contrato-guardado.mjs'), alvo);
      g('add', '.');
      g('commit', '-q', '-m', 'base');
      const base = g('rev-parse', 'HEAD');
      // a cabeça troca o script por um que sempre aprova
      writeFileSync(alvo, 'console.log("sempre aprova");\n');
      g('add', '.');
      g('commit', '-q', '-m', 'adultera a guarda');
      // a versão da base, como o CI a extrai para fora da árvore
      const daBase = path.join(tmp, 'guarda-da-base.mjs');
      writeFileSync(daBase, g('show', `${base}:frontend/scripts/contrato-guardado.mjs`) + '\n');
      const rodar = script =>
        execFileSync('node', [script], {
          cwd: tmp,
          encoding: 'utf8',
          stdio: ['ignore', 'pipe', 'pipe'],
          env: {...process.env, EVENTO: 'push', ANTES: base},
        });
      let falha;
      try {
        rodar(daBase);
      } catch (e) {
        falha = e;
      }
      expect(falha?.status).toBe(1);
      expect(String(falha?.stderr)).toContain('Contrato-Front: aprovado');
      expect(rodar(alvo)).toContain('sempre aprova'); // a adulterada passaria
    } finally {
      rmSync(tmp, {recursive: true, force: true});
    }
  });
});

describe('contrato-guardado: intervalo', () => {
  // git falso: responde só aos comandos conhecidos; o resto falha (null).
  const gitFalso = respostas => args => respostas[args.join(' ')] ?? null;

  it('PR: do merge base até a cabeça do PR', () => {
    const git = gitFalso({'merge-base b1 c1': 'mb1'});
    expect(
      escolherIntervalo({EVENTO: 'pull_request', BASE_PR: 'b1', CABECA_PR: 'c1'}, git),
    ).toEqual({base: 'mb1', topo: 'c1'});
  });

  it('push: do before até HEAD', () => {
    const git = gitFalso({'rev-parse HEAD': 'h1', 'cat-file -e a1^{commit}': ''});
    expect(escolherIntervalo({EVENTO: 'push', ANTES: 'a1'}, git)).toEqual({base: 'a1', topo: 'h1'});
  });

  it('push de branch nova (before zerado): merge base com o branch padrão', () => {
    const git = gitFalso({'rev-parse HEAD': 'h1', 'merge-base HEAD origin/main': 'mb1'});
    expect(
      escolherIntervalo({EVENTO: 'push', ANTES: '0'.repeat(40), BRANCH_PADRAO: 'main'}, git),
    ).toEqual({base: 'mb1', topo: 'h1'});
  });

  it('push no próprio branch padrão sem before válido: commit anterior', () => {
    const git = gitFalso({
      'rev-parse HEAD': 'h1',
      'merge-base HEAD origin/main': 'h1',
      'rev-parse HEAD~1': 'p1',
    });
    expect(escolherIntervalo({EVENTO: 'push', ANTES: 'sumiu', BRANCH_PADRAO: 'main'}, git)).toEqual({
      base: 'p1',
      topo: 'h1',
    });
  });
});

describe('contrato-guardado: workflow da base', () => {
  const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
  const yml = readFileSync(path.join(raiz, '.github', 'workflows', 'guarda-base.yml'), 'utf8');

  it('roda por pull_request_target, só lê o repositório e não executa código do PR', () => {
    expect(yml).toContain('pull_request_target:');
    expect(yml).toContain('guarda-base:');
    expect(yml).toContain('contents: read');
    expect(yml).toContain('ref: ${{ github.event.pull_request.base.sha }}');
    expect(yml).toContain('node frontend/scripts/contrato-guardado.mjs');
    for (const proibido of ['npm ', 'npx ', 'pull_request.head.ref', 'ref: ${{ github.event.pull_request.head']) {
      expect(yml).not.toContain(proibido);
    }
  });
});

describe('contrato-guardado: amarrações', () => {
  const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

  it('o CODEOWNERS lista exatamente os arquivos guardados', () => {
    const linhas = readFileSync(path.join(raiz, '.github', 'CODEOWNERS'), 'utf8')
      .split('\n')
      .map(l => l.trim())
      .filter(l => l !== '' && !l.startsWith('#'));
    expect(linhas.map(l => l.split(/\s+/)[0].replace(/^\//, ''))).toEqual(GUARDADOS);
    expect(linhas.every(l => l.endsWith(' @augusto-spati'))).toBe(true);
  });

  it('o CI roda a guarda com o histórico completo e o front roda o verificador', () => {
    const ci = readFileSync(path.join(raiz, '.github', 'workflows', 'ci.yml'), 'utf8');
    expect(ci).toContain('  contrato-guardado:');
    expect(ci).toContain('fetch-depth: 0');
    expect(ci).toContain('git show "$BASE:frontend/scripts/contrato-guardado.mjs"');
    expect(ci).toContain('node "$RUNNER_TEMP/guarda.mjs"');
    expect(ci).not.toContain('run: node frontend/scripts/contrato-guardado.mjs');
    expect(ci).toContain('npm run contrato:verificar');
    expect(ci).toContain('git diff --exit-code src/dados/api.d.ts');
  });
});
