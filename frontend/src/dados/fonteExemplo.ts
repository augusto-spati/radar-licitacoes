import exemplo from './exemplo.json';
import type {Fonte, Filtros} from './fonte';
import {instante} from './formatos';
import type {EditalDetalhe, Lista} from './tipos';

type Dados = {atualizado_em: string | null; editais: EditalDetalhe[]};
const dadosExemplo = exemplo as Dados;

// Mesma semântica de GET /api/editais: linha pelos itens de `linhas`, UFs por
// pertencimento (sem diferenciar caixa), sem `todos` só os abertos (situação
// "aberta" e prazo futuro; sem prazo não é aberto), ordenado por encerramento
// crescente com nulos por último.
const estaAberto = (e: EditalDetalhe, agora: Date) =>
  e.situacao_normalizada === 'aberta' &&
  e.data_encerramento_proposta !== null &&
  instante(e.data_encerramento_proposta) > agora.getTime();

function selecionar(
  dados: Dados,
  {linha, ufs, todos = false}: Filtros,
  agora: Date,
): EditalDetalhe[] {
  const ufsPedidas = ufs && ufs.length > 0 ? new Set(ufs.map(u => u.toUpperCase())) : null;
  const chave = (e: EditalDetalhe) =>
    e.data_encerramento_proposta === null ? Infinity : instante(e.data_encerramento_proposta);
  return dados.editais
    .filter(e => linha === undefined || e.linhas.includes(linha))
    .filter(e => ufsPedidas === null || (e.uf !== null && ufsPedidas.has(e.uf.toUpperCase())))
    .filter(e => todos || estaAberto(e, agora))
    .sort((a, b) => chave(a) - chave(b));
}

export function criarFonteExemplo(
  agora: () => Date = () => new Date(),
  dados: Dados = dadosExemplo,
): Fonte {
  return {
    listar: async (f): Promise<Lista> => ({
      atualizado_em: dados.atualizado_em,
      editais: selecionar(dados, f, agora()),
    }),
    obter: async id => dados.editais.find(e => e.id_publico === id) ?? null,
    // O exemplo não traz análise por IA: o detalhe mostra "ainda não analisado".
    analise: async () => null,
    // Nem histórico do órgão: o detalhe mostra "sem histórico".
    historico: async () => null,
    status: async () => ({atualizado_em: dados.atualizado_em}),
  };
}

export const fonteExemplo = criarFonteExemplo();
