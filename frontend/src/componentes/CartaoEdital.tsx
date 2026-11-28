import {ClickableCard} from '@astryxdesign/core/ClickableCard';
import {HStack, VStack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';
import {Token} from '@astryxdesign/core/Token';
import {ehNovo, formatarPrazo, formatarValor, localidade, nomeCampo} from '../dados/formatos';
import type {EditalResumo} from '../dados/tipos';
import {SeloFonte} from './SeloFonte';
import {TokensLinhas} from './TokensLinhas';

type Props = {edital: EditalResumo; href: string; agora: Date; comAderencia?: boolean};

/** Selo de aderência do resumo: veredito da análise ou, sem ela, "conferir edital". */
type PropsAderencia = {veredito: EditalResumo['veredito']; motivo: string | null};

function SeloAderencia({veredito, motivo}: PropsAderencia) {
  if (veredito === 'apto') return <Token label="apto" size="sm" color="green" />;
  if (veredito === 'inapto') return <Token label="inapto" size="sm" color="red" />;
  if (veredito === 'verificar') {
    const rotulo = motivo === null ? 'verificar' : `verificar: ${nomeCampo(motivo)}`;
    return <Token label={rotulo} size="sm" color="yellow" />;
  }
  return <Token label="sem análise · conferir edital" size="sm" color="gray" />;
}

// Cartão do template `library` (LibraryCard), sem a miniatura: prazo em
// destaque, onde e como, o objeto em até 3 linhas, valor e linhas.
export function CartaoEdital({edital, href, agora, comAderencia = false}: Props) {
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
          {ehNovo(edital.primeiro_visto_em, agora) && <Token label="novo" size="sm" color="blue" />}
          {comAderencia && (
            <SeloAderencia veredito={edital.veredito} motivo={edital.motivo_verificar} />
          )}
          {edital.linhas.length > 0 && <TokensLinhas linhas={edital.linhas} />}
        </HStack>
      </VStack>
    </ClickableCard>
  );
}
