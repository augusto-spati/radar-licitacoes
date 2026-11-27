import {describe, expect, it} from 'vitest';
import {tituloDaRota} from './App';

describe('título da aba', () => {
  // O id do edital chega com a barra codificada (%2F), como a lista navega.
  it('muda com a rota e mostra a 404 fora delas', () => {
    expect(tituloDaRota('/')).toBe('Editais · Radar FR');
    expect(tituloDaRota('/edital/pncp~123-1-000001%2F2026')).toBe('Edital · Radar FR');
    expect(tituloDaRota('/entrar')).toBe('Entrar · Radar FR');
    expect(tituloDaRota('/nao-existe')).toBe('Página não encontrada · Radar FR');
  });
});
