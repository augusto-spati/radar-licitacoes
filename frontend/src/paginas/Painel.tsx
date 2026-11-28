import {Navigate, useSearchParams} from 'react-router';
import {Grid} from '@astryxdesign/core/Grid';
import {Layout, LayoutContent, LayoutHeader} from '@astryxdesign/core/Layout';
import {VStack} from '@astryxdesign/core/Stack';
import {Heading, Text} from '@astryxdesign/core/Text';
import {AvisoColeta} from '../componentes/AvisoColeta';
import {ContadorPainel} from '../componentes/ContadorPainel';
import {Carregando, ErroAoCarregar} from '../componentes/EstadoCarga';
import {SecaoPainel} from '../componentes/SecaoPainel';
import {useFonte} from '../dados/FonteContext';
import {DIAS_DECIDIR} from '../dados/formatos';
import type {Painel as DadosPainel} from '../dados/tipos';
import {useCarga} from '../dados/useCarga';

// Contadores lado a lado no desktop; no celular (390 px) dois por linha.
const COLUNAS_CONTADORES = {minWidth: 150, max: 3} as const;

// Com perfil de exemplo não há aderência: o contador não filtra por veredito.
const VEREDITOS_DECIDIR = 'veredito=apto&veredito=verificar&veredito=sem_analise';
const hrefDecidir = (perfilExemplo: boolean) =>
  `/editais?encerra_em_dias=${DIAS_DECIDIR}${perfilExemplo ? '' : `&${VEREDITOS_DECIDIR}`}`;

function Conteudo({dados}: {dados: DadosPainel}) {
  const agora = new Date();
  const comAderencia = !dados.perfil_exemplo;
  const decidir = hrefDecidir(dados.perfil_exemplo);
  const secao = (chave: 'decidir' | 'mais_adiante') =>
    dados.secoes.find(s => s.chave === chave) ?? {chave, total: 0, editais: []};
  return (
    <VStack gap={6}>
      <AvisoColeta falhas={dados.coleta} />
      <Grid columns={COLUNAS_CONTADORES} gap={4}>
        <ContadorPainel valor={dados.contadores.decidir} rotulo="para decidir" href={decidir} />
        <ContadorPainel valor={dados.contadores.novos} rotulo="novos" href="/editais?novos=true" />
        <ContadorPainel valor={dados.contadores.abertos} rotulo="abertos ao todo" href="/editais" />
      </Grid>
      {dados.perfil_exemplo && (
        <Text type="supporting" color="secondary">
          Aderência indisponível: perfil não carregado.
        </Text>
      )}
      <SecaoPainel
        titulo="Decidir"
        descricao={`Sem triagem, encerra em até ${DIAS_DECIDIR} dias`}
        vazio="Nada para decidir agora."
        secao={secao('decidir')}
        agora={agora}
        comAderencia={comAderencia}
        hrefLista={decidir}
      />
      <SecaoPainel
        titulo="Mais adiante"
        descricao={`Sem triagem, encerra em mais de ${DIAS_DECIDIR} dias`}
        vazio="Nada mais adiante."
        secao={secao('mais_adiante')}
        agora={agora}
        comAderencia={comAderencia}
        recolhida
        hrefLista="/editais"
      />
    </VStack>
  );
}

// Home de leitura: contadores que levam à Lista filtrada, faixa da coleta e as
// seções Decidir e Mais adiante. Link antigo da Lista (#/?linha=...&uf=...)
// cai aqui e segue para /editais com a mesma consulta.
export function Painel() {
  const [consulta] = useSearchParams();
  if (consulta.has('linha') || consulta.has('uf')) {
    return <Navigate to={`/editais?${consulta.toString()}`} replace />;
  }
  return <PainelCarregado />;
}

function PainelCarregado() {
  const fonte = useFonte();
  const {carga, tentarDeNovo} = useCarga(() => fonte.painel(), 'painel');
  return (
    <Layout
      header={
        <LayoutHeader hasDivider padding={6}>
          <Heading level={1}>Painel</Heading>
        </LayoutHeader>
      }
      content={
        <LayoutContent padding={6}>
          {carga.estado === 'carregando' && (
            <Carregando rotulo="Carregando o painel" alturas={[96, 160, 160]} />
          )}
          {carga.estado === 'erro' && (
            <ErroAoCarregar
              titulo="Não foi possível carregar o painel"
              descricao="Verifique a conexão e tente de novo."
              onTentarDeNovo={tentarDeNovo}
            />
          )}
          {carga.estado === 'pronto' && <Conteudo dados={carga.dados} />}
        </LayoutContent>
      }
    />
  );
}
