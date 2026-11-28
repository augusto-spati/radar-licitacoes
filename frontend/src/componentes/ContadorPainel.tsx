import {ClickableCard} from '@astryxdesign/core/ClickableCard';
import {VStack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';

type Props = {valor: number; rotulo: string; href: string};

/** Contador do painel ("9 para decidir"): o cartão inteiro leva à Lista filtrada. */
export function ContadorPainel({valor, rotulo, href}: Props) {
  return (
    <ClickableCard label={`${valor} ${rotulo}`} href={href} padding={4}>
      <VStack gap={1}>
        <Text type="display-3" weight="semibold">
          {valor}
        </Text>
        <Text type="supporting" color="secondary">
          {rotulo}
        </Text>
      </VStack>
    </ClickableCard>
  );
}
