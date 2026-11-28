import exemplo from './exemplo.json';
import type {Fonte, Filtros} from './fonte';
import {DIAS_DECIDIR, diasAteEncerrar, ehNovo, instante} from './formatos';
import type {EditalDetalhe, Lista, Painel} from './tipos';

type Dados = {atualizado_em: string | null; editais: EditalDetalhe[]};

// O exemplo.json é anterior aos campos de veredito e de primeira visita: quando faltam,
// sem análise e "visto" na data de publicação.
type Bruto = Omit<EditalDetalhe, 'veredito' | 'motivo_verificar' | 'primeiro_visto_em'> &
  Partial<Pick<EditalDetalhe, 'veredito' | 'motivo_verificar' | 'primeiro_visto_em'>>;
const completar = (e: Bruto): EditalDetalhe => ({
  ...e,
  veredito: e.veredito ?? null,
  motivo_verificar: e.motivo_verificar ?? null,
  primeiro_visto_em: e.primeiro_visto_em ?? e.data_publicacao,
});
const dadosExemplo: Dados = {
  ...(exemplo as {atualizado_em: string | null; editais: Bruto[]}),
  editais: (exemplo as {editais: Bruto[]}).editais.map(completar),
};

// Mesmo tamanho de seção do painel do back (radar.editais.painel.LIMITE_SECAO).
const LIMITE_SECAO = 30;

// Mesma semântica de GET /api/editais: linha pelos itens de `linhas`, UFs por
// pertencimento (sem diferenciar caixa), sem `todos` só os abertos (situação
// "aberta" e prazo futuro; sem prazo não é aberto), ordenado por encerramento
// crescente com nulos por último. O recorte (encerra em N dias, novos, vereditos) segue as
// regras do back: dias de calendário de Brasília, 3 dias de novo, sem_analise = veredito nulo.
const estaAberto = (e: EditalDetalhe, agora: Date) =>
  e.situacao_normalizada === 'aberta' &&
  e.data_encerramento_proposta !== null &&
  instante(e.data_encerramento_proposta) > agora.getTime();

function selecionar(
  dados: Dados,
  {linha, ufs, todos = false, encerraEmDias, novos = false, vereditos}: Filtros,
  agora: Date,
): EditalDetalhe[] {
  const ufsPedidas = ufs && ufs.length > 0 ? new Set(ufs.map(u => u.toUpperCase())) : null;
  const chave = (e: EditalDetalhe) =>
    e.data_encerramento_proposta === null ? Infinity : instante(e.data_encerramento_proposta);
  return dados.editais
    .filter(e => linha === undefined || e.linhas.includes(linha))
    .filter(e => ufsPedidas === null || (e.uf !== null && ufsPedidas.has(e.uf.toUpperCase())))
    .filter(e => todos || estaAberto(e, agora))
    .filter(
      e =>
        encerraEmDias === undefined ||
        (e.data_encerramento_proposta !== null &&
          diasAteEncerrar(e.data_encerramento_proposta, agora) <= encerraEmDias),
    )
    .filter(e => !novos || ehNovo(e.primeiro_visto_em ?? null, agora))
    .filter(
      e =>
        vereditos === undefined ||
        vereditos.length === 0 ||
        vereditos.includes(e.veredito ?? 'sem_analise'),
    )
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
      perfil_exemplo: true,
    }),
    painel: async (): Promise<Painel> => {
      const hoje = agora();
      const base = selecionar(dados, {}, hoje);
      const decidir = base.filter(
        e =>
          e.data_encerramento_proposta !== null &&
          diasAteEncerrar(e.data_encerramento_proposta, hoje) <= DIAS_DECIDIR,
      );
      const adiante = base.filter(e => !decidir.includes(e));
      return {
        atualizado_em: dados.atualizado_em,
        perfil_exemplo: true,
        coleta: [],
        contadores: {
          abertos: base.length,
          decidir: decidir.length,
          novos: base.filter(e => ehNovo(e.primeiro_visto_em ?? null, hoje)).length,
        },
        secoes: [
          {chave: 'decidir', total: decidir.length, editais: decidir.slice(0, LIMITE_SECAO)},
          {chave: 'mais_adiante', total: adiante.length, editais: adiante.slice(0, LIMITE_SECAO)},
        ],
      };
    },
    obter: async id => dados.editais.find(e => e.id_publico === id) ?? null,
    // O exemplo não traz análise por IA: o detalhe mostra "ainda não analisado".
    analise: async () => null,
    // Nem histórico do órgão: o detalhe mostra "sem histórico".
    historico: async () => null,
    // Nem preços de referência: o detalhe mostra "sem preços".
    precos: async () => null,
    status: async () => ({atualizado_em: dados.atualizado_em}),
  };
}

export const fonteExemplo = criarFonteExemplo();
