// @vitest-environment jsdom
import {cleanup, fireEvent, screen} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {
  corpoEnviado,
  desafioCognito,
  fonteFalsa,
  renderizar,
  respostaJson,
  simularFetch,
  tokensCognito,
} from '../testes/apoio';
import {Entrar} from './Entrar';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  sessionStorage.clear();
});

const abrir = () => renderizar(<Entrar />, {fonte: fonteFalsa(), endereco: '/entrar', padrao: '/entrar'});
const endereco = () => screen.getByRole('status', {name: 'endereço'}).textContent;

async function pedirCodigo(email: string) {
  fireEvent.change(screen.getByLabelText('E-mail'), {target: {value: email}});
  fireEvent.click(screen.getByRole('button', {name: 'Enviar código'}));
  return screen.findByLabelText('Código');
}

async function digitarCodigo(codigo: string) {
  fireEvent.change(await screen.findByLabelText('Código'), {target: {value: codigo}});
  fireEvent.click(screen.getByRole('button', {name: 'Entrar'}));
}

describe('Entrar (protótipo, fonte de exemplo)', () => {
  it('e-mail, código e "Entrar" levam à lista sem chamar o Cognito', async () => {
    const mock = simularFetch();
    abrir();
    expect(screen.getByText('Protótipo com dados de exemplo')).toBeTruthy();
    expect(screen.queryByLabelText('Código')).toBeNull();

    await pedirCodigo('tio@fr.com.br');
    await digitarCodigo('123456');

    await vi.waitFor(() => expect(endereco()).toBe('/'));
    expect(mock).not.toHaveBeenCalled();
  });
});

describe('Entrar (login real, Cognito)', () => {
  it('pede o código por EMAIL_OTP, confirma e guarda o id token', async () => {
    vi.stubEnv('VITE_FONTE', 'api');
    vi.stubEnv('VITE_COGNITO_REGION', 'us-east-2');
    vi.stubEnv('VITE_COGNITO_CLIENT_ID', 'cliente-1');
    const mock = simularFetch(respostaJson(desafioCognito), respostaJson(tokensCognito));
    abrir();
    expect(screen.queryByText('Protótipo com dados de exemplo')).toBeNull();

    await pedirCodigo(' Tio@FR.com.br ');
    expect(screen.getByText('Se este e-mail tiver acesso, o código chega em instantes.')).toBeTruthy();
    const [url, init] = mock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('https://cognito-idp.us-east-2.amazonaws.com/');
    expect((init.headers as Record<string, string>)['X-Amz-Target']).toBe(
      'AWSCognitoIdentityProviderService.InitiateAuth',
    );
    expect(corpoEnviado(mock, 0)).toEqual({
      ClientId: 'cliente-1',
      AuthFlow: 'USER_AUTH',
      AuthParameters: {USERNAME: 'tio@fr.com.br', PREFERRED_CHALLENGE: 'EMAIL_OTP'},
    });

    await digitarCodigo('123456');
    await vi.waitFor(() => expect(endereco()).toBe('/'));
    expect(corpoEnviado(mock, 1)).toMatchObject({
      ChallengeName: 'EMAIL_OTP',
      Session: 'sessao-1',
      ChallengeResponses: {USERNAME: 'tio@fr.com.br', EMAIL_OTP_CODE: '123456'},
    });
    expect(sessionStorage.getItem('radar.sessao')).toContain('id-token-1');
  });

  it('código errado mostra a mensagem e continua na tela', async () => {
    vi.stubEnv('VITE_FONTE', 'api');
    simularFetch(
      respostaJson(desafioCognito),
      respostaJson({__type: 'CodeMismatchException', message: 'Invalid code'}, 400),
    );
    abrir();

    await pedirCodigo('tio@fr.com.br');
    await digitarCodigo('000000');

    const alerta = await screen.findByRole('alert');
    expect(alerta.textContent).toContain('Código incorreto. Confira o e-mail e tente de novo.');
    expect(endereco()).toBe('/entrar');
    expect(sessionStorage.getItem('radar.sessao')).toBeNull();
  });

  it('Enter envia o e-mail e o código; os campos pedem o preenchimento automático certo', async () => {
    vi.stubEnv('VITE_FONTE', 'api');
    const mock = simularFetch(respostaJson(desafioCognito), respostaJson(tokensCognito));
    abrir();

    const email = screen.getByLabelText('E-mail');
    expect(email.getAttribute('autocomplete')).toBe('email');
    fireEvent.change(email, {target: {value: 'tio@fr.com.br'}});
    fireEvent.keyDown(email, {key: 'Enter'});

    const codigo = await screen.findByLabelText('Código');
    expect(codigo.getAttribute('autocomplete')).toBe('one-time-code');
    expect(codigo.getAttribute('inputmode')).toBe('numeric');
    expect(document.activeElement).toBe(codigo);
    fireEvent.change(codigo, {target: {value: '654321'}});
    fireEvent.keyDown(codigo, {key: 'Enter'});

    await vi.waitFor(() => expect(endereco()).toBe('/'));
    expect(mock).toHaveBeenCalledTimes(2);
  });

  it('"Pedir outro código" fica ocupado enquanto o pedido não volta', async () => {
    vi.stubEnv('VITE_FONTE', 'api');
    let responder: (r: Response) => void = () => undefined;
    const pendente = new Promise<Response>(resolve => (responder = resolve));
    const mock = simularFetch(respostaJson(desafioCognito));
    mock.mockReturnValueOnce(pendente);
    abrir();

    await pedirCodigo('tio@fr.com.br');
    const outro = screen.getByRole('button', {name: 'Pedir outro código'});
    fireEvent.click(outro);
    await vi.waitFor(() => expect(outro.getAttribute('aria-busy')).toBe('true'));

    responder(respostaJson(desafioCognito));
    await vi.waitFor(() => expect(outro.getAttribute('aria-busy')).not.toBe('true'));
    expect(mock).toHaveBeenCalledTimes(2);
  });
});
