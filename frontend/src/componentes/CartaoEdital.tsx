import {ClickableCard} from '@astryxdesign/core/ClickableCard';
import {HStack, VStack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';
import {formatarPrazo, formatarValor, localidade} from '../dados/formatos';
import type {EditalResumo} from '../dados/tipos';
import {SeloFonte} from './SeloFonte';
import {TokensLinhas} from './TokensLinhas';

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
            {edital.modalidade_nome}
          </Text>
        </HStack>
        <Text type="body" maxLines={3} hasTruncateTooltip={false}>
          {edital.objeto}
        </Text>
        <HStack gap={2} vAlign="center" wrap="wrap">
          <Text type="body" weight="semibold">
            {formatarValor(edital.valor_estimado)}
          </Text>
          <SeloFonte fonte={edital.fonte} />
          {edital.linhas.length > 0 && <TokensLinhas linhas={edital.linhas} />}
        </HStack>
      </VStack>
    </ClickableCard>
  );
}
