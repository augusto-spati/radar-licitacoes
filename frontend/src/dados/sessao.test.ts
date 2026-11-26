// @vitest-environment jsdom
import {afterEach, describe, expect, it, vi} from 'vitest';
import {corpoEnviado, respostaJson, simularFetch} from '../testes/apoio';
import {ErroFonte, fonteApi} from './fonteApi';
import {obterToken} from './sessao';

const guardarSessao = (expiraEm: number, refreshToken?: string) =>
  sessionStorage.setItem('radar.sessao', JSON.stringify({idToken: 'id-1', refreshToken, expiraEm}));

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
  window.location.hash = '';
});

describe('sessão na fonteApi', () => {
  it('com sessão, a chamada leva Authorization: Bearer <id token>', async () => {
    guardarSessao(Date.now() + 3_600_000);
    const mock = simularFetch(respostaJson({atualizado_em: null, editais: []}));
    await fonteApi.listar({});
    expect(mock).toHaveBeenCalledWith('/api/editais', {headers: {Authorization: 'Bearer id-1'}});
  });

  it('sem sessão, a chamada vai sem cabeçalho', async () => {
    const mock = simularFetch(respostaJson({atualizado_em: null}));
    await fonteApi.status();
    expect(mock).toHaveBeenCalledWith('/api/status');
  });

  it('401 apaga a sessão e leva para /entrar', async () => {
    guardarSessao(Date.now() + 3_600_000);
    simularFetch(respostaJson({message: 'Unauthorized'}, 401));
    const erro = await fonteApi.listar({}).catch((e: unknown) => e);
    expect(erro).toBeInstanceOf(ErroFonte);
    expect((erro as ErroFonte).status).toBe(401);
    expect(window.location.hash).toBe('#/entrar');
    expect(sessionStorage.getItem('radar.sessao')).toBeNull();
  });
});

describe('obterToken', () => {
  it('token vencido é renovado com o refresh token', async () => {
    guardarSessao(Date.now() - 1, 'refresh-1');
    const mock = simularFetch(
      respostaJson({AuthenticationResult: {IdToken: 'id-2', ExpiresIn: 3600}}),
    );
    expect(await obterToken()).toBe('id-2');
    expect(corpoEnviado(mock, 0)).toMatchObject({
      AuthFlow: 'REFRESH_TOKEN_AUTH',
      AuthParameters: {REFRESH_TOKEN: 'refresh-1'},
    });
    expect(sessionStorage.getItem('radar.sessao')).toContain('refresh-1');
  });

  it('refresh recusado encerra a sessão', async () => {
    guardarSessao(Date.now() - 1, 'refresh-1');
    simularFetch(respostaJson({__type: 'NotAuthorizedException'}, 400));
    expect(await obterToken()).toBeNull();
    expect(sessionStorage.getItem('radar.sessao')).toBeNull();
  });
});
