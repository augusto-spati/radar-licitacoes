// @vitest-environment jsdom
import {cleanup, fireEvent, screen} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {edital, fonteFalsa, renderizar} from '../testes/apoio';
import {Detalhe} from './Detalhe';

afterEach(cleanup);

const ID = 'pncp~13127340000120-1-000036/2025';
// A lista navega com o id codificado num segmento só (%2F no lugar da barra).
const abrir = (obter: (id: string) => Promise<ReturnType<typeof edital> | null>, consulta = '') =>
  renderizar(<Detalhe />, {
    fonte: fonteFalsa({obter}),
    endereco: `/edital/${encodeURIComponent(ID)}${consulta}`,
    padrao: '/edital/:id',
  });

const valorDe = (rotulo: string) =>
  screen.getByText(rotulo, {selector: 'dt'}).nextElementSibling?.textContent;

describe('Detalhe', () => {
  it('detalhe_com_barra_no_id_abre', async () => {
    const obter = vi.fn(async () => edital());
    abrir(obter, '?linha=redes&uf=SP');

    expect(await screen.findByText('Execução de rede de esgoto no bairro Centro')).toBeTruthy();
    expect(obter).toHaveBeenCalledWith(ID);
    expect(screen.getByRole('heading', {level: 1}).textContent).toBe(
      'SERVIÇO AUTÔNOMO DE ÁGUA E ESGOTO',
    );
    expect(valorDe('CNPJ do órgão')).toBe('13.127.340/0001-20');
    expect(valorDe('Município/UF')).toBe('Holambra/SP');
    expect(valorDe('Modalidade')).toBe('Concorrência - Eletrônica');
    expect(valorDe('Fonte')).toBe('PNCP');
    expect(valorDe('Identificador na fonte')).toBe('13127340000120-1-000036/2025');
    expect(valorDe('Situação')).toBe('Divulgada no PNCP');
    expect(valorDe('Valor estimado')).toBe('R$ 845 mil');
    expect(valorDe('Publicação')).toBe('20/11/2026 10:16');
    expect(valorDe('Abertura das propostas')).toBe('21/11/2026 08:00');
    expect(valorDe('Encerramento das propostas')).toBe('10/12/2026 08:00');
    expect(screen.getByText('Redes')).toBeTruthy();

    const origem = screen.getByRole('link', {name: /Abrir no sistema de origem/});
    expect(origem.getAttribute('href')).toBe('https://compras.exemplo.gov.br/edital/36');
    expect(origem.getAttribute('target')).toBe('_blank');
    expect(origem.getAttribute('rel')).toContain('noopener');

    expect(screen.getByText('Aderência à FR — em breve')).toBeTruthy();
    // "Voltar" leva os filtros de volta para a lista.
    expect(screen.getByRole('link', {name: /Voltar/}).getAttribute('href')).toBe(
      '/?linha=redes&uf=SP',
    );
  });

  it('detalhe_campos_nulos_mostram_traco', async () => {
    abrir(async () =>
      edital({
        uf: null,
        municipio: null,
        valor_estimado: null,
        data_abertura_proposta: null,
        data_encerramento_proposta: null,
        link_origem: null,
        linhas: [],
      }),
    );

    await screen.findByText('Execução de rede de esgoto no bairro Centro');
    expect(valorDe('Município/UF')).toBe('—');
    expect(valorDe('Valor estimado')).toBe('—');
    expect(valorDe('Abertura das propostas')).toBe('—');
    expect(valorDe('Encerramento das propostas')).toBe('—');
    expect(valorDe('Linhas de serviço')).toBe('—');
    // Sem link (a API já manda o de reserva): o front não monta um por conta própria.
    expect(screen.queryByRole('link', {name: /Abrir no/})).toBeNull();
    expect(screen.getByRole('link', {name: /Voltar/}).getAttribute('href')).toBe('/');
  });

  it.each(['', '   ', 'javascript:alert(1)', 'www.compras.gov.br/36'])(
    'link %j (vazio ou sem http) não vira botão: o front não monta link por conta própria',
    async link => {
      abrir(async () => edital({link_origem: link}));
      await screen.findByText('Execução de rede de esgoto no bairro Centro');
      expect(screen.queryByRole('link', {name: /Abrir no/})).toBeNull();
    },
  );

  it('detalhe_sanepar_com_barra_no_id_abre', async () => {
    const obter = vi.fn(async () =>
      edital({
        fonte: 'sanepar',
        id_publico: 'sanepar~OE-345/26',
        link_origem: 'https://www.sanepar.com.br/licitacao/detalhe/345/26',
      }),
    );
    renderizar(<Detalhe />, {
      fonte: fonteFalsa({obter}),
      endereco: `/edital/${encodeURIComponent('sanepar~OE-345/26')}`,
      padrao: '/edital/:id',
    });

    expect(await screen.findByText('Execução de rede de esgoto no bairro Centro')).toBeTruthy();
    expect(obter).toHaveBeenCalledWith('sanepar~OE-345/26');
    expect(valorDe('Fonte')).toBe('Sanepar');
    expect(valorDe('Identificador na fonte')).toBe('OE-345/26');
    expect(
      screen.getByRole('link', {name: /Abrir no sistema de origem/}).getAttribute('href'),
    ).toBe('https://www.sanepar.com.br/licitacao/detalhe/345/26');
  });

  it('detalhe_inexistente_mostra_nao_encontrado', async () => {
    abrir(async () => null);
    expect(await screen.findByText('Edital não encontrado')).toBeTruthy();
    expect(screen.getByRole('link', {name: /Voltar/})).toBeTruthy();
  });

  it('erro ao carregar oferece tentar de novo, que repete a chamada', async () => {
    const obter = vi
      .fn()
      .mockRejectedValueOnce(new Error('A API respondeu 500'))
      .mockResolvedValueOnce(edital());
    abrir(obter);
    expect(await screen.findByText('Não foi possível carregar o edital')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', {name: 'Tentar de novo'}));

    expect(await screen.findByText('Execução de rede de esgoto no bairro Centro')).toBeTruthy();
    expect(obter).toHaveBeenCalledTimes(2);
    expect(obter).toHaveBeenLastCalledWith(ID);
    // O título visível some (a região viva do Astryx pode guardar o anúncio).
    expect(screen.queryByRole('heading', {name: 'Não foi possível carregar o edital'})).toBeNull();
  });

  it('mostra o esqueleto enquanto carrega', () => {
    abrir(() => new Promise(() => {}));
    expect(screen.getByRole('status', {name: 'Carregando edital'})).toBeTruthy();
  });
});
