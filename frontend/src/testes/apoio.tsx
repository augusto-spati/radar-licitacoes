// Apoio dos testes de tela: fonte simulada, edital de exemplo e render com
// roteador em memória. Não é arquivo *.test.* para poder ser importado.
import type {ReactElement} from 'react';
import {render} from '@testing-library/react';
import {vi} from 'vitest';
import {MemoryRouter, Route, Routes, useLocation} from 'react-router';
import {LinkProvider} from '@astryxdesign/core/Link';
import {Theme} from '@astryxdesign/core/theme';
import {neutralTheme} from '@astryxdesign/theme-neutral/built';
import {LinkDoRoteador} from '../App';
import type {Fonte} from '../dados/fonte';
import {FonteContext} from '../dados/FonteContext';
import type {EditalDetalhe} from '../dados/tipos';

export function fonteFalsa(parcial: Partial<Fonte> = {}): Fonte {
  return {
    listar: async () => ({atualizado_em: new Date().toISOString(), editais: []}),
    obter: async () => null,
    analise: async () => null,
    historico: async () => null,
    precos: async () => null,
    status: async () => ({atualizado_em: null}),
    ...parcial,
  };
}

export function edital(parcial: Partial<EditalDetalhe> = {}): EditalDetalhe {
  return {
    fonte: 'pncp',
    id_publico: 'pncp~13127340000120-1-000036/2025',
    objeto: 'Execução de rede de esgoto no bairro Centro',
    orgao_nome: 'SERVIÇO AUTÔNOMO DE ÁGUA E ESGOTO',
    orgao_cnpj: '13127340000120',
    uf: 'SP',
    municipio: 'Holambra',
    modalidade_id: 4,
    modalidade_nome: 'Concorrência - Eletrônica',
    valor_estimado: '845000',
    data_publicacao: '2026-11-20T10:16:40',
    data_abertura_proposta: '2026-11-21T08:00:00',
    data_encerramento_proposta: '2026-12-10T08:00:00',
    situacao_nome: 'Divulgada no PNCP',
    situacao_normalizada: 'aberta',
    link_origem: 'https://compras.exemplo.gov.br/edital/36',
    linhas: ['redes'],
    ...parcial,
  };
}

// Mostra a URL atual, para conferir o que foi parar na consulta.
function Endereco() {
  const {pathname, search} = useLocation();
  return <output aria-label="endereço">{pathname + search}</output>;
}

type Opcoes = {fonte: Fonte; endereco?: string; padrao?: string};

export function renderizar(tela: ReactElement, {fonte, endereco = '/', padrao = '/'}: Opcoes) {
  return render(
    <Theme theme={neutralTheme}>
      <FonteContext value={fonte}>
        <MemoryRouter initialEntries={[endereco]}>
          <LinkProvider component={LinkDoRoteador}>
            <Routes>
              <Route path={padrao} element={tela} />
            </Routes>
            <Endereco />
          </LinkProvider>
        </MemoryRouter>
      </FonteContext>
    </Theme>,
  );
}

/** Resposta JSON (da API ou do Cognito) para um `fetch` simulado. */
export const respostaJson = (corpo: unknown, status = 200) =>
  new Response(JSON.stringify(corpo), {status, headers: {'Content-Type': 'application/json'}});

/** Simula o `fetch` devolvendo as respostas na ordem; devolve o mock. */
export function simularFetch(...respostas: Response[]) {
  const mock = vi.fn();
  for (const r of respostas) mock.mockResolvedValueOnce(r);
  vi.stubGlobal('fetch', mock);
  return mock;
}

/** Corpo JSON enviado ao Cognito na chamada `n` do mock. */
export const corpoEnviado = (mock: ReturnType<typeof vi.fn>, n: number) =>
  JSON.parse((mock.mock.calls[n][1] as RequestInit).body as string) as Record<string, unknown>;

export const desafioCognito = {ChallengeName: 'EMAIL_OTP', Session: 'sessao-1'};
export const tokensCognito = {
  AuthenticationResult: {IdToken: 'id-token-1', RefreshToken: 'refresh-1', ExpiresIn: 3600},
};
