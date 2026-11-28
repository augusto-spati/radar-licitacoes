import {Collapsible} from '@astryxdesign/core/Collapsible';
import {Grid} from '@astryxdesign/core/Grid';
import {Link} from '@astryxdesign/core/Link';
import {VStack} from '@astryxdesign/core/Stack';
import {Heading, Text} from '@astryxdesign/core/Text';
import type {SecaoDoPainel} from '../dados/tipos';
import {CartaoEdital} from './CartaoEdital';

// A mesma grade da Lista: uma coluna no celular (390 px), mais conforme a tela abre.
const COLUNAS = {minWidth: 280, max: 3} as const;

type Props = {
  titulo: string;
  descricao: string;
  vazio: string;
  secao: SecaoDoPainel;
  agora: Date;
  comAderencia: boolean;
  recolhida?: boolean;
  hrefLista: string;
  /** Texto do link quando o destino não tem o mesmo recorte da seção (sem número). */
  rotuloLista?: string;
};

/** Uma seção do painel: título, o que entra nela e os cartões (ou uma linha, se vazia). */
export function SecaoPainel(props: Props) {
  const {titulo, descricao, vazio, secao, agora, comAderencia, recolhida = false} = props;
  const conteudo =
    secao.editais.length === 0 ? (
      <Text type="body" color="secondary">
        {vazio}
      </Text>
    ) : (
      <VStack gap={4}>
        <Grid columns={COLUNAS} gap={4}>
          {secao.editais.map(e => (
            <CartaoEdital
              key={e.id_publico}
              edital={e}
              agora={agora}
              comAderencia={comAderencia}
              href={`/edital/${encodeURIComponent(e.id_publico)}`}
            />
          ))}
        </Grid>
        {secao.total > secao.editais.length && (
          <Link href={props.hrefLista} isStandalone>
            {props.rotuloLista ?? `Ver os ${secao.total} na lista`}
          </Link>
        )}
      </VStack>
    );
  return (
    <VStack gap={3}>
      <VStack gap={1}>
        <Heading level={2}>{titulo}</Heading>
        <Text type="supporting" color="secondary">
          {descricao}
        </Text>
      </VStack>
      {recolhida ? (
        <Collapsible trigger={`Mostrar ${titulo.toLowerCase()}`} defaultIsOpen={false}>
          {conteudo}
        </Collapsible>
      ) : (
        conteudo
      )}
    </VStack>
  );
}
