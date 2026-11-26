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
const diaCalendario = (d: Date) => Math.floor(d.getTime() / UM_DIA_MS);

export function formatarPrazo(iso: string | null, agora: Date): string {
  if (iso === null) return '—';
  const alvo = instante(iso);
  const parede = paredeBrasilia(alvo);
  if (alvo < agora.getTime()) return `encerrado · ${diaMes(parede)}`;
  const dias = diaCalendario(parede) - diaCalendario(paredeBrasilia(agora.getTime()));
  if (dias === 0) return `encerra hoje · ${dois(parede.getUTCHours())}:${dois(parede.getUTCMinutes())}`;
  return `encerra em ${dias} dias · ${diaMes(parede)}`;
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
