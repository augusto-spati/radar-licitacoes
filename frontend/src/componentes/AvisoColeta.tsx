import {Banner} from '@astryxdesign/core/Banner';
import {Collapsible} from '@astryxdesign/core/Collapsible';
import {VStack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';
import {formatarDataHora, NOMES_FONTE} from '../dados/formatos';
import type {FalhaColeta} from '../dados/tipos';

function frase({fonte, motivo, dados_de}: FalhaColeta): string {
  const nome = NOMES_FONTE[fonte];
  if (motivo === 'falha') {
    return `A última leitura de ${nome} falhou; os editais dela são de ${formatarDataHora(dados_de)}`;
  }
  return dados_de === null
    ? `${nome} ainda não teve nenhuma leitura concluída`
    : `${nome} pode estar desatualizado desde ${formatarDataHora(dados_de)}`;
}

/** Faixa do painel quando alguma fonte falhou ou está atrasada; some quando está tudo em dia. */
export function AvisoColeta({falhas}: {falhas: FalhaColeta[]}) {
  if (falhas.length === 0) return null;
  if (falhas.length === 1) return <Banner status="warning" title={frase(falhas[0])} />;
  return (
    <Banner
      status="warning"
      title={`${falhas.length} fontes podem estar desatualizadas`}
      collapsible={false}
    >
      <Collapsible trigger="detalhes" defaultIsOpen={false}>
        <VStack gap={1}>
          {falhas.map(f => (
            <Text key={f.fonte} type="supporting">
              {frase(f)}
            </Text>
          ))}
        </VStack>
      </Collapsible>
    </Banner>
  );
}
