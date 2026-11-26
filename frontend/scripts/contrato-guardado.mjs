// Guarda dos arquivos que fazem o contrato do front valer. Se algum deles mudou
// no intervalo de commits do push ou do PR, pelo menos um commit do intervalo
// tem de trazer o trailer `Contrato-Front: aprovado` (aprovação do Augusto).
//
// Roda no CI pelo workflow `guarda-base` (pull_request_target), da raiz do
// repositório, a partir do checkout da BASE do PR (não da cabeça), para que um PR
// não possa editar a guarda e se aprovar. A lista GUARDADOS usada é a da base.
//   node frontend/scripts/contrato-guardado.mjs
// O intervalo vem das variáveis do GitHub Actions (veja `escolherIntervalo`).
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

export const TRAILER = 'Contrato-Front';
export const APROVADO = 'aprovado';

// Caminhos a partir da raiz do repositório. Termina em "/" = a pasta inteira;
// com "*" = glob (o "*" casa qualquer coisa menos "/"). O CODEOWNERS espelha a lista.
export const GUARDADOS = [
  'frontend/eslint.config.*',
  'frontend/.jscpd.json',
  'frontend/package.json',
  'frontend/package-lock.json',
  'frontend/index.html',
  'frontend/vite.config.*',
  'frontend/vitest.config.*',
  'frontend/tsconfig*.json',
  '.gitignore',
  'frontend/.gitignore',
  'frontend/playwright.config.ts',
  'frontend/CLAUDE.md',
  'frontend/src/rotas.test.ts',
  'frontend/src/dependencias.test.ts',
  'frontend/src/componentes.test.ts',
  'frontend/src/eslint-contrato.test.ts',
  'frontend/src/componentes/Shell.tsx',
  'frontend/e2e/',
  'frontend/scripts/',
  '.github/workflows/',
  '.github/CODEOWNERS',
];

function casa(guardado, caminho) {
  if (guardado.endsWith('/')) return caminho.startsWith(guardado);
  if (!guardado.includes('*')) return caminho === guardado;
  const regex = guardado
    .split('*')
    .map(parte => parte.replace(/[.+?^${}()|[\]\\]/g, '\\$&'))
    .join('[^/]*');
  return new RegExp(`^${regex}$`).test(caminho);
}

export function ehGuardado(caminho) {
  return GUARDADOS.some(g => casa(g, caminho));
}

/**
 * Decide o resultado a partir dos arquivos alterados e dos valores do trailer
 * em cada commit do intervalo (um texto por commit, vazio quando não tem).
 */
export function decidir(alterados, trailersPorCommit) {
  const guardados = [...new Set(alterados.filter(ehGuardado))].sort();
  const aprovado = trailersPorCommit.some(valores =>
    valores
      .split(/[\n,]/)
      .map(v => v.trim().toLowerCase())
      .includes(APROVADO),
  );
  return {ok: guardados.length === 0 || aprovado, guardados, aprovado};
}

/**
 * Intervalo `base..topo` a conferir: do merge base entre a base e a cabeça do PR
 * até a cabeça. `git(args)` devolve a saída do git ou null se o comando falhar.
 */
export function escolherIntervalo(ambiente, git) {
  const {BASE_PR, CABECA_PR} = ambiente;
  const base = git(['merge-base', BASE_PR, CABECA_PR]);
  if (base === null) throw new Error('sem merge base entre a base e a cabeça do PR');
  return {base, topo: CABECA_PR};
}

function git(args) {
  try {
    return execFileSync('git', args, {encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore']}).trim();
  } catch {
    return null;
  }
}

function executar() {
  const {base, topo} = escolherIntervalo(process.env, git);
  const saidaDiff = git(['diff', '--name-only', '--no-renames', base, topo]);
  const saidaLog = git([
    'log',
    `--format=%(trailers:key=${TRAILER},valueonly,separator=%x2C)%x00`,
    `${base}..${topo}`,
  ]);
  if (saidaDiff === null || saidaLog === null) throw new Error(`git falhou em ${base}..${topo}`);
  const alterados = saidaDiff.split('\n').filter(Boolean);
  const trailers = saidaLog.split('\0').map(t => t.trim());
  const {ok, guardados, aprovado} = decidir(alterados, trailers);

  console.log(`intervalo ${base.slice(0, 12)}..${topo.slice(0, 12)}`);
  if (guardados.length === 0) {
    console.log('nenhum arquivo de guarda do contrato do front mudou');
  } else {
    console.log(`arquivos de guarda alterados:\n  ${guardados.join('\n  ')}`);
    console.log(aprovado ? `${TRAILER}: ${APROVADO} encontrado` : `sem "${TRAILER}: ${APROVADO}"`);
  }
  if (!ok) {
    console.error(
      `\nEstes arquivos guardam o contrato do front e só mudam com aprovação do Augusto: ` +
        `um commit do intervalo precisa do trailer "${TRAILER}: ${APROVADO}". ` +
        'Se a mudança não foi pedida, desfaça-a (veja frontend/CLAUDE.md).',
    );
    process.exit(1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  executar();
}
