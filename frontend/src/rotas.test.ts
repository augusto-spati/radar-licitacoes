import {describe, expect, it} from 'vitest';
import {rotas} from './rotas';

describe('rotas', () => {
  it('formam a lista fechada da v1', () => {
    expect(rotas.map(rota => rota.caminho)).toEqual(['/', '/edital/:id', '/entrar']);
  });

  it('só a lista aparece no menu', () => {
    expect(rotas.filter(rota => rota.noMenu).map(rota => rota.caminho)).toEqual(['/']);
  });
});
