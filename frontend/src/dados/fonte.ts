import type {Analise, EditalDetalhe, Historico, Linha, Lista, Precos, Status, UF} from './tipos';
import {fonteApi} from './fonteApi';
import {fonteExemplo} from './fonteExemplo';

export type Filtros = {linha?: Linha; ufs?: UF[]; todos?: boolean};

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
}

// VITE_FONTE='api' usa a API; qualquer outro valor (ou nenhum) usa o exemplo.
export const fonte: Fonte = import.meta.env.VITE_FONTE === 'api' ? fonteApi : fonteExemplo;
