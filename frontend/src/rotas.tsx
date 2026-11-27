import {type ComponentType, lazy} from 'react';
import {Lista} from './paginas/Lista';

export type Rota = {
  caminho: string;
  nome: string;
  noMenu: boolean;
  elemento: ComponentType;
};

// A lista é a porta de entrada e vem no pacote inicial (sem ida e volta a
// mais). Detalhe e login viram pedaços próprios do build (import dinâmico):
// quem abre a lista não baixa os dois. O App põe o <Suspense> em volta.
const Detalhe = lazy(() => import('./paginas/Detalhe').then(m => ({default: m.Detalhe})));
const Entrar = lazy(() => import('./paginas/Entrar').then(m => ({default: m.Entrar})));

// Lista fechada das rotas da v1. É o único arquivo que importa páginas e
// também alimenta o menu do Shell. Rota nova só com pedido explícito.
export const rotas: readonly Rota[] = [
  {caminho: '/', nome: 'Editais', noMenu: true, elemento: Lista},
  {caminho: '/edital/:id', nome: 'Edital', noMenu: false, elemento: Detalhe},
  {caminho: '/entrar', nome: 'Entrar', noMenu: false, elemento: Entrar},
];
