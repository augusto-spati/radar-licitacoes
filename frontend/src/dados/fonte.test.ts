import {afterEach, describe, expect, it, vi} from 'vitest';
import {ErroFonte, fonteApi} from './fonteApi';
import {criarFonteExemplo} from './fonteExemplo';
import {edital} from '../testes/apoio';
import type {EditalDetalhe, Linha} from './tipos';

const agora = new Date('2026-11-26T12:00:00-03:00');
const fonteExemplo = criarFonteExemplo(() => agora);

describe('fonteExemplo', () => {
  it('filtra por UF', async () => {
    const {editais} = await fonteExemplo.listar({ufs: ['SP']});
    expect(editais.length).toBeGreaterThan(0);
    expect(editais.every(e => e.uf === 'SP')).toBe(true);
  });
  it('várias UFs fazem interseção por pertencimento', async () => {
    const {editais} = await fonteExemplo.listar({ufs: ['SP', 'PR']});
    expect(new Set(editais.map(e => e.uf))).toEqual(new Set(['SP', 'PR']));
  });
  it('filtra pela linha de serviço', async () => {
    const {editais} = await fonteExemplo.listar({linha: 'redes'});
    expect(editais.length).toBeGreaterThan(0);
    expect(editais.every(e => e.linhas.includes('redes'))).toBe(true);
    expect((await fonteExemplo.listar({linha: 'inexistente' as Linha})).editais).toEqual([]);
  });
  it('sem "todos" só traz encerramento no futuro, em ordem crescente', async () => {
    const {editais} = await fonteExemplo.listar({});
    const datas = editais.map(e => e.data_encerramento_proposta);
    expect(datas.every(d => d !== null && new Date(`${d}-03:00`) > agora)).toBe(true);
    expect(datas).toEqual([...datas].sort());
  });
  it('com "todos" traz também os encerrados e os sem data, nulos por último', async () => {
    const depois = criarFonteExemplo(() => new Date('2026-12-05T12:00:00-03:00'));
    const abertos = await depois.listar({});
    const todos = await depois.listar({todos: true});
    expect(todos.editais.length).toBeGreaterThan(abertos.editais.length);
    const datas = todos.editais.map(e => e.data_encerramento_proposta);
    const comData = datas.filter(d => d !== null);
    expect(comData).toEqual([...comData].sort());
    expect(datas.slice(comData.length).every(d => d === null)).toBe(true);
  });
  it('devolve a data da última atualização do arquivo', async () => {
    const lista = await fonteExemplo.listar({});
    expect(lista.atualizado_em).toBe((await fonteExemplo.status()).atualizado_em);
  });
  it('obter acha pelo id público (com barra) e devolve null se não existe', async () => {
    const {editais} = await fonteExemplo.listar({});
    const id = editais[0].id_publico;
    expect((await fonteExemplo.obter(id))?.id_publico).toBe(id);
    expect(await fonteExemplo.obter('pncp~0-0-0/0000')).toBeNull();
  });
});

describe('fonteExemplo com as três fontes', () => {
  const pronto = (fonte: EditalDetalhe['fonte'], id: string, parcial: Partial<EditalDetalhe> = {}) =>
    edital({
      fonte,
      id_publico: `${fonte}~${id}`,
      data_encerramento_proposta: '2026-12-10T08:00:00',
      ...parcial,
    });
  const dados = {
    atualizado_em: null,
    editais: [
      pronto('pncp', '1-1-1/2026'),
      pronto('sanepar', 'OE-345/26'),
      pronto('compesa', '2026LC213CEL-1'),
      pronto('sanepar', 'OE-1/26', {situacao_normalizada: 'encerrada'}),
      pronto('compesa', 'JULG-1', {situacao_normalizada: 'em_julgamento'}),
      pronto('sanepar', 'OE-2/26', {data_encerramento_proposta: '2026-11-01T08:00:00'}),
      pronto('compesa', 'SEM-PRAZO', {data_encerramento_proposta: null}),
    ],
  };
  const fonte = criarFonteExemplo(() => agora, dados);

  it('aberto = situação aberta e prazo futuro, nas três fontes', async () => {
    const {editais} = await fonte.listar({});
    expect(editais.map(e => e.id_publico)).toEqual([
      'pncp~1-1-1/2026',
      'sanepar~OE-345/26',
      'compesa~2026LC213CEL-1',
    ]);
  });
  it('com "todos" traz também os que não estão abertos', async () => {
    expect((await fonte.listar({todos: true})).editais).toHaveLength(7);
  });
  it('obter acha o edital da Sanepar pelo id público', async () => {
    expect((await fonte.obter('sanepar~OE-345/26'))?.fonte).toBe('sanepar');
    expect(await fonte.obter('OE-345/26')).toBeNull();
  });
});

describe('fonteApi', () => {
  const resposta = (corpo: unknown, status = 200) =>
    new Response(JSON.stringify(corpo), {status, headers: {'Content-Type': 'application/json'}});
  const simular = (r: Response) => {
    const mock = vi.fn().mockResolvedValue(r);
    vi.stubGlobal('fetch', mock);
    return mock;
  };
  afterEach(() => vi.unstubAllGlobals());

  it('obter com 404 devolve null', async () => {
    simular(resposta({detail: 'edital não encontrado'}, 404));
    expect(await fonteApi.obter('pncp~1-1-1/2025')).toBeNull();
  });
  it('obter mantém a barra do id como separador de caminho', async () => {
    const mock = simular(resposta(edital()));
    await fonteApi.obter('pncp~13127340000120-1-000036/2025');
    expect(mock).toHaveBeenCalledWith('/api/editais/pncp~13127340000120-1-000036/2025');
  });
  it('obter da Sanepar monta fonte~id com a barra preservada', async () => {
    const mock = simular(resposta(edital()));
    await fonteApi.obter('sanepar~OE-345/26');
    expect(mock).toHaveBeenCalledWith('/api/editais/sanepar~OE-345/26');
  });
  it('obter codifica caracteres especiais dentro de cada trecho', async () => {
    const mock = simular(resposta({}));
    await fonteApi.obter('a b/c?d');
    expect(mock).toHaveBeenCalledWith('/api/editais/a%20b/c%3Fd');
  });
  it('outros status viram ErroFonte com o status', async () => {
    simular(resposta({detail: 'x'}, 500));
    const erro = await fonteApi.obter('pncp~1-1-1/2025').catch((e: unknown) => e);
    expect(erro).toBeInstanceOf(ErroFonte);
    expect((erro as ErroFonte).status).toBe(500);
  });
  it('listar monta a consulta com uf repetido, linha e todos', async () => {
    const mock = simular(resposta({atualizado_em: null, editais: []}));
    await fonteApi.listar({linha: 'redes', ufs: ['SP', 'MG'], todos: true});
    expect(mock).toHaveBeenCalledWith('/api/editais?linha=redes&uf=SP&uf=MG&todos=true');
  });
  it('listar sem filtros não leva consulta', async () => {
    const mock = simular(resposta({atualizado_em: null, editais: []}));
    await fonteApi.listar({});
    expect(mock).toHaveBeenCalledWith('/api/editais');
  });
  it('status lê /api/status', async () => {
    const mock = simular(resposta({atualizado_em: '2026-11-26T13:09:37-03:00'}));
    expect((await fonteApi.status()).atualizado_em).toBe('2026-11-26T13:09:37-03:00');
    expect(mock).toHaveBeenCalledWith('/api/status');
  });
  it('falha de rede vira ErroFonte sem status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const erro = await fonteApi.status().catch((e: unknown) => e);
    expect(erro).toBeInstanceOf(ErroFonte);
    expect((erro as ErroFonte).status).toBeUndefined();
  });
});
