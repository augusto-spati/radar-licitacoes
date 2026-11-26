// @vitest-environment jsdom
import {afterEach, describe, expect, it, vi} from 'vitest';
import {corpoEnviado, respostaJson, simularFetch} from '../testes/apoio';
import {ErroFonte, fonteApi} from './fonteApi';
import {ErroSessao, obterToken, sair} from './sessao';

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

  it('refresh recusado (NotAuthorizedException) encerra a sessão', async () => {
    guardarSessao(Date.now() - 1, 'refresh-1');
    simularFetch(respostaJson({__type: 'NotAuthorizedException'}, 400));
    expect(await obterToken()).toBeNull();
    expect(sessionStorage.getItem('radar.sessao')).toBeNull();
  });

  it('falha de rede no refresh mantém a sessão e mostra o erro', async () => {
    guardarSessao(Date.now() - 1, 'refresh-1');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const erro = await obterToken().catch((e: unknown) => e);
    expect(erro).toBeInstanceOf(ErroSessao);
    expect(sessionStorage.getItem('radar.sessao')).toContain('refresh-1');
    expect(await fonteApi.status().catch((e: unknown) => e)).toBeInstanceOf(ErroFonte);
  });

  it('chamadas simultâneas dividem uma renovação só', async () => {
    guardarSessao(Date.now() - 1, 'refresh-1');
    const mock = simularFetch(respostaJson({AuthenticationResult: {IdToken: 'id-2', ExpiresIn: 3600}}));
    expect(await Promise.all([obterToken(), obterToken()])).toEqual(['id-2', 'id-2']);
    expect(mock).toHaveBeenCalledTimes(1);
  });
});

describe('sair', () => {
  it('revoga o refresh token no Cognito e apaga a sessão', () => {
    guardarSessao(Date.now() + 3_600_000, 'refresh-1');
    const mock = simularFetch(respostaJson({}));
    sair();
    const [, init] = mock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)['X-Amz-Target']).toBe(
      'AWSCognitoIdentityProviderService.RevokeToken',
    );
    expect(corpoEnviado(mock, 0)).toMatchObject({Token: 'refresh-1'});
    expect(sessionStorage.getItem('radar.sessao')).toBeNull();
  });
});
