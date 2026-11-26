import {HStack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';
import {Token} from '@astryxdesign/core/Token';
import {nomeLinha} from '../dados/formatos';

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
