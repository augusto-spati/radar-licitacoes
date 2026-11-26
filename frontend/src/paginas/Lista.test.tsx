// @vitest-environment jsdom
import {cleanup, fireEvent, screen, within} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import type {Fonte} from '../dados/fonte';
import {edital, fonteFalsa, renderizar} from '../testes/apoio';
import {Lista} from './Lista';

// 26/11/2026 12:00 em Brasília; só o Date é falso, os timers seguem reais.
const AGORA = new Date('2026-11-26T12:00:00-03:00');

beforeEach(() => {
  vi.useFakeTimers({toFake: ['Date']});
  vi.setSystemTime(AGORA);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const recente = '2026-11-26T09:00:00-03:00';
const cartoes = () => screen.getAllByRole('link', {name: /^Abrir edital/});
const endereco = () => screen.getByRole('status', {name: 'endereço'}).textContent;

describe('Lista', () => {
  it('lista_mostra_cartoes_ordenados', async () => {
    const primeiro = edital({
      numero_controle_pncp: '13127340000120-1-000036/2025',
      objeto: 'Rede de esgoto',
      data_encerramento_proposta: '2026-11-27T08:00:00',
      linhas: ['redes', 'perdas'],
    });
    const segundo = edital({
      numero_controle_pncp: '46189726000115-1-000075/2026',
      objeto: 'Controle de perdas',
      municipio: null,
      uf: null,
      valor_estimado: null,
      modalidade_id: 6,
      data_encerramento_proposta: '2026-12-10T08:00:00',
      linhas: [],
    });
    renderizar(<Lista />, {
      fonte: fonteFalsa({listar: async () => ({atualizado_em: recente, editais: [primeiro, segundo]})}),
    });

    expect(await screen.findByText('Rede de esgoto')).toBeTruthy();
    const [a, b] = cartoes();
    expect(cartoes()).toHaveLength(2);
    // A ordem é a da fonte (encerramento crescente), na tela de cima para baixo.
    expect(a.getAttribute('aria-label')).toContain('Rede de esgoto');
    expect(b.getAttribute('aria-label')).toContain('Controle de perdas');
    // O id tem barra: vai num segmento só, codificado.
    expect(a.getAttribute('href')).toBe('/edital/13127340000120-1-000036%2F2025');

    expect(screen.getByText('encerra amanhã · 27/11')).toBeTruthy();
    expect(screen.getByText('encerra em 14 dias · 10/12')).toBeTruthy();
    expect(screen.getByText('Holambra/SP')).toBeTruthy();
    expect(screen.getByText('Concorrência eletrônica')).toBeTruthy();
    expect(screen.getByText('Pregão eletrônico')).toBeTruthy();
    expect(screen.getByText('R$ 845 mil')).toBeTruthy();
    // Tokens das linhas dentro do cartão (os filtros também dizem Redes/Perdas).
    const cartaoA = within(a.parentElement as HTMLElement);
    expect(cartaoA.getByText('Redes')).toBeTruthy();
    expect(cartaoA.getByText('Perdas')).toBeTruthy();
    // Sem município, UF e valor: travessão, sem quebrar.
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText(/desatualizad/)).toBeNull();
  });

  it('lê os filtros da URL, repassa à fonte e leva a consulta ao detalhe', async () => {
    const listar = vi.fn<Fonte['listar']>(async () => ({atualizado_em: recente, editais: [edital()]}));
    renderizar(<Lista />, {fonte: fonteFalsa({listar}), endereco: '/?linha=redes&uf=SP&uf=PR'});

    await screen.findByText('Execução de rede de esgoto no bairro Centro');
    expect(listar).toHaveBeenLastCalledWith({linha: 'redes', ufs: ['SP', 'PR']});
    expect(cartoes()[0].getAttribute('href')).toBe(
      '/edital/13127340000120-1-000036%2F2025?linha=redes&uf=SP&uf=PR',
    );
  });

  it('trocar a linha atualiza a URL e recarrega', async () => {
    const listar = vi.fn<Fonte['listar']>(async () => ({atualizado_em: recente, editais: [edital()]}));
    renderizar(<Lista />, {fonte: fonteFalsa({listar}), endereco: '/?uf=SP'});
    await screen.findByText('Execução de rede de esgoto no bairro Centro');

    const grupo = screen.getByRole('group', {name: 'Linha de serviço'});
    fireEvent.click(within(grupo).getByRole('button', {name: 'Perdas'}));

    await vi.waitFor(() => expect(listar).toHaveBeenLastCalledWith({linha: 'perdas', ufs: ['SP']}));
    expect(endereco()).toBe('/?uf=SP&linha=perdas');

    fireEvent.click(within(grupo).getByRole('button', {name: 'Todas'}));
    await vi.waitFor(() => expect(listar).toHaveBeenLastCalledWith({ufs: ['SP']}));
    expect(endereco()).toBe('/?uf=SP');
  });

  it('lista_vazia_mostra_estado_vazio', async () => {
    renderizar(<Lista />, {
      fonte: fonteFalsa({listar: async () => ({atualizado_em: recente, editais: []})}),
    });
    expect(await screen.findByText('Nenhum edital aberto com esses filtros')).toBeTruthy();
  });

  it('mostra o esqueleto enquanto carrega', () => {
    renderizar(<Lista />, {fonte: fonteFalsa({listar: () => new Promise(() => {})})});
    expect(screen.getByLabelText('Carregando editais')).toBeTruthy();
  });

  it('lista_erro_mostra_tentar_de_novo_e_recarrega', async () => {
    const listar = vi
      .fn()
      .mockRejectedValueOnce(new Error('A API respondeu 500'))
      .mockResolvedValueOnce({atualizado_em: recente, editais: [edital()]});
    renderizar(<Lista />, {fonte: fonteFalsa({listar})});

    expect(await screen.findByText('Não foi possível carregar os editais')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', {name: 'Tentar de novo'}));

    expect(await screen.findByText('Execução de rede de esgoto no bairro Centro')).toBeTruthy();
    expect(listar).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('Não foi possível carregar os editais')).toBeNull();
  });

  it('lista_desatualizada_mostra_aviso', async () => {
    renderizar(<Lista />, {
      fonte: fonteFalsa({listar: async () => ({atualizado_em: null, editais: [edital()]})}),
    });
    expect(await screen.findByText('Os dados podem estar desatualizados')).toBeTruthy();
    expect(screen.getByText('Nenhuma coleta foi concluída ainda.')).toBeTruthy();
  });

  it('aviso de desatualizado diz quando foi a última coleta', async () => {
    renderizar(<Lista />, {
      fonte: fonteFalsa({
        listar: async () => ({atualizado_em: '2026-11-24T08:30:00-03:00', editais: [edital()]}),
      }),
    });
    expect(await screen.findByText('Última atualização em 24/11/2026 08:30.')).toBeTruthy();
  });
});
