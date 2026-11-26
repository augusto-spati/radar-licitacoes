import {describe, expect, it} from 'vitest';
import {
  estaDesatualizado,
  formatarCnpj,
  formatarDataHora,
  formatarHora,
  formatarPrazo,
  formatarValor,
  linkPncp,
  localidade,
  nomeLinha,
  nomeModalidade,
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
  it('fora do formato devolve como veio', () => {
    expect(formatarCnpj('123')).toBe('123');
  });
});

describe('linkPncp', () => {
  it('monta o link do edital no PNCP a partir do número de controle', () => {
    expect(linkPncp('13127340000120-1-000036/2025')).toBe(
      'https://pncp.gov.br/app/editais/13127340000120/2025/36',
    );
  });
  it('número fora do formato não gera link', () => {
    expect(linkPncp('qualquer-coisa')).toBeNull();
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
