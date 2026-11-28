// @vitest-environment jsdom
import {Suspense, createElement} from 'react';
import {cleanup, fireEvent, screen} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {tituloDaRota} from './App';
import type {Fonte} from './dados/fonte';
import {rotas} from './rotas';
import {edital, fonteFalsa, renderizar} from './testes/apoio';

afterEach(cleanup);

describe('título da aba', () => {
  // O id do edital chega com a barra codificada (%2F), como a lista navega.
  it('muda com a rota e mostra a 404 fora delas', () => {
    expect(tituloDaRota('/')).toBe('Painel · Radar FR');
    expect(tituloDaRota('/editais')).toBe('Editais · Radar FR');
    expect(tituloDaRota('/edital/pncp~123-1-000001%2F2026')).toBe('Edital · Radar FR');
    expect(tituloDaRota('/entrar')).toBe('Entrar · Radar FR');
    expect(tituloDaRota('/nao-existe')).toBe('Página não encontrada · Radar FR');
  });
});

const tela = (caminho: string) => {
  const rota = rotas.find(r => r.caminho === caminho);
  if (rota === undefined) throw new Error(caminho);
  return <Suspense fallback={null}>{createElement(rota.elemento)}</Suspense>;
};

describe('rotas reais', () => {
  it('link_antigo_da_lista_abre_a_lista_filtrada_e_o_voltar_do_detalhe_volta_para_ela', async () => {
    const listar = vi.fn<Fonte['listar']>(async () => ({
      atualizado_em: new Date().toISOString(),
      perfil_exemplo: false,
      editais: [edital()],
    }));
    renderizar(tela('/'), {
      fonte: fonteFalsa({listar, obter: async () => edital()}),
      endereco: '/?linha=redes&uf=SP',
      outras: {'/editais': tela('/editais'), '/edital/:id': tela('/edital/:id')},
    });

    await screen.findByText('Execução de rede de esgoto no bairro Centro');
    expect(screen.getByRole('status', {name: 'endereço'}).textContent).toBe(
      '/editais?linha=redes&uf=SP',
    );
    expect(listar).toHaveBeenLastCalledWith({linha: 'redes', ufs: ['SP']});

    fireEvent.click(screen.getAllByRole('link', {name: /^Abrir edital/})[0]);
    const voltar = await screen.findByRole('link', {name: /Voltar/});
    expect(voltar.getAttribute('href')).toBe('/editais?linha=redes&uf=SP');
  });
});
