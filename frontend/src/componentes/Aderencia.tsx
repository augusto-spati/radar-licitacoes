import {Banner} from '@astryxdesign/core/Banner';
import {Link} from '@astryxdesign/core/Link';
import {List, ListItem} from '@astryxdesign/core/List';
import {VStack} from '@astryxdesign/core/Stack';
import {Heading, Text} from '@astryxdesign/core/Text';
import {Token} from '@astryxdesign/core/Token';
import {formatarDataHora, linkHttp} from '../dados/formatos';
import type {Analise, DocumentoAnalise, ItemAnalise} from '../dados/tipos';
import type {Carga} from '../dados/useCarga';
import {Carregando, ErroAoCarregar} from './EstadoCarga';

type Veredito = Analise['veredito'];
type Situacao = ItemAnalise['situacao'];
type CorToken = 'green' | 'red' | 'yellow' | 'gray';

const VEREDITOS: Record<Veredito, {status: 'success' | 'error' | 'warning'; titulo: string}> = {
  apto: {status: 'success', titulo: 'Atende ao perfil da FR'},
  inapto: {status: 'error', titulo: 'Não atende ao perfil da FR'},
  verificar: {status: 'warning', titulo: 'Verificar antes de participar'},
};

const SITUACOES: Record<Situacao, {rotulo: string; cor: CorToken}> = {
  atende: {rotulo: 'Atende', cor: 'green'},
  nao_atende: {rotulo: 'Não atende', cor: 'red'},
  verificar: {rotulo: 'Verificar', cor: 'yellow'},
  informativo: {rotulo: 'Informativo', cor: 'gray'},
};

// Campos da comparação (src/radar/analise/comparacao.py). Campo novo sem nome
// aqui aparece como veio da API.
const CAMPOS: Record<string, string> = {
  capital_social_minimo: 'Capital social mínimo',
  patrimonio_liquido_minimo: 'Patrimônio líquido mínimo',
  garantia_proposta_percentual: 'Garantia da proposta',
  garantia_contratual_percentual: 'Garantia contratual',
  visita_tecnica: 'Visita técnica',
  consorcio: 'Consórcio',
  me_epp: 'Participação de ME/EPP',
  prazo_execucao_dias: 'Prazo de execução',
  documentos: 'Documentos de habilitação',
  atestados: 'Atestado técnico',
};

const STATUS_DOCUMENTO: Record<DocumentoAnalise['status'], string | null> = {
  extraido: null,
  sem_texto: 'sem texto (PDF escaneado)',
  nao_pdf: 'não é PDF',
  grande_demais: 'grande demais para ler',
  falha: 'falhou ao baixar',
};

function onde(item: ItemAnalise): string | null {
  if (item.documento === null) return item.pagina === null ? null : `página ${item.pagina}`;
  return item.pagina === null ? item.documento : `${item.documento}, página ${item.pagina}`;
}

function Exigencia({item}: {item: ItemAnalise}) {
  const {rotulo, cor} = SITUACOES[item.situacao];
  const local = onde(item);
  return (
    <ListItem
      align="start"
      label={CAMPOS[item.campo] ?? item.campo}
      endContent={<Token label={rotulo} size="sm" color={cor} />}
      description={
        <VStack gap={1}>
          <Text type="supporting">Exigido: {item.exigido}</Text>
          <Text type="supporting">Perfil da FR: {item.perfil}</Text>
          <Text type="supporting">Por quê: {item.motivo}</Text>
          {item.trecho !== null && (
            <Text type="supporting" color="secondary">
              “{item.trecho}”{local === null ? '' : ` (${local})`}
            </Text>
          )}
        </VStack>
      }
    />
  );
}

function Documento({documento}: {documento: DocumentoAnalise}) {
  const problema = STATUS_DOCUMENTO[documento.status];
  const paginas = documento.paginas === null ? '' : ` (${documento.paginas} páginas)`;
  const destino = linkHttp(documento.url);
  const titulo = `${documento.titulo}${paginas}`;
  return (
    <ListItem
      label={
        destino === null ? (
          titulo
        ) : (
          <Link href={destino} target="_blank" rel="noopener noreferrer">
            {titulo}
          </Link>
        )
      }
      description={problema ?? undefined}
    />
  );
}

function Resultado({analise}: {analise: Analise}) {
  const {status, titulo} = VEREDITOS[analise.veredito];
  const incompleta = analise.incompleta
    ? ' Análise incompleta: algum documento não pôde ser lido.'
    : '';
  return (
    <VStack gap={3}>
      <Banner
        status={status}
        title={titulo}
        description={`${analise.aviso}${incompleta}`}
        collapsible={false}
      />
      {analise.itens.length === 0 ? (
        <Text type="body" color="secondary">
          A IA não achou exigência de habilitação nos documentos lidos.
        </Text>
      ) : (
        <List hasDividers header={<Heading level={3}>Exigências do edital</Heading>}>
          {analise.itens.map((item, i) => (
            <Exigencia key={i} item={item} />
          ))}
        </List>
      )}
      {analise.documentos.length > 0 && (
        <List hasDividers header={<Heading level={3}>Documentos lidos</Heading>}>
          {analise.documentos.map(documento => (
            <Documento key={documento.url} documento={documento} />
          ))}
        </List>
      )}
      <Text type="supporting" color="secondary">
        Analisado em {formatarDataHora(analise.criada_em)} por {analise.modelo}.
      </Text>
    </VStack>
  );
}

type Props = {carga: Carga<Analise | null>; onTentarDeNovo: () => void};

/** Bloco "Aderência à FR" do detalhe: o resultado da análise por IA, quando há. */
export function Aderencia({carga, onTentarDeNovo}: Props) {
  return (
    <VStack gap={2}>
      <Heading level={2}>Aderência à FR</Heading>
      {carga.estado === 'carregando' && <Carregando rotulo="Carregando análise" alturas={[80]} />}
      {carga.estado === 'erro' && (
        <ErroAoCarregar
          titulo="Não foi possível carregar a análise"
          onTentarDeNovo={onTentarDeNovo}
        />
      )}
      {carga.estado === 'pronto' &&
        (carga.dados === null ? (
          <Text type="body" color="secondary">
            Este edital ainda não foi analisado. A análise por IA roda uma vez por dia, em parte dos
            editais abertos do PNCP.
          </Text>
        ) : (
          <Resultado analise={carga.dados} />
        ))}
    </VStack>
  );
}
