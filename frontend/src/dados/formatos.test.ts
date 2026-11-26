import {describe, expect, it} from 'vitest';
import {estaDesatualizado, formatarPrazo, formatarValor, nomeModalidade} from './formatos';

// 26/11/2026 12:00 em Brasília.
const agora = new Date('2026-11-26T12:00:00-03:00');

describe('formatarValor', () => {
  it('acima de 1 milhão usa "mi" com 2 casas', () => {
    expect(formatarValor('1369661.52')).toBe('R$ 1,37 mi');
  });
  it('a partir de mil usa "mil" sem casas', () => {
    expect(formatarValor('845000')).toBe('R$ 845 mil');
    expect(formatarValor('1000')).toBe('R$ 1 mil');
  });
  it('abaixo de mil mostra o valor cheio, com espaço comum (sem NBSP)', () => {
    expect(formatarValor('999.5')).toBe('R$ 999,50');
    expect(formatarValor('0')).toBe('R$ 0,00');
  });
  it('nulo vira travessão', () => {
    expect(formatarValor(null)).toBe('—');
  });
});

describe('formatarPrazo', () => {
  it('nulo vira travessão', () => {
    expect(formatarPrazo(null, agora)).toBe('—');
  });
  it('hoje mostra a hora', () => {
    expect(formatarPrazo('2026-11-26T14:30:00', agora)).toBe('encerra hoje · 14:30');
  });
  it('dias futuros contam por dia de calendário em Brasília', () => {
    expect(formatarPrazo('2026-11-27T08:00:00', agora)).toBe('encerra em 1 dias · 27/11');
    expect(formatarPrazo('2026-12-10T08:00:00', agora)).toBe('encerra em 14 dias · 10/12');
  });
  it('perto da meia-noite de Brasília o dia não escorrega por UTC', () => {
    // 23:30 em Brasília de 26/11 já é 27/11 em UTC.
    const tarde = new Date('2026-11-26T23:30:00-03:00');
    expect(formatarPrazo('2026-11-27T08:00:00', tarde)).toBe('encerra em 1 dias · 27/11');
    expect(formatarPrazo('2026-11-26T23:59:00', tarde)).toBe('encerra hoje · 23:59');
  });
  it('já encerrado mostra a data', () => {
    expect(formatarPrazo('2026-11-20T08:00:00', agora)).toBe('encerrado · 20/11');
    expect(formatarPrazo('2026-11-26T09:00:00', agora)).toBe('encerrado · 26/11');
  });
});

describe('nomeModalidade', () => {
  it('traduz as quatro modalidades', () => {
    expect(nomeModalidade(4)).toBe('Concorrência eletrônica');
    expect(nomeModalidade(5)).toBe('Concorrência presencial');
    expect(nomeModalidade(6)).toBe('Pregão eletrônico');
    expect(nomeModalidade(7)).toBe('Pregão presencial');
  });
  it('desconhecida cai num texto genérico', () => {
    expect(nomeModalidade(99)).toBe('Modalidade 99');
  });
});

describe('estaDesatualizado', () => {
  it('nulo conta como desatualizado', () => {
    expect(estaDesatualizado(null, agora)).toBe(true);
  });
  it('até 36 h está atualizado; acima, não', () => {
    expect(estaDesatualizado('2026-11-25T00:00:00-03:00', agora)).toBe(false); // 36 h exatas
    expect(estaDesatualizado('2026-11-24T23:59:00-03:00', agora)).toBe(true);
    expect(estaDesatualizado('2026-11-26T11:00:00-03:00', agora)).toBe(false);
  });
});
