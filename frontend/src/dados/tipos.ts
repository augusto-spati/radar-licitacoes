import type {components} from './api';

export type EditalResumo = components['schemas']['EditalResumoOut'];
export type EditalDetalhe = components['schemas']['EditalDetalheOut'];
export type FonteEdital = components['schemas']['Fonte'];
export type Lista = components['schemas']['ListaOut'];
export type Painel = components['schemas']['PainelOut'];
export type SecaoDoPainel = components['schemas']['SecaoPainelOut'];
export type FalhaColeta = components['schemas']['FalhaColetaOut'];
export type FiltroVeredito = components['schemas']['FiltroVeredito'];
export type Status = components['schemas']['StatusOut'];
// Linhas de serviço e UFs do config/filtro.toml, via OpenAPI (enums da API).
export type Linha = components['schemas']['Linha'];
export type UF = components['schemas']['UF'];
// Análise por IA do edital (etapa 6): veredito, exigências e documentos lidos.
export type Analise = components['schemas']['AnaliseOut'];
export type ItemAnalise = components['schemas']['ItemAnaliseOut'];
export type DocumentoAnalise = components['schemas']['DocumentoAnaliseOut'];
// Histórico do órgão do edital (etapa 7a): contagens, vencedores, últimas e régua de desconto.
export type Historico = components['schemas']['HistoricoOut'];
export type ContratacaoPassada = components['schemas']['ContratacaoPassadaOut'];
export type Vencedor = components['schemas']['VencedorOut'];
export type Regua = components['schemas']['ReguaOut'];
// Preços de referência por serviço-padrão (etapa 7b): estimado do órgão e contratado (TCE-PE).
export type Precos = components['schemas']['PrecosOut'];
export type ReferenciaPreco = components['schemas']['ReferenciaOut'];
