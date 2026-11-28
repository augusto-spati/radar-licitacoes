import type {
  Analise,
  EditalDetalhe,
  FiltroVeredito,
  Historico,
  Linha,
  Lista,
  Painel,
  Precos,
  Status,
  UF,
} from './tipos';
import {fonteApi} from './fonteApi';
import {fonteExemplo} from './fonteExemplo';

export type Filtros = {
  linha?: Linha;
  ufs?: UF[];
  todos?: boolean;
  /** Encerra em até N dias de calendário (Brasília), contando hoje. */
  encerraEmDias?: number;
  /** Só os vistos pela primeira vez nos últimos dias. */
  novos?: boolean;
  vereditos?: FiltroVeredito[];
};

export interface Fonte {
  listar(f: Filtros): Promise<Lista>;
  obter(id: string): Promise<EditalDetalhe | null>;
  /** Análise por IA do edital; null quando ele ainda não foi analisado. */
  analise(id: string): Promise<Analise | null>;
  /** Histórico do órgão do edital (vazio sem contratações); null se o edital não existe. */
  historico(id: string): Promise<Historico | null>;
  /** Preços de referência das linhas do edital (lista vazia sem casos); null se o edital não existe. */
  precos(id: string): Promise<Precos | null>;
  status(): Promise<Status>;
  /** Home: Decidir e Mais adiante, contadores da base e falhas da coleta. */
  painel(): Promise<Painel>;
}

// VITE_FONTE='api' usa a API; qualquer outro valor (ou nenhum) usa o exemplo.
export const fonte: Fonte = import.meta.env.VITE_FONTE === 'api' ? fonteApi : fonteExemplo;
