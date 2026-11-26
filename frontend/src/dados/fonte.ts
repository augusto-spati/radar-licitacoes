import type {EditalDetalhe, Linha, Lista, Status, UF} from './tipos';
import {fonteApi} from './fonteApi';
import {fonteExemplo} from './fonteExemplo';

export type Filtros = {linha?: Linha; ufs?: UF[]; todos?: boolean};

export interface Fonte {
  listar(f: Filtros): Promise<Lista>;
  obter(id: string): Promise<EditalDetalhe | null>;
  status(): Promise<Status>;
}

// VITE_FONTE='api' usa a API; qualquer outro valor (ou nenhum) usa o exemplo.
export const fonte: Fonte = import.meta.env.VITE_FONTE === 'api' ? fonteApi : fonteExemplo;
