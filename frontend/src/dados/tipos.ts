import type {components} from './api';

export type EditalResumo = components['schemas']['EditalResumoOut'];
export type EditalDetalhe = components['schemas']['EditalDetalheOut'];
export type FonteEdital = components['schemas']['Fonte'];
export type Lista = components['schemas']['ListaOut'];
export type Status = components['schemas']['StatusOut'];
// Linhas de serviço e UFs do config/filtro.toml, via OpenAPI (enums da API).
export type Linha = components['schemas']['Linha'];
export type UF = components['schemas']['UF'];
