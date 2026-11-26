import type {Fonte, Filtros} from './fonte';
import {obterToken, sair} from './sessao';
import type {EditalDetalhe} from './tipos';

export class ErroFonte extends Error {
  status?: number;
  constructor(mensagem: string, status?: number) {
    super(mensagem);
    this.name = 'ErroFonte';
    this.status = status;
  }
}

// Com sessão, manda o id token; um 401 encerra a sessão e leva para a tela de entrar.
async function buscar(caminho: string): Promise<Response> {
  const token = await obterToken();
  let resposta: Response;
  try {
    resposta = token
      ? await fetch(caminho, {headers: {Authorization: `Bearer ${token}`}})
      : await fetch(caminho);
  } catch (erro) {
    throw new ErroFonte(`Sem conexão com a API: ${String(erro)}`);
  }
  if (resposta.status === 401) {
    sair();
    window.location.hash = '#/entrar';
    throw new ErroFonte('Sessão expirada: entre de novo', 401);
  }
  return resposta;
}

async function lerJson<T>(caminho: string): Promise<T> {
  const resposta = await buscar(caminho);
  if (!resposta.ok) throw new ErroFonte(`A API respondeu ${resposta.status}`, resposta.status);
  return (await resposta.json()) as T;
}

function consulta({linha, ufs, todos}: Filtros): string {
  const params = new URLSearchParams();
  if (linha !== undefined) params.set('linha', linha);
  for (const uf of ufs ?? []) params.append('uf', uf);
  if (todos !== undefined) params.set('todos', String(todos));
  const texto = params.toString();
  return texto === '' ? '' : `?${texto}`;
}

export const fonteApi: Fonte = {
  listar: f => lerJson(`/api/editais${consulta(f)}`),
  status: () => lerJson('/api/status'),
  async obter(id) {
    // O id tem "/" (a rota é {numero:path}): codifica cada trecho, mantém a barra.
    const caminho = `/api/editais/${id.split('/').map(encodeURIComponent).join('/')}`;
    const resposta = await buscar(caminho);
    if (resposta.status === 404) return null;
    if (!resposta.ok) throw new ErroFonte(`A API respondeu ${resposta.status}`, resposta.status);
    return (await resposta.json()) as EditalDetalhe;
  },
};
