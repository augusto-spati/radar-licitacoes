// @vitest-environment jsdom
import {cleanup, fireEvent, screen} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {Fonte} from '../dados/fonte';
import type {Analise} from '../dados/tipos';
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

    expect(screen.getByRole('heading', {level: 2, name: 'Aderência à FR'})).toBeTruthy();
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

const analise = (parcial: Partial<Analise> = {}): Analise => ({
  id_publico: ID,
  modelo: 'amazon.nova-lite-v1:0',
  versao_prompt: 'v1',
  criada_em: '2026-11-27T12:05:00+00:00',
  veredito: 'verificar',
  incompleta: false,
  aviso: 'Segundo o que foi extraído do edital por IA.',
  itens: [
    {
      campo: 'capital_social_minimo',
      situacao: 'atende',
      exigido: 'R$ 100.000,00',
      perfil: 'R$ 500.000,00',
      motivo: 'perfil ≥ exigido',
      trecho: 'capital social mínimo de R$ 100.000,00',
      documento: 'Edital',
      pagina: 12,
    },
    {
      campo: 'atestados',
      situacao: 'verificar',
      exigido: 'rede de esgoto (500 m)',
      perfil: 'nenhum atestado do perfil casa',
      motivo: 'casamento por termo é aproximado: confira',
      trecho: null,
      documento: null,
      pagina: null,
    },
  ],
  documentos: [
    {
      titulo: 'Edital',
      url: 'https://pncp.gov.br/pncp-api/v1/arquivo/1',
      paginas: 40,
      status: 'extraido',
    },
    {
      titulo: 'Projeto',
      url: 'https://pncp.gov.br/pncp-api/v1/arquivo/2',
      paginas: null,
      status: 'sem_texto',
    },
  ],
  ...parcial,
});

describe('Detalhe: aderência (análise por IA)', () => {
  const abrirCom = (analiseFn: Fonte['analise']) =>
    renderizar(<Detalhe />, {
      fonte: fonteFalsa({obter: async () => edital(), analise: analiseFn}),
      endereco: `/edital/${encodeURIComponent(ID)}`,
      padrao: '/edital/:id',
    });

  it('sem análise diz que ainda não foi analisado', async () => {
    const pedir = vi.fn(async () => null);
    abrirCom(pedir);
    expect(await screen.findByText(/Este edital ainda não foi analisado/)).toBeTruthy();
    expect(pedir).toHaveBeenCalledWith(ID);
  });

  it('com análise mostra veredito, exigências com trecho e página, e documentos', async () => {
    abrirCom(async () => analise());
    expect(await screen.findByText('Verificar antes de participar')).toBeTruthy();
    expect(screen.getByText('Capital social mínimo')).toBeTruthy();
    expect(screen.getByText('Atende')).toBeTruthy();
    expect(screen.getByText('Exigido: R$ 100.000,00')).toBeTruthy();
    expect(
      screen.getByText('“capital social mínimo de R$ 100.000,00” (Edital, página 12)'),
    ).toBeTruthy();
    expect(screen.getByText('Atestado técnico')).toBeTruthy();
    expect(screen.getByText('Verificar')).toBeTruthy();
    const doc = screen.getByRole('link', {name: 'Edital (40 páginas)'});
    expect(doc.getAttribute('href')).toBe('https://pncp.gov.br/pncp-api/v1/arquivo/1');
    expect(doc.getAttribute('target')).toBe('_blank');
    expect(screen.getByText('sem texto (PDF escaneado)')).toBeTruthy();
    expect(
      screen.getByText(/Analisado em 27\/11\/2026 09:05 por amazon.nova-lite-v1:0/),
    ).toBeTruthy();
  });

  it('veredito inapto e análise incompleta aparecem no aviso', async () => {
    abrirCom(async () => analise({veredito: 'inapto', incompleta: true, itens: []}));
    expect(await screen.findByText('Não atende ao perfil da FR')).toBeTruthy();
    expect(screen.getByText(/Análise incompleta/)).toBeTruthy();
    expect(screen.getByText(/A IA não achou exigência/)).toBeTruthy();
  });

  it('campo desconhecido aparece com o nome da API', async () => {
    const [item] = analise().itens;
    abrirCom(async () => analise({veredito: 'apto', itens: [{...item, campo: 'campo_novo'}]}));
    expect(await screen.findByText('Atende ao perfil da FR')).toBeTruthy();
    expect(screen.getByText('campo_novo')).toBeTruthy();
  });

  it('erro na análise não derruba o edital e oferece tentar de novo', async () => {
    const pedir = vi
      .fn<Fonte['analise']>()
      .mockRejectedValueOnce(new Error('A API respondeu 500'))
      .mockResolvedValueOnce(null);
    abrirCom(pedir);
    expect(await screen.findByText('Não foi possível carregar a análise')).toBeTruthy();
    expect(screen.getByText('Execução de rede de esgoto no bairro Centro')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', {name: 'Tentar de novo'}));
    expect(await screen.findByText(/Este edital ainda não foi analisado/)).toBeTruthy();
    expect(pedir).toHaveBeenCalledTimes(2);
  });
});
