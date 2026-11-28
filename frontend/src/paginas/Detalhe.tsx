import {useLocation, useParams} from 'react-router';
import {ArrowLeft, ExternalLink} from 'lucide-react';
import {Button} from '@astryxdesign/core/Button';
import {EmptyState} from '@astryxdesign/core/EmptyState';
import {Icon} from '@astryxdesign/core/Icon';
import {Layout, LayoutContent, LayoutHeader} from '@astryxdesign/core/Layout';
import {Link} from '@astryxdesign/core/Link';
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {Section} from '@astryxdesign/core/Section';
import {HStack, VStack} from '@astryxdesign/core/Stack';
import {Heading, Text} from '@astryxdesign/core/Text';
import {rtlStyles} from '@astryxdesign/core/utils';
import {Aderencia} from '../componentes/Aderencia';
import {Carregando, ErroAoCarregar} from '../componentes/EstadoCarga';
import {HistoricoOrgao} from '../componentes/HistoricoOrgao';
import {PrecosReferencia} from '../componentes/PrecosReferencia';
import {TokensLinhas} from '../componentes/TokensLinhas';
import {useFonte} from '../dados/FonteContext';
import {
  formatarCnpj,
  formatarDataHora,
  formatarPrazo,
  formatarValor,
  NOMES_FONTE,
  idNaFonte,
  linkHttp,
  localidade,
} from '../dados/formatos';
import type {EditalDetalhe} from '../dados/tipos';
import {useCarga} from '../dados/useCarga';

// A lista navega com o id num segmento só (a barra vira %2F); o roteador já
// devolve o parâmetro decodificado, com a barra de volta.
function useIdDoEdital(): string {
  return useParams().id ?? '';
}

function Voltar() {
  // A consulta (?linha=&uf=) veio da lista e volta com ela.
  const {search} = useLocation();
  return (
    <Link href={`/editais${search}`} color="secondary">
      <HStack gap={1} vAlign="center">
        <Icon icon={ArrowLeft} size="sm" color="inherit" xstyle={rtlStyles.mirror} />
        Voltar
      </HStack>
    </Link>
  );
}

function Cabecalho({edital}: {edital?: EditalDetalhe}) {
  // A API já manda nulo no lugar de "" ou link sem http(s); o front confere de novo.
  // O link de reserva por fonte já vem da API em `link_origem`.
  const destino = linkHttp(edital?.link_origem);
  return (
    <LayoutHeader hasDivider padding={4}>
      <VStack gap={3}>
        <VStack gap={0}>
          <Voltar />
          {edital && (
            <VStack gap={0}>
              <Heading level={1} maxLines={2}>
                {edital.orgao_nome}
              </Heading>
              <HStack gap={2} vAlign="center" wrap="wrap">
                <Text type="body">{formatarPrazo(edital.data_encerramento_proposta, new Date())}</Text>
                <Text type="body" color="secondary">
                  {localidade(edital.municipio, edital.uf)}
                </Text>
              </HStack>
            </VStack>
          )}
        </VStack>
        {destino !== null && (
          <HStack gap={2}>
            <Button
              label="Abrir no sistema de origem"
              variant="primary"
              href={destino}
              target="_blank"
              rel="noopener noreferrer"
              endContent={<Icon icon={ExternalLink} size="sm" />}
            />
          </HStack>
        )}
      </VStack>
    </LayoutHeader>
  );
}

// A análise carrega à parte: se ela falhar, os dados do edital continuam na tela.
function AderenciaDoEdital({id}: {id: string}) {
  const fonte = useFonte();
  const {carga, tentarDeNovo} = useCarga(() => fonte.analise(id), id);
  return <Aderencia carga={carga} onTentarDeNovo={tentarDeNovo} />;
}

// O histórico do órgão também carrega à parte, pelo mesmo motivo.
function HistoricoDoEdital({id}: {id: string}) {
  const fonte = useFonte();
  const {carga, tentarDeNovo} = useCarga(() => fonte.historico(id), id);
  return <HistoricoOrgao carga={carga} onTentarDeNovo={tentarDeNovo} />;
}

// E os preços de referência, idem.
function PrecosDoEdital({id}: {id: string}) {
  const fonte = useFonte();
  const {carga, tentarDeNovo} = useCarga(() => fonte.precos(id), id);
  return <PrecosReferencia carga={carga} onTentarDeNovo={tentarDeNovo} />;
}

function Conteudo({edital}: {edital: EditalDetalhe}) {
  return (
    <VStack gap={4}>
      <Section>
        <VStack gap={2}>
          <Heading level={2}>Objeto</Heading>
          <Text type="body">{edital.objeto}</Text>
        </VStack>
      </Section>
      <Section>
        <VStack gap={2}>
          <Heading level={2}>Dados do edital</Heading>
          <MetadataList>
            <MetadataListItem label="Órgão">{edital.orgao_nome}</MetadataListItem>
            <MetadataListItem label="CNPJ do órgão">{formatarCnpj(edital.orgao_cnpj)}</MetadataListItem>
            <MetadataListItem label="Município/UF">{localidade(edital.municipio, edital.uf)}</MetadataListItem>
            <MetadataListItem label="Modalidade">{edital.modalidade_nome}</MetadataListItem>
            <MetadataListItem label="Situação">{edital.situacao_nome}</MetadataListItem>
            <MetadataListItem label="Valor estimado">{formatarValor(edital.valor_estimado)}</MetadataListItem>
            <MetadataListItem label="Publicação">{formatarDataHora(edital.data_publicacao)}</MetadataListItem>
            <MetadataListItem label="Abertura das propostas">
              {formatarDataHora(edital.data_abertura_proposta)}
            </MetadataListItem>
            <MetadataListItem label="Encerramento das propostas">
              {formatarDataHora(edital.data_encerramento_proposta)}
            </MetadataListItem>
            <MetadataListItem label="Linhas de serviço">
              <TokensLinhas linhas={edital.linhas} />
            </MetadataListItem>
            <MetadataListItem label="Fonte">{NOMES_FONTE[edital.fonte]}</MetadataListItem>
            <MetadataListItem label="Identificador na fonte">
              {idNaFonte(edital.id_publico)}
            </MetadataListItem>
          </MetadataList>
        </VStack>
      </Section>
      <Section>
        <AderenciaDoEdital id={edital.id_publico} />
      </Section>
      <Section>
        <HistoricoDoEdital id={edital.id_publico} />
      </Section>
      <Section>
        <PrecosDoEdital id={edital.id_publico} />
      </Section>
    </VStack>
  );
}

// A partir do template `detail-page`: cabeçalho com "Voltar", título,
// metadados e a ação principal; seções empilhadas numa coluna (a coluna
// lateral do template ficou de fora: o detalhe cabe numa coluna só).
export function Detalhe() {
  const fonte = useFonte();
  const id = useIdDoEdital();
  const {carga, tentarDeNovo} = useCarga(() => fonte.obter(id), id);
  const edital = carga.estado === 'pronto' ? (carga.dados ?? undefined) : undefined;

  return (
    <Layout
      contentWidth={1000}
      defaultHasDividers
      header={<Cabecalho edital={edital} />}
      content={
        <LayoutContent padding={4}>
          {carga.estado === 'carregando' && (
            <Carregando rotulo="Carregando edital" alturas={[120, 320]} />
          )}
          {carga.estado === 'erro' && (
            <ErroAoCarregar titulo="Não foi possível carregar o edital" onTentarDeNovo={tentarDeNovo} />
          )}
          {carga.estado === 'pronto' &&
            (edital ? (
              <Conteudo edital={edital} />
            ) : (
              <EmptyState
                title="Edital não encontrado"
                description="Ele pode ter saído do PNCP ou o endereço está incompleto."
              />
            ))}
        </LayoutContent>
      }
    />
  );
}
