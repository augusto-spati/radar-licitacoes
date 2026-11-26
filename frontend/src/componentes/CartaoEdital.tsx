import {ClickableCard} from '@astryxdesign/core/ClickableCard';
import {HStack, VStack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';
import {Token} from '@astryxdesign/core/Token';
import {formatarPrazo, formatarValor, localidade, nomeLinha, nomeModalidade} from '../dados/formatos';
import type {EditalResumo} from '../dados/tipos';

/** As linhas de serviço do edital como tokens; sem linha, um travessão. */
export function TokensLinhas({linhas}: {linhas: string[]}) {
  if (linhas.length === 0) return <Text type="body">—</Text>;
  return (
    <HStack gap={1} wrap="wrap">
      {linhas.map(linha => (
        <Token key={linha} label={nomeLinha(linha)} size="sm" color="blue" />
      ))}
    </HStack>
  );
}

type Props = {edital: EditalResumo; href: string; agora: Date};

// Cartão do template `library` (LibraryCard), sem a miniatura: prazo em
// destaque, onde e como, o objeto em até 3 linhas, valor e linhas.
export function CartaoEdital({edital, href, agora}: Props) {
  return (
    <ClickableCard label={`Abrir edital: ${edital.objeto}`} href={href} padding={4}>
      <VStack gap={2}>
        <Text type="label" weight="semibold">
          {formatarPrazo(edital.data_encerramento_proposta, agora)}
        </Text>
        <HStack gap={2} wrap="wrap">
          <Text type="supporting" color="secondary">
            {localidade(edital.municipio, edital.uf)}
          </Text>
          <Text type="supporting" color="secondary">
            {nomeModalidade(edital.modalidade_id)}
          </Text>
        </HStack>
        <Text type="body" maxLines={3} hasTruncateTooltip={false}>
          {edital.objeto}
        </Text>
        <HStack gap={2} vAlign="center" wrap="wrap">
          <Text type="body" weight="semibold">
            {formatarValor(edital.valor_estimado)}
          </Text>
          {edital.linhas.length > 0 && <TokensLinhas linhas={edital.linhas} />}
        </HStack>
      </VStack>
    </ClickableCard>
  );
}
