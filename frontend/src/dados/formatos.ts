import type {FonteEdital, Linha, UF} from './tipos';

// Datas da API sem fuso (ex.: "2026-11-27T08:00:00") são horário de Brasília.
// O Brasil não tem horário de verão desde 2019, então Brasília é UTC-3 fixo.
const DESLOCAMENTO_BRASILIA_MS = -3 * 60 * 60 * 1000;
const UM_DIA_MS = 24 * 60 * 60 * 1000;

/** Instante de um ISO com fuso ("...-03:00", "Z") ou, sem fuso, em Brasília. */
export function instante(iso: string): number {
  return Date.parse(/(?:Z|[+-]\d{2}:\d{2})$/.test(iso) ? iso : `${iso}-03:00`);
}

// Relógio de parede de Brasília: o mesmo instante deslocado, lido com getters UTC.
const paredeBrasilia = (ms: number) => new Date(ms + DESLOCAMENTO_BRASILIA_MS);
const dois = (n: number) => String(n).padStart(2, '0');
const diaMes = (d: Date) => `${dois(d.getUTCDate())}/${dois(d.getUTCMonth() + 1)}`;
const horaMinuto = (d: Date) => `${dois(d.getUTCHours())}:${dois(d.getUTCMinutes())}`;
const diaCalendario = (d: Date) => Math.floor(d.getTime() / UM_DIA_MS);

export function formatarPrazo(iso: string | null, agora: Date): string {
  if (iso === null) return '—';
  const alvo = instante(iso);
  const parede = paredeBrasilia(alvo);
  if (alvo < agora.getTime()) return `encerrado · ${diaMes(parede)}`;
  const dias = diaCalendario(parede) - diaCalendario(paredeBrasilia(agora.getTime()));
  if (dias === 0) return `encerra hoje · ${horaMinuto(parede)}`;
  if (dias === 1) return `encerra amanhã · ${diaMes(parede)}`;
  return `encerra em ${dias} dias · ${diaMes(parede)}`;
}

const diaMesAno = (d: Date) => `${diaMes(d)}/${d.getUTCFullYear()}`;

/** "dd/mm/aaaa HH:MM" no relógio de Brasília; nulo vira travessão. */
export function formatarDataHora(iso: string | null): string {
  if (iso === null) return '—';
  const parede = paredeBrasilia(instante(iso));
  return `${diaMesAno(parede)} ${horaMinuto(parede)}`;
}

/** "dd/mm/aaaa" no relógio de Brasília; nulo vira travessão. */
export const formatarData = (iso: string | null): string =>
  iso === null ? '—' : diaMesAno(paredeBrasilia(instante(iso)));

// Folga contra o erro de ponto flutuante: 0.285 * 100 dá 28.499999999999996.
const FOLGA_ARREDONDAMENTO = 1e-9;

/**
 * Fração da API ("0.2415") em percentual inteiro ("24%"), meio para cima
 * ("0.285" → "29%"); nulo ou inválido vira travessão.
 */
export function formatarPercentual(fracao: string | null): string {
  const n = fracao === null || fracao.trim() === '' ? NaN : Number(fracao);
  return Number.isFinite(n) ? `${Math.round(n * 100 + FOLGA_ARREDONDAMENTO)}%` : '—';
}

/** "HH:MM" no relógio de Brasília. */
export const formatarHora = (iso: string): string => horaMinuto(paredeBrasilia(instante(iso)));

export function formatarCnpj(cnpj: string | null): string {
  if (cnpj === null) return '—';
  const m = /^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/.exec(cnpj);
  return m === null ? cnpj : `${m[1]}.${m[2]}.${m[3]}/${m[4]}-${m[5]}`;
}

/** Só endereço http(s) vira link; vazio ou outro esquema (javascript:, ftp:...) dá nulo. */
export function linkHttp(link: string | null | undefined): string | null {
  const aparado = (link ?? '').trim();
  return /^https?:\/\//i.test(aparado) ? aparado : null;
}

const numero = (v: number, casas: number) =>
  v.toLocaleString('pt-BR', {minimumFractionDigits: casas, maximumFractionDigits: casas});

// A partir daqui "mil" arredondaria para "R$ 1.000 mil": vira "R$ 1,00 mi".
const LIMIAR_MILHAO = 999_500;

export function formatarValor(v: string | null): string {
  if (v === null || v.trim() === '') return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  if (n >= LIMIAR_MILHAO) return `R$ ${numero(n / 1_000_000, 2)} mi`;
  if (n >= 1_000) return `R$ ${numero(n / 1_000, 0)} mil`;
  return reais(n);
}

// O Intl do pt-BR separa "R$" do número com espaço sem quebra (U+00A0);
// trocamos por espaço comum para o texto ser comparável e copiável.
const reais = (n: number) =>
  new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'})
    .format(n)
    .replace(/\u00a0/g, ' ');

// Unidades canônicas dos serviços-padrão (config/servicos.toml); outra aparece como veio.
const UNIDADES: Record<string, string> = {m2: 'm²', m3: 'm³'};

export const nomeUnidade = (unidade: string): string => UNIDADES[unidade] ?? unidade;

/**
 * Preço unitário por extenso, sem "mil"/"mi" (o centavo importa): "R$ 1.234,50/m³";
 * nulo ou inválido vira travessão.
 */
export function formatarPrecoUnitario(v: string | null, unidade: string): string {
  const n = v === null || v.trim() === '' ? NaN : Number(v);
  return Number.isFinite(n) ? `${reais(n)}/${nomeUnidade(unidade)}` : '—';
}

/** "05/2025" de uma data ISO ("2025-05-01"): só mês e ano, sem fuso. */
export function formatarMesAno(iso: string): string {
  const m = /^(\d{4})-(\d{2})/.exec(iso);
  return m === null ? iso : `${m[2]}/${m[1]}`;
}

const LIMITE_DESATUALIZADO_MS = 36 * 60 * 60 * 1000;

export function estaDesatualizado(atualizadoEm: string | null, agora: Date): boolean {
  return atualizadoEm === null || agora.getTime() - instante(atualizadoEm) > LIMITE_DESATUALIZADO_MS;
}

/**
 * Linhas de serviço da FR, na ordem dos filtros: valor da API → nome de tela.
 * O tipo vem do api.d.ts (enum gerado do config/filtro.toml): linha nova ou
 * removida no filtro.toml faz o tsc falhar aqui até o front acompanhar.
 */
export const LINHAS: Record<Linha, string> = {
  redes: 'Redes',
  perdas: 'Perdas',
  comercial: 'Comercial',
  consultoria: 'Consultoria',
};

// `satisfies Record<UF, ...>` reprova UF a mais ou a menos que o filtro.toml.
const ORDEM_UFS = {SP: 0, PR: 1, PE: 2, MG: 3} satisfies Record<UF, number>;
export const UFS = Object.keys(ORDEM_UFS) as UF[];

export const ehLinha = (v: string): v is Linha => Object.hasOwn(LINHAS, v);
export const ehUF = (v: string): v is UF => Object.hasOwn(ORDEM_UFS, v);

export const nomeLinha = (linha: string): string => (ehLinha(linha) ? LINHAS[linha] : linha);

export function localidade(municipio: string | null, uf: string | null): string {
  return municipio === null && uf === null ? '—' : `${municipio ?? '—'}/${uf ?? '—'}`;
}

/** Nome de tela de cada fonte; o tipo vem do enum gerado: fonte nova na API faz o tsc falhar aqui. */
export const NOMES_FONTE: Record<FonteEdital, string> = {
  pncp: 'PNCP',
  sanepar: 'Sanepar',
  compesa: 'Compesa',
  dae_jundiai: 'DAE Jundiaí',
};

/** O id da fonte dentro do id público `<fonte>~<id>` (só o primeiro "~" separa). */
export function idNaFonte(idPublico: string): string {
  const posicao = idPublico.indexOf('~');
  return posicao < 0 ? idPublico : idPublico.slice(posicao + 1);
}
