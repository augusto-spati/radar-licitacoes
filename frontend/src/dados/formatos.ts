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

/** "dd/mm/aaaa HH:MM" no relógio de Brasília; nulo vira travessão. */
export function formatarDataHora(iso: string | null): string {
  if (iso === null) return '—';
  const parede = paredeBrasilia(instante(iso));
  return `${diaMes(parede)}/${parede.getUTCFullYear()} ${horaMinuto(parede)}`;
}

/** "HH:MM" no relógio de Brasília. */
export const formatarHora = (iso: string): string => horaMinuto(paredeBrasilia(instante(iso)));

export function formatarCnpj(cnpj: string): string {
  const m = /^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/.exec(cnpj);
  return m === null ? cnpj : `${m[1]}.${m[2]}.${m[3]}/${m[4]}-${m[5]}`;
}

/** Página do edital no PNCP, a partir do número de controle "<cnpj>-1-<seq>/<ano>". */
export function linkPncp(numeroControle: string): string | null {
  const m = /^(\d{14})-\d+-(\d+)\/(\d{4})$/.exec(numeroControle);
  return m === null ? null : `https://pncp.gov.br/app/editais/${m[1]}/${m[3]}/${Number(m[2])}`;
}

const numero = (v: number, casas: number) =>
  v.toLocaleString('pt-BR', {minimumFractionDigits: casas, maximumFractionDigits: casas});

export function formatarValor(v: string | null): string {
  if (v === null) return '—';
  const n = Number(v);
  if (n > 1_000_000) return `R$ ${numero(n / 1_000_000, 2)} mi`;
  if (n >= 1_000) return `R$ ${numero(n / 1_000, 0)} mil`;
  // O Intl do pt-BR separa "R$" do número com espaço sem quebra (U+00A0);
  // trocamos por espaço comum para o texto ser comparável e copiável.
  return new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'})
    .format(n)
    .replace(/\u00a0/g, ' ');
}

const MODALIDADES: Record<number, string> = {
  4: 'Concorrência eletrônica',
  5: 'Concorrência presencial',
  6: 'Pregão eletrônico',
  7: 'Pregão presencial',
};

export const nomeModalidade = (id: number): string => MODALIDADES[id] ?? `Modalidade ${id}`;

const LIMITE_DESATUALIZADO_MS = 36 * 60 * 60 * 1000;

export function estaDesatualizado(atualizadoEm: string | null, agora: Date): boolean {
  return atualizadoEm === null || agora.getTime() - instante(atualizadoEm) > LIMITE_DESATUALIZADO_MS;
}

/** Linhas de serviço da FR, na ordem dos filtros: valor da API → nome de tela. */
export const LINHAS: Record<string, string> = {
  redes: 'Redes',
  perdas: 'Perdas',
  comercial: 'Comercial',
  consultoria: 'Consultoria',
};

export const nomeLinha = (linha: string): string => LINHAS[linha] ?? linha;

export function localidade(municipio: string | null, uf: string | null): string {
  return municipio === null && uf === null ? '—' : `${municipio ?? '—'}/${uf ?? '—'}`;
}
