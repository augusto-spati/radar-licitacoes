import type {JSX} from 'react';
import {Detalhe} from './paginas/Detalhe';
import {Entrar} from './paginas/Entrar';
import {Lista} from './paginas/Lista';

export type Rota = {
  caminho: string;
  nome: string;
  noMenu: boolean;
  elemento: () => JSX.Element;
};

// Lista fechada das rotas da v1. É o único arquivo que importa páginas e
// também alimenta o menu do Shell. Rota nova só com pedido explícito.
export const rotas: readonly Rota[] = [
  {caminho: '/', nome: 'Editais', noMenu: true, elemento: Lista},
  {caminho: '/edital/:id', nome: 'Edital', noMenu: false, elemento: Detalhe},
  {caminho: '/entrar', nome: 'Entrar', noMenu: false, elemento: Entrar},
];
