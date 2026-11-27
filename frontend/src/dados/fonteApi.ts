import type {Fonte, Filtros} from './fonte';
import {obterToken, sair} from './sessao';
import type {Analise, EditalDetalhe, Historico} from './tipos';

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
  let resposta: Response;
  try {
    // Falha ao renovar o token (rede) aparece como falha de conexão; a sessão fica.
    const token = await obterToken();
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

// O id público é `<fonte>~<id>` e o id tem "/" (as rotas são {id_publico:path}):
// codifica cada trecho e mantém a barra.
const caminhoDoId = (id: string): string => id.split('/').map(encodeURIComponent).join('/');

/** JSON da resposta, ou null quando o status diz "não tem" (404, e o que mais vier em `vazios`). */
async function lerOuNulo<T>(caminho: string, vazios: number[] = [404]): Promise<T | null> {
  const resposta = await buscar(caminho);
  if (vazios.includes(resposta.status)) return null;
  if (!resposta.ok) throw new ErroFonte(`A API respondeu ${resposta.status}`, resposta.status);
  return (await resposta.json()) as T;
}

export const fonteApi: Fonte = {
  listar: f => lerJson(`/api/editais${consulta(f)}`),
  status: () => lerJson('/api/status'),
  obter: id => lerOuNulo<EditalDetalhe>(`/api/editais/${caminhoDoId(id)}`),
  // 404: edital ainda não analisado; 503: a API está sem modelo de IA configurado.
  analise: id => lerOuNulo<Analise>(`/api/analises/${caminhoDoId(id)}`, [404, 503]),
  // 404: edital não encontrado; órgão sem contratações vem vazio (200).
  historico: id => lerOuNulo<Historico>(`/api/historicos/${caminhoDoId(id)}`),
};
