import {List, ListItem} from '@astryxdesign/core/List';
import {VStack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';
import {Token} from '@astryxdesign/core/Token';
import {formatarPercentual, nomeLinha} from '../dados/formatos';
import type {Regua} from '../dados/tipos';

// Faixas de valor estimado da régua (src/radar/editais/historico.py). Faixa nova
// sem nome aqui aparece como veio da API.
const FAIXAS: Record<string, string> = {
  ate_1m: 'até R$ 1 mi',
  '1m_a_5m': 'R$ 1–5 mi',
  acima_5m: 'acima de R$ 5 mi',
};

const casos = (n: number) => (n === 1 ? '1 caso' : `${n} casos`);

function recorte(regua: Regua): string {
  const linha = regua.linha === null ? 'todas as linhas' : nomeLinha(regua.linha);
  const uf = regua.uf ?? 'todas as UFs';
  const faixa = regua.faixa === null ? 'qualquer valor' : (FAIXAS[regua.faixa] ?? regua.faixa);
  return `${linha}, ${uf}, ${faixa}`;
}

function faixaDeDesconto(regua: Regua): string {
  if (regua.casos === 0) return 'nenhum caso';
  const {p25, mediana, p75} = regua;
  return (
    `${formatarPercentual(p25)} a ${formatarPercentual(p75)} ` +
    `(mediana ${formatarPercentual(mediana)}), ${casos(regua.casos)}`
  );
}

type Props = {reguas: Regua[]; limite: string};

/** Faixa de desconto dos vencedores (uma por linha do edital) e o limite de inexequibilidade. */
export function ReguaDesconto({reguas, limite}: Props) {
  return (
    <VStack gap={2}>
      {reguas.length > 0 && (
        <List hasDividers>
          {reguas.map((regua, i) => (
            <ListItem
              key={i}
              align="start"
              label={
                <Text type="body">
                  Faixa de desconto dos vencedores ({recorte(regua)}): {faixaDeDesconto(regua)}
                </Text>
              }
              endContent={
                regua.poucos_casos && regua.casos > 0 ? (
                  <Token label="poucos casos" size="sm" color="yellow" />
                ) : undefined
              }
            />
          ))}
        </List>
      )}
      <Text type="supporting" color="secondary">
        Abaixo de {formatarPercentual(limite)} do estimado a proposta é presumida inexequível.
      </Text>
    </VStack>
  );
}
