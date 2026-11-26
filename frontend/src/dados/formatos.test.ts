import {describe, expect, expectTypeOf, it} from 'vitest';
import type {Linha, UF} from './tipos';
import {
  LINHAS,
  UFS,
  ehLinha,
  ehUF,
  estaDesatualizado,
  formatarCnpj,
  formatarDataHora,
  formatarHora,
  formatarPrazo,
  formatarValor,
  localidade,
  nomeLinha,
  NOMES_FONTE,
  idNaFonte,
} from './formatos';

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
  it('perto de 1 milhão não escreve "R$ 1.000 mil"', () => {
    expect(formatarValor('999499')).toBe('R$ 999 mil');
    expect(formatarValor('999500')).toBe('R$ 1,00 mi');
    expect(formatarValor('999999.99')).toBe('R$ 1,00 mi');
    expect(formatarValor('1000000')).toBe('R$ 1,00 mi');
  });
  it('texto que não é número vira travessão', () => {
    expect(formatarValor('abc')).toBe('—');
    expect(formatarValor('')).toBe('—');
  });
});

describe('formatarPrazo', () => {
  it('nulo vira travessão', () => {
    expect(formatarPrazo(null, agora)).toBe('—');
  });
  it('hoje mostra a hora', () => {
    expect(formatarPrazo('2026-11-26T14:30:00', agora)).toBe('encerra hoje · 14:30');
  });
  it('amanhã diz "amanhã", não "1 dias"', () => {
    expect(formatarPrazo('2026-11-27T08:00:00', agora)).toBe('encerra amanhã · 27/11');
  });
  it('dias futuros contam por dia de calendário em Brasília', () => {
    expect(formatarPrazo('2026-11-28T08:00:00', agora)).toBe('encerra em 2 dias · 28/11');
    expect(formatarPrazo('2026-12-10T08:00:00', agora)).toBe('encerra em 14 dias · 10/12');
  });
  it('perto da meia-noite de Brasília o dia não escorrega por UTC', () => {
    // 23:30 em Brasília de 26/11 já é 27/11 em UTC.
    const tarde = new Date('2026-11-26T23:30:00-03:00');
    expect(formatarPrazo('2026-11-27T08:00:00', tarde)).toBe('encerra amanhã · 27/11');
    expect(formatarPrazo('2026-11-26T23:59:00', tarde)).toBe('encerra hoje · 23:59');
  });
  it('já encerrado mostra a data', () => {
    expect(formatarPrazo('2026-11-20T08:00:00', agora)).toBe('encerrado · 20/11');
    expect(formatarPrazo('2026-11-26T09:00:00', agora)).toBe('encerrado · 26/11');
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

describe('formatarDataHora', () => {
  it('mostra dd/mm/aaaa HH:MM no relógio de Brasília', () => {
    expect(formatarDataHora('2026-11-25T10:16:40')).toBe('25/11/2026 10:16');
    expect(formatarDataHora('2026-11-26T01:30:00Z')).toBe('25/11/2026 22:30');
  });
  it('nulo vira travessão', () => {
    expect(formatarDataHora(null)).toBe('—');
  });
});

describe('formatarHora', () => {
  it('mostra HH:MM em Brasília, com ou sem fuso no texto', () => {
    expect(formatarHora('2026-11-26T13:09:37.476379-03:00')).toBe('13:09');
    expect(formatarHora('2026-11-26T16:05:00Z')).toBe('13:05');
  });
});

describe('formatarCnpj', () => {
  it('aplica a máscara de 14 dígitos', () => {
    expect(formatarCnpj('13127340000120')).toBe('13.127.340/0001-20');
  });
  it('fora do formato devolve como veio; nulo (fontes sem CNPJ) vira travessão', () => {
    expect(formatarCnpj('123')).toBe('123');
    expect(formatarCnpj(null)).toBe('—');
  });
});

describe('fontes', () => {
  it('cada fonte tem seu nome de tela', () => {
    expect(NOMES_FONTE).toEqual({pncp: 'PNCP', sanepar: 'Sanepar', compesa: 'Compesa'});
  });
  it('idNaFonte tira o prefixo "fonte~" e mantém o resto', () => {
    expect(idNaFonte('sanepar~OE-345/26')).toBe('OE-345/26');
    expect(idNaFonte('pncp~13127340000120-1-000036/2025')).toBe('13127340000120-1-000036/2025');
    expect(idNaFonte('sanepar~OE~estranho')).toBe('OE~estranho');
    expect(idNaFonte('sem-separador')).toBe('sem-separador');
  });
});

describe('localidade', () => {
  it('junta município e UF; o que faltar vira travessão', () => {
    expect(localidade('Holambra', 'SP')).toBe('Holambra/SP');
    expect(localidade(null, 'SP')).toBe('—/SP');
    expect(localidade(null, null)).toBe('—');
  });
});

describe('nomeLinha', () => {
  it('dá o nome de tela das linhas de serviço', () => {
    expect(nomeLinha('redes')).toBe('Redes');
    expect(nomeLinha('consultoria')).toBe('Consultoria');
    expect(nomeLinha('outra')).toBe('outra');
  });
});

describe('linhas e UFs vêm do contrato da API (config/filtro.toml)', () => {
  it('LINHAS tem exatamente as linhas do api.d.ts e UFS exatamente as UFs', () => {
    // Checagem de tipo (tsc): linha ou UF a mais ou a menos não compila.
    expectTypeOf<keyof typeof LINHAS>().toEqualTypeOf<Linha>();
    expectTypeOf<(typeof UFS)[number]>().toEqualTypeOf<UF>();
    expect(Object.keys(LINHAS)).toEqual(['redes', 'perdas', 'comercial', 'consultoria']);
    expect(UFS).toEqual(['SP', 'PR', 'PE', 'MG']);
  });
  it('ehLinha e ehUF só aceitam valores do contrato', () => {
    expect(ehLinha('redes')).toBe(true);
    expect(ehLinha('inexistente')).toBe(false);
    expect(ehLinha('toString')).toBe(false);
    expect(ehUF('SP')).toBe(true);
    expect(ehUF('BA')).toBe(false);
  });
});
