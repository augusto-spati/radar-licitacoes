import {List, ListItem} from '@astryxdesign/core/List';
import {VStack} from '@astryxdesign/core/Stack';
import {Heading, Text} from '@astryxdesign/core/Text';
import {Token} from '@astryxdesign/core/Token';
import {formatarData, formatarPercentual, formatarValor} from '../dados/formatos';
import type {ContratacaoPassada, Historico, Vencedor} from '../dados/tipos';
import type {Carga} from '../dados/useCarga';
import {Carregando, ErroAoCarregar} from './EstadoCarga';
import {ReguaDesconto} from './ReguaDesconto';

type CorToken = 'yellow' | 'red' | 'gray' | 'blue';

// Situações de `resultado_busca` (src/radar/editais/resultados.py). Homologada
// não leva marca; situação nova sem nome aqui aparece como veio da API.
const SITUACOES: Record<string, {rotulo: string; cor: CorToken} | null> = {
  homologada: null,
  parcial: {rotulo: 'parcial', cor: 'yellow'},
  em_andamento: {rotulo: 'em andamento', cor: 'blue'},
  fracassada: {rotulo: 'fracassada', cor: 'red'},
  deserta: {rotulo: 'deserta', cor: 'gray'},
  anulada: {rotulo: 'anulada', cor: 'gray'},
  sem_itens: {rotulo: 'sem itens', cor: 'gray'},
};

const plural = (n: number, um: string, varios: string) => `${n} ${n === 1 ? um : varios}`;

/** "12 licitações · 8 homologadas · 3 fracassadas ou desertas · 1 em andamento" (sem os zeros). */
function contagens({contagens: c, total}: Historico): string {
  const n = (...situacoes: string[]) => situacoes.reduce((soma, s) => soma + (c[s] ?? 0), 0);
  const partes: [number, string, string][] = [
    [n('homologada'), 'homologada', 'homologadas'],
    [n('fracassada', 'deserta'), 'fracassada ou deserta', 'fracassadas ou desertas'],
    [n('em_andamento', 'parcial'), 'em andamento', 'em andamento'],
  ];
  const resto = partes.filter(([qtd]) => qtd > 0).map(p => plural(...p));
  return [plural(total, 'licitação', 'licitações'), ...resto].join(' · ');
}

const marcaDa = (situacao: string) =>
  Object.hasOwn(SITUACOES, situacao)
    ? SITUACOES[situacao]
    : {rotulo: situacao, cor: 'gray' as const};

const SEM_HISTORICO = (
  <Text type="body" color="secondary">
    Sem histórico deste órgão no recorte.
  </Text>
);

function QuemVence({vencedor}: {vencedor: Vencedor}) {
  const detalhes = [
    vencedor.porte,
    plural(vencedor.vitorias, 'vitória', 'vitórias'),
    vencedor.desconto_mediano === null
      ? null
      : `desconto mediano ${formatarPercentual(vencedor.desconto_mediano)}`,
  ].filter(d => d !== null);
  return <ListItem label={vencedor.nome} description={detalhes.join(' · ')} />;
}

function Contratacao({contratacao: c}: {contratacao: ContratacaoPassada}) {
  const marca = marcaDa(c.situacao);
  const valores =
    `${formatarData(c.data)} · estimado ${formatarValor(c.valor_estimado)} · ` +
    `homologado ${formatarValor(c.valor_homologado)} · desconto ${formatarPercentual(c.desconto)}`;
  return (
    <ListItem
      align="start"
      label={c.objeto}
      endContent={
        marca === null ? undefined : <Token label={marca.rotulo} size="sm" color={marca.cor} />
      }
      description={
        <VStack gap={1}>
          <Text type="supporting">{valores}</Text>
          {c.vencedor_nome !== null && (
            <Text type="supporting">
              Vencedor: {c.vencedor_nome}
              {c.vencedor_porte === null ? '' : ` (${c.vencedor_porte})`}
            </Text>
          )}
        </VStack>
      }
    />
  );
}

function Resumo({historico}: {historico: Historico}) {
  if (historico.total === 0) {
    return (
      <VStack gap={2}>
        {SEM_HISTORICO}
        <ReguaDesconto reguas={[]} limite={historico.limite_inexequivel} />
      </VStack>
    );
  }
  return (
    <VStack gap={3}>
      <Text type="body">{contagens(historico)}</Text>
      {historico.vencedores.length > 0 && (
        <List hasDividers header={<Heading level={3}>Quem vence</Heading>}>
          {historico.vencedores.map(v => (
            <QuemVence key={v.nome} vencedor={v} />
          ))}
        </List>
      )}
      {historico.ultimas.length > 0 && (
        <List hasDividers header={<Heading level={3}>Últimas contratações</Heading>}>
          {historico.ultimas.map(c => (
            <Contratacao key={c.id_publico} contratacao={c} />
          ))}
        </List>
      )}
      <ReguaDesconto reguas={historico.reguas} limite={historico.limite_inexequivel} />
    </VStack>
  );
}

type Props = {carga: Carga<Historico | null>; onTentarDeNovo: () => void};

/** Bloco "Histórico do órgão" do detalhe: contratações passadas, vencedores e régua. */
export function HistoricoOrgao({carga, onTentarDeNovo}: Props) {
  return (
    <VStack gap={2}>
      <VStack gap={0}>
        <Heading level={2}>Histórico do órgão</Heading>
        <Text type="supporting" color="secondary">
          Últimos 24 meses, serviços de saneamento
        </Text>
      </VStack>
      {carga.estado === 'carregando' && <Carregando rotulo="Carregando histórico" alturas={[80]} />}
      {carga.estado === 'erro' && (
        <ErroAoCarregar
          titulo="Não foi possível carregar o histórico"
          onTentarDeNovo={onTentarDeNovo}
        />
      )}
      {carga.estado === 'pronto' &&
        (carga.dados === null ? SEM_HISTORICO : <Resumo historico={carga.dados} />)}
    </VStack>
  );
}
