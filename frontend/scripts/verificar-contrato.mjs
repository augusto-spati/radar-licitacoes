// Reprova qualquer arquivo de código do front que tente desligar o contrato por
// comentário: diretivas do ESLint (desligar, religar ou config inline) e o
// marcador de ignorar do jscpd. O ESLint já ignora config inline
// (`noInlineConfig`), mas o texto nem chega a entrar no repositório.
//
// Uso: `npm run contrato:verificar` (dentro de frontend/). Sem exceções por
// arquivo: este script e os testes montam os textos proibidos em partes.
import {readdirSync, readFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const juntar = (...partes) => partes.join('');

export const PROIBIDOS = [
  {
    regex: new RegExp(juntar('jscpd', ':', 'ignore'), 'i'),
    motivo: 'marcador que esconde duplicação do jscpd',
  },
  {
    regex: new RegExp(juntar('eslint', '-', '(?:disable|enable)'), 'i'),
    motivo: 'diretiva que desliga regra do ESLint',
  },
  {
    // Config inline: comentário de bloco que abre com eslint, eslint-env ou global(s).
    regex: new RegExp(juntar('/\\*\\s*(?:eslint|eslint', '-env|globals?)\\s'), 'i'),
    motivo: 'configuração inline do ESLint',
  },
];

// Pastas que não são código do front (dependências, build, relatórios).
const PASTAS_FORA = new Set(['node_modules', 'dist', 'playwright-report', 'test-results', 'blob-report', '.git']);
const EXTENSOES = new Set(['.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs']);

/** Motivos violados por um texto (vazio quando está limpo). */
export function violacoesNoTexto(texto) {
  return PROIBIDOS.filter(({regex}) => regex.test(texto)).map(({motivo}) => motivo);
}

/** Arquivos de código sob `raiz`, fora das pastas geradas. Caminhos relativos, com "/". */
export function arquivosDeCodigo(raiz) {
  const achados = [];
  const visitar = pasta => {
    for (const entrada of readdirSync(pasta, {withFileTypes: true})) {
      const caminho = path.join(pasta, entrada.name);
      if (entrada.isDirectory()) {
        if (!PASTAS_FORA.has(entrada.name)) visitar(caminho);
      } else if (EXTENSOES.has(path.extname(entrada.name))) {
        achados.push(path.relative(raiz, caminho).split(path.sep).join('/'));
      }
    }
  };
  visitar(raiz);
  return achados.sort();
}

/** [{arquivo, motivos}] de cada arquivo de código de `raiz` que tenta desligar o contrato. */
export function verificar(raiz) {
  return arquivosDeCodigo(raiz)
    .map(arquivo => ({arquivo, motivos: violacoesNoTexto(readFileSync(path.join(raiz, arquivo), 'utf8'))}))
    .filter(({motivos}) => motivos.length > 0);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const violacoes = verificar(raiz);
  for (const {arquivo, motivos} of violacoes) console.error(`${arquivo}: ${motivos.join('; ')}`);
  if (violacoes.length > 0) {
    console.error(
      '\nO contrato do front não se desliga por comentário. Corrija o código para a regra passar ' +
        '(veja frontend/CLAUDE.md); não afrouxe a regra.',
    );
    process.exit(1);
  }
  console.log(`contrato: ${arquivosDeCodigo(raiz).length} arquivos sem diretivas de desligar regra`);
}
