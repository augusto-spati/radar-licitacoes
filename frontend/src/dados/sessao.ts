// Login sem senha no Cognito: o e-mail pede um código (USER_AUTH + EMAIL_OTP) e o
// código devolve os tokens. Chama a API JSON do Cognito direto com `fetch`: o app
// client é público (sem segredo), então não precisa de assinatura nem de SDK.
// Os tokens ficam no sessionStorage (somem ao fechar a aba).

const CHAVE = 'radar.sessao';
// Renova um pouco antes de vencer, para a chamada não chegar com token expirado.
const FOLGA_MS = 60_000;

type Tokens = {idToken: string; refreshToken?: string; expiraEm: number};

type AuthenticationResult = {IdToken: string; RefreshToken?: string; ExpiresIn: number};
type RespostaCognito = {
  ChallengeName?: string;
  Session?: string;
  AuthenticationResult?: AuthenticationResult;
};

/** Desafio pendente entre "Enviar código" e "Entrar". */
export type Desafio = {email: string; session: string};

/** Erro do login; `tipo` é o `__type` do Cognito (ou 'rede' sem conexão). */
export class ErroSessao extends Error {
  tipo: string;
  constructor(mensagem: string, tipo = '') {
    super(mensagem);
    this.name = 'ErroSessao';
    this.tipo = tipo;
  }
}

const MUITAS_TENTATIVAS = 'Muitas tentativas. Espere alguns minutos.';
// Com prevent_user_existence_errors, a tela não diz se o e-mail existe.
const MENSAGENS: Record<string, string> = {
  CodeMismatchException: 'Código incorreto. Confira o e-mail e tente de novo.',
  ExpiredCodeException: 'O código expirou. Peça um novo.',
  NotAuthorizedException: 'Código incorreto ou expirado. Peça um novo código.',
  UserNotFoundException: 'Não foi possível enviar o código para este e-mail.',
  LimitExceededException: MUITAS_TENTATIVAS,
  TooManyRequestsException: MUITAS_TENTATIVAS,
};

/** Login real só com a API; com a fonte de exemplo a tela de entrar é só visual. */
export const loginReal = (): boolean => import.meta.env.VITE_FONTE === 'api';

async function chamar(operacao: string, corpo: object): Promise<RespostaCognito> {
  const regiao = import.meta.env.VITE_COGNITO_REGION ?? 'us-east-2';
  let resposta: Response;
  try {
    resposta = await fetch(`https://cognito-idp.${regiao}.amazonaws.com/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-amz-json-1.1',
        'X-Amz-Target': `AWSCognitoIdentityProviderService.${operacao}`,
      },
      body: JSON.stringify({ClientId: import.meta.env.VITE_COGNITO_CLIENT_ID, ...corpo}),
    });
  } catch {
    throw new ErroSessao('Sem conexão. Tente de novo.', 'rede');
  }
  const dados = (await resposta.json().catch(() => ({}))) as RespostaCognito & {__type?: string};
  if (!resposta.ok) {
    // __type vem como "Nome" ou "prefixo#Nome".
    const tipo = (dados.__type ?? '').split('#').pop() ?? '';
    throw new ErroSessao(MENSAGENS[tipo] ?? 'Não foi possível entrar agora. Tente de novo.', tipo);
  }
  return dados;
}

function guardar({IdToken, RefreshToken, ExpiresIn}: AuthenticationResult, refreshAnterior?: string) {
  const tokens: Tokens = {
    idToken: IdToken,
    refreshToken: RefreshToken ?? refreshAnterior,
    expiraEm: Date.now() + ExpiresIn * 1000,
  };
  sessionStorage.setItem(CHAVE, JSON.stringify(tokens));
}

function ler(): Tokens | null {
  try {
    return JSON.parse(sessionStorage.getItem(CHAVE) ?? 'null') as Tokens | null;
  } catch {
    return null;
  }
}

export async function enviarCodigo(email: string): Promise<Desafio> {
  // O pool não diferencia maiúsculas; normalizar evita usuário "diferente" por digitação.
  const usuario = email.trim().toLowerCase();
  const resposta = await chamar('InitiateAuth', {
    AuthFlow: 'USER_AUTH',
    AuthParameters: {USERNAME: usuario, PREFERRED_CHALLENGE: 'EMAIL_OTP'},
  });
  if (resposta.ChallengeName !== 'EMAIL_OTP' || !resposta.Session) {
    throw new ErroSessao(MENSAGENS.UserNotFoundException);
  }
  return {email: usuario, session: resposta.Session};
}

export async function confirmarCodigo({email, session}: Desafio, codigo: string): Promise<void> {
  const resposta = await chamar('RespondToAuthChallenge', {
    ChallengeName: 'EMAIL_OTP',
    Session: session,
    ChallengeResponses: {USERNAME: email, EMAIL_OTP_CODE: codigo.trim()},
  });
  if (!resposta.AuthenticationResult) throw new ErroSessao(MENSAGENS.CodeMismatchException);
  guardar(resposta.AuthenticationResult);
}

async function renovar(refreshToken: string): Promise<string | null> {
  try {
    const resposta = await chamar('InitiateAuth', {
      AuthFlow: 'REFRESH_TOKEN_AUTH',
      AuthParameters: {REFRESH_TOKEN: refreshToken},
    });
    if (!resposta.AuthenticationResult) return sair();
    guardar(resposta.AuthenticationResult, refreshToken);
    return resposta.AuthenticationResult.IdToken;
  } catch (erro) {
    // Refresh recusado encerra a sessão; falha de rede ou do serviço a mantém.
    if (erro instanceof ErroSessao && erro.tipo === 'NotAuthorizedException') return sair();
    throw erro;
  }
}

// Chamadas simultâneas com token vencido dividem uma renovação só.
let renovando: Promise<string | null> | null = null;

/** id token válido (renovado pelo refresh token se venceu) ou null se não há sessão. */
export async function obterToken(): Promise<string | null> {
  const tokens = ler();
  if (!tokens) return null;
  if (tokens.expiraEm - FOLGA_MS > Date.now()) return tokens.idToken;
  if (!tokens.refreshToken) return sair();
  renovando ??= renovar(tokens.refreshToken).finally(() => {
    renovando = null;
  });
  return renovando;
}

/** Encerra a sessão: revoga o refresh token no Cognito (sem esperar) e apaga os tokens. */
export function sair(): null {
  const refreshToken = ler()?.refreshToken;
  if (refreshToken) chamar('RevokeToken', {Token: refreshToken}).catch(() => undefined);
  sessionStorage.removeItem(CHAVE);
  return null;
}
