// @vitest-environment jsdom
import {cleanup, fireEvent, screen} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {Fonte} from '../dados/fonte';
import type {Analise, Historico, ReferenciaPreco} from '../dados/tipos';
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

const historico = (parcial: Partial<Historico> = {}): Historico => ({
  orgao_cnpj: '13127340000120',
  orgao_nome: 'SERVIÇO AUTÔNOMO DE ÁGUA E ESGOTO',
  contagens: {homologada: 7, parcial: 1, fracassada: 2, deserta: 1, em_andamento: 1},
  total: 12,
  ultimas: [
    {
      id_publico: 'pncp~13127340000120-1-000010/2025',
      objeto: 'Rede de água no bairro Norte',
      data: '2026-05-10T08:00:00',
      situacao: 'homologada',
      valor_estimado: '2000000',
      valor_homologado: '1520000',
      desconto: '0.24',
      parcial: false,
      vencedor_nome: 'Construtora X',
      vencedor_porte: 'ME',
    },
    {
      id_publico: 'pncp~13127340000120-1-000011/2025',
      objeto: 'Ligações prediais de esgoto',
      data: null,
      situacao: 'parcial',
      valor_estimado: '500000',
      valor_homologado: null,
      desconto: null,
      parcial: true,
      vencedor_nome: null,
      vencedor_porte: null,
    },
  ],
  vencedores: [
    {nome: 'Construtora X', porte: 'ME', vitorias: 3, desconto_mediano: '0.2415'},
    {nome: 'Y Engenharia', porte: null, vitorias: 1, desconto_mediano: null},
  ],
  reguas: [
    {
      linha: 'redes',
      uf: 'SP',
      faixa: '1m_a_5m',
      casos: 14,
      p25: '0.18',
      mediana: '0.24',
      p75: '0.31',
      poucos_casos: false,
    },
  ],
  limite_inexequivel: '0.75',
  ...parcial,
});

const regua = (parcial: Partial<Historico['reguas'][number]>) => ({
  linha: 'perdas',
  uf: null,
  faixa: null,
  casos: 0,
  p25: null,
  mediana: null,
  p75: null,
  poucos_casos: true,
  ...parcial,
});

describe('Detalhe: histórico do órgão', () => {
  const abrirCom = (historicoFn: Fonte['historico']) =>
    renderizar(<Detalhe />, {
      fonte: fonteFalsa({obter: async () => edital(), historico: historicoFn}),
      endereco: `/edital/${encodeURIComponent(ID)}`,
      padrao: '/edital/:id',
    });

  it('com histórico mostra contagens, quem vence, últimas, régua e limite', async () => {
    const pedir = vi.fn(async () => historico());
    abrirCom(pedir);
    expect(
      await screen.findByText(
        '12 licitações · 7 homologadas · 3 fracassadas ou desertas · 2 em andamento',
      ),
    ).toBeTruthy();
    expect(pedir).toHaveBeenCalledWith(ID);
    expect(screen.getByRole('heading', {level: 2, name: 'Histórico do órgão'})).toBeTruthy();
    expect(screen.getByRole('heading', {level: 3, name: 'Quem vence'})).toBeTruthy();
    expect(screen.getByText('ME · 3 vitórias · desconto mediano 24%')).toBeTruthy();
    expect(screen.getByText('Y Engenharia')).toBeTruthy();
    expect(screen.getByText('1 vitória')).toBeTruthy();
    expect(screen.getByText('Rede de água no bairro Norte')).toBeTruthy();
    expect(
      screen.getByText('10/05/2026 · estimado R$ 2,00 mi · homologado R$ 1,52 mi · desconto 24%'),
    ).toBeTruthy();
    expect(screen.getByText('Vencedor: Construtora X (ME)')).toBeTruthy();
    // Sem data, homologado nem desconto: só o que veio, sem pares de travessão.
    expect(screen.getByText('estimado R$ 500 mil')).toBeTruthy();
    expect(screen.getByText('parcial')).toBeTruthy();
    expect(
      screen.getByText(
        'Faixa de referência do desconto dos vencedores (Redes, SP, R$ 1–5 mi): ' +
          '18% a 31% (mediana 24%), 14 casos',
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        'É o que os vencedores deram em licitações parecidas, não o preço que ganha.',
      ),
    ).toBeTruthy();
    expect(screen.queryByText('poucos casos')).toBeNull();
    expect(
      screen.getByText('Abaixo de 75% do estimado a proposta é presumida inexequível.'),
    ).toBeTruthy();
  });

  it('poucos casos é marcado, a régua de todas as UFs vem junto e o limite de estatal', async () => {
    abrirCom(async () =>
      historico({
        reguas: [
          regua({uf: 'PR', faixa: 'acima_5m', casos: 3, p25: '0.1', mediana: '0.12', p75: '0.2'}),
          regua({}),
        ],
        limite_inexequivel: '0.70',
      }),
    );
    expect(
      await screen.findByText(
        'Faixa de referência do desconto dos vencedores (Perdas, PR, acima de R$ 5 mi): ' +
          '10% a 20% (mediana 12%), 3 casos',
      ),
    ).toBeTruthy();
    expect(screen.getAllByText('poucos casos')).toHaveLength(1);
    expect(
      screen.getByText(
        'Faixa de referência do desconto dos vencedores ' +
          '(Perdas, todas as UFs, qualquer valor): nenhum caso',
      ),
    ).toBeTruthy();
    expect(
      screen.getByText('Abaixo de 70% do estimado a proposta é presumida inexequível.'),
    ).toBeTruthy();
  });

  it('fracassada mostra só data e estimado; régua sem quartis não mostra "— a —"', async () => {
    const [primeira] = historico().ultimas;
    abrirCom(async () =>
      historico({
        ultimas: [
          {
            ...primeira,
            situacao: 'fracassada',
            data: '2026-05-20T08:00:00',
            valor_estimado: '450000',
            valor_homologado: null,
            desconto: null,
            vencedor_nome: null,
            vencedor_porte: null,
          },
        ],
        reguas: [regua({casos: 6, poucos_casos: false})],
      }),
    );
    expect(await screen.findByText('20/05/2026 · estimado R$ 450 mil')).toBeTruthy();
    expect(screen.getByText('fracassada')).toBeTruthy();
    expect(
      screen.getByText(
        'Faixa de referência do desconto dos vencedores ' +
          '(Perdas, todas as UFs, qualquer valor): 6 casos, sem desconto calculável',
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/— a —/)).toBeNull();
  });

  it('com contagens mas sem últimas nem vencedores, mostra as contagens sem listas vazias', async () => {
    abrirCom(async () => historico({ultimas: [], vencedores: []}));
    expect(
      await screen.findByText(
        '12 licitações · 7 homologadas · 3 fracassadas ou desertas · 2 em andamento',
      ),
    ).toBeTruthy();
    expect(screen.queryByRole('heading', {name: 'Quem vence'})).toBeNull();
    expect(screen.queryByRole('heading', {name: 'Últimas contratações'})).toBeNull();
    expect(screen.getByText(/Abaixo de 75% do estimado/)).toBeTruthy();
  });

  it('mais de um vencedor "Pessoa física" aparece sem chave repetida', async () => {
    const erro = vi.spyOn(console, 'error').mockImplementation(() => {});
    abrirCom(async () =>
      historico({
        vencedores: [
          {nome: 'Pessoa física', porte: null, vitorias: 1, desconto_mediano: '0.10'},
          {nome: 'Pessoa física', porte: null, vitorias: 1, desconto_mediano: '0.20'},
        ],
      }),
    );
    expect(await screen.findByText('1 vitória · desconto mediano 10%')).toBeTruthy();
    expect(screen.getByText('1 vitória · desconto mediano 20%')).toBeTruthy();
    expect(screen.getAllByText('Pessoa física')).toHaveLength(2);
    const chaves = erro.mock.calls.filter(chamada => String(chamada[0]).includes('same key'));
    erro.mockRestore();
    expect(chaves).toEqual([]);
  });

  it('órgão sem contratações mostra a frase de vazio', async () => {
    abrirCom(async () =>
      historico({contagens: {}, total: 0, ultimas: [], vencedores: [], reguas: []}),
    );
    expect(await screen.findByText('Sem histórico deste órgão no recorte.')).toBeTruthy();
    expect(screen.queryByText('Quem vence')).toBeNull();
  });

  it('erro no histórico não derruba o edital e oferece tentar de novo', async () => {
    const pedir = vi
      .fn<Fonte['historico']>()
      .mockRejectedValueOnce(new Error('A API respondeu 500'))
      .mockResolvedValueOnce(historico());
    abrirCom(pedir);
    expect(await screen.findByText('Não foi possível carregar o histórico')).toBeTruthy();
    expect(screen.getByText('Execução de rede de esgoto no bairro Centro')).toBeTruthy();
    expect(screen.getByRole('heading', {level: 2, name: 'Aderência à FR'})).toBeTruthy();
    fireEvent.click(screen.getByRole('button', {name: 'Tentar de novo'}));
    expect(await screen.findByText(/^12 licitações/)).toBeTruthy();
    expect(pedir).toHaveBeenCalledTimes(2);
  });
});

const referencia = (parcial: Partial<ReferenciaPreco> = {}): ReferenciaPreco => ({
  servico: 'rede_agua',
  rotulo: 'Rede de água',
  linha: 'redes',
  material: 'pvc',
  dn: 150,
  escopo: 'completo',
  faixa: null,
  unidade: 'm',
  abrangencia: 'uf',
  uf: 'PE',
  casos_estimado: 14,
  mediana_estimado: '182.40',
  casos_contratado: 9,
  mediana_contratado: '151.10',
  data_base_inicio: '2025-05-01',
  data_base_fim: '2025-06-01',
  ...parcial,
});

describe('Detalhe: preços de referência', () => {
  const abrirCom = (precos: Fonte['precos']) =>
    renderizar(<Detalhe />, {
      fonte: fonteFalsa({obter: async () => edital({uf: 'PE'}), precos}),
      endereco: `/edital/${encodeURIComponent(ID)}`,
      padrao: '/edital/:id',
    });

  it('mostra estimado e contratado em linhas separadas, com origem, período e rodapé', async () => {
    const pedir = vi.fn(async () => ({uf: 'PE', referencias: [referencia()]}));
    abrirCom(pedir);
    expect(
      await screen.findByText('Rede de água · PVC DN 150 · fornecimento e assentamento (m)'),
    ).toBeTruthy();
    expect(pedir).toHaveBeenCalledWith(ID);
    expect(screen.getByRole('heading', {level: 2, name: 'Preços de referência'})).toBeTruthy();
    expect(screen.getByText('Estimado pelo órgão: R$ 182,40/m (14 casos, PE)')).toBeTruthy();
    expect(screen.getByText('Contratado: R$ 151,10/m (9 casos, PE)')).toBeTruthy();
    expect(screen.getByText('preços de 05/2025 a 06/2025')).toBeTruthy();
    expect(
      screen.getByText('Estimado é o preço do órgão, com BDI. Contratado: só Pernambuco (TCE-PE).'),
    ).toBeTruthy();
  });

  it('nacional diz Brasil; sem contratado nem data-base, só o estimado; faixa e unidade', async () => {
    abrirCom(async () => ({
      uf: 'SP',
      referencias: [
        referencia({
          servico: 'escavacao_vala',
          rotulo: 'Escavação mecânica de vala',
          linha: 'redes',
          material: null,
          dn: null,
          escopo: null,
          faixa: '1_5_a_3m',
          unidade: 'm3',
          abrangencia: 'nacional',
          uf: null,
          casos_estimado: 1,
          mediana_estimado: '1234.5',
          casos_contratado: 0,
          mediana_contratado: null,
          data_base_inicio: null,
          data_base_fim: null,
        }),
        referencia({
          material: 'fofo',
          escopo: 'material',
          data_base_inicio: '2025-03-01',
          data_base_fim: '2025-03-20',
        }),
      ],
    }));
    expect(await screen.findByText('Escavação mecânica de vala · 1,5 a 3 m (m³)')).toBeTruthy();
    expect(screen.getByText('Estimado pelo órgão: R$ 1.234,50/m³ (1 caso, Brasil)')).toBeTruthy();
    expect(screen.getAllByText(/^Contratado:/)).toHaveLength(1);
    expect(screen.getByText('Rede de água · ferro fundido DN 150 · só o tubo (m)')).toBeTruthy();
    expect(screen.getByText('preços de 03/2025')).toBeTruthy();
    expect(screen.getAllByText(/^preços de/)).toHaveLength(1);
  });

  it.each([
    ['lista vazia', async () => ({uf: 'PE', referencias: []})],
    ['edital sem preços (null)', async () => null],
  ])('%s mostra a frase de vazio, sem rodapé', async (_, precos) => {
    abrirCom(precos);
    expect(
      await screen.findByText('Sem preços de referência para as linhas deste edital.'),
    ).toBeTruthy();
    expect(screen.queryByText(/Estimado é o preço do órgão/)).toBeNull();
  });

  it('erro nos preços não derruba o edital e oferece tentar de novo', async () => {
    const pedir = vi
      .fn<Fonte['precos']>()
      .mockRejectedValueOnce(new Error('A API respondeu 500'))
      .mockResolvedValueOnce({uf: 'PE', referencias: [referencia()]});
    abrirCom(pedir);
    expect(await screen.findByText('Não foi possível carregar os preços')).toBeTruthy();
    expect(screen.getByText('Execução de rede de esgoto no bairro Centro')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', {name: 'Tentar de novo'}));
    expect(await screen.findByText(/^Estimado pelo órgão: R\$ 182,40\/m/)).toBeTruthy();
    expect(pedir).toHaveBeenCalledTimes(2);
  });
});
