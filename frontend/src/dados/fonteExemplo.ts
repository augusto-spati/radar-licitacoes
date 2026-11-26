import exemplo from './exemplo.json';
import type {Fonte, Filtros} from './fonte';
import {instante} from './formatos';
import type {EditalDetalhe, Lista} from './tipos';

const dados = exemplo as {atualizado_em: string | null; editais: EditalDetalhe[]};

// Mesma semântica de GET /api/editais: linha pelos itens de `linhas`, UFs por
// pertencimento (sem diferenciar caixa), só encerramento futuro sem `todos`,
// ordenado por encerramento crescente com nulos por último.
function selecionar({linha, ufs, todos = false}: Filtros, agora: Date): EditalDetalhe[] {
  const ufsPedidas = ufs && ufs.length > 0 ? new Set(ufs.map(u => u.toUpperCase())) : null;
  const chave = (e: EditalDetalhe) =>
    e.data_encerramento_proposta === null ? Infinity : instante(e.data_encerramento_proposta);
  return dados.editais
    .filter(e => linha === undefined || e.linhas.includes(linha))
    .filter(e => ufsPedidas === null || (e.uf !== null && ufsPedidas.has(e.uf.toUpperCase())))
    .filter(e => todos || chave(e) > agora.getTime())
    .sort((a, b) => chave(a) - chave(b));
}

export function criarFonteExemplo(agora: () => Date = () => new Date()): Fonte {
  return {
    listar: async (f): Promise<Lista> => ({
      atualizado_em: dados.atualizado_em,
      editais: selecionar(f, agora()),
    }),
    obter: async id => dados.editais.find(e => e.numero_controle_pncp === id) ?? null,
    status: async () => ({atualizado_em: dados.atualizado_em}),
  };
}

export const fonteExemplo = criarFonteExemplo();
