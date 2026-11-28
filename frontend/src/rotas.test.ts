import {describe, expect, it} from 'vitest';
import {rotas} from './rotas';

describe('rotas', () => {
  it('formam a lista fechada da v1', () => {
    expect(rotas.map(rota => rota.caminho)).toEqual(['/', '/editais', '/edital/:id', '/entrar', '*']);
  });

  it('só o painel e a lista aparecem no menu, nessa ordem', () => {
    expect(rotas.filter(rota => rota.noMenu).map(rota => rota.caminho)).toEqual(['/', '/editais']);
  });
});
