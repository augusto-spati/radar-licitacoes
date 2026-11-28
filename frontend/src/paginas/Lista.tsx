import {useSearchParams} from 'react-router';
import {EmptyState} from '@astryxdesign/core/EmptyState';
import {Grid} from '@astryxdesign/core/Grid';
import {Layout, LayoutContent, LayoutHeader} from '@astryxdesign/core/Layout';
import {Button} from '@astryxdesign/core/Button';
import {HStack, VStack} from '@astryxdesign/core/Stack';
import {Heading, Text} from '@astryxdesign/core/Text';
import {AvisoDesatualizado} from '../componentes/AvisoDesatualizado';
import {CartaoEdital} from '../componentes/CartaoEdital';
import {Carregando, ErroAoCarregar} from '../componentes/EstadoCarga';
import {FiltrosEditais} from '../componentes/FiltrosEditais';
import type {Filtros} from '../dados/fonte';
import {useFonte} from '../dados/FonteContext';
import {ehFiltroVeredito, ehLinha, ehUF} from '../dados/formatos';
import {useCarga} from '../dados/useCarga';

// Uma coluna no celular (390 px), mais colunas conforme a tela abre.
const COLUNAS = {minWidth: 280, max: 3} as const;

const CHAVES_RECORTE = ['encerra_em_dias', 'novos', 'veredito'];

// Inteiro de 0 a 365; qualquer outro valor é ignorado.
function lerDias(valor: string | null): number | null {
  if (valor === null || !/^\d+$/.test(valor)) return null;
  const dias = Number(valor);
  return dias <= 365 ? dias : null;
}

// A partir do template `library`: cabeçalho, filtros e grade de cartões, com
// os estados de carregando, vazio e erro. Os filtros vivem na consulta da URL
// (#/editais?linha=redes&uf=SP&uf=PR), para o "Voltar" do detalhe trazê-los de volta.
export function Lista() {
  const fonte = useFonte();
  const [consulta, setConsulta] = useSearchParams();
  // Só valores do contrato da API: o resto da URL é ignorado (a API daria 422).
  const pedida = consulta.get('linha');
  const linha = pedida !== null && ehLinha(pedida) ? pedida : null;
  const ufs = consulta.getAll('uf').filter(ehUF);
  const dias = lerDias(consulta.get('encerra_em_dias'));
  const novos = consulta.get('novos') === 'true';
  const vereditos = consulta.getAll('veredito').filter(ehFiltroVeredito);
  const filtros: Filtros = {ufs};
  if (linha !== null) filtros.linha = linha;
  if (dias !== null) filtros.encerraEmDias = dias;
  if (novos) filtros.novos = true;
  if (vereditos.length > 0) filtros.vereditos = vereditos;
  const recorte = [
    dias === null ? null : `Só os que encerram em até ${dias} dias`,
    novos ? 'só os novos' : null,
    vereditos.length === 0
      ? null
      : `aderência: ${vereditos.map(v => v.replace('sem_analise', 'sem análise')).join(', ')}`,
  ].filter(parte => parte !== null);
  const texto = consulta.toString();
  const {carga, tentarDeNovo} = useCarga(() => fonte.listar(filtros), texto);
  const agora = new Date();
  const sufixo = texto === '' ? '' : `?${texto}`;

  const mudar = (chave: string, valores: string[]) =>
    setConsulta(atual => {
      const nova = new URLSearchParams(atual);
      nova.delete(chave);
      for (const v of valores) nova.append(chave, v);
      return nova;
    });

  const mostrarTodos = () =>
    setConsulta(atual => {
      const nova = new URLSearchParams(atual);
      for (const chave of CHAVES_RECORTE) nova.delete(chave);
      return nova;
    });

  return (
    <Layout
      header={
        <LayoutHeader hasDivider padding={6}>
          <Heading level={1}>Editais abertos</Heading>
        </LayoutHeader>
      }
      content={
        <LayoutContent padding={6}>
          <VStack gap={6}>
            {carga.estado === 'pronto' && (
              <AvisoDesatualizado atualizadoEm={carga.dados.atualizado_em} agora={agora} />
            )}
            {recorte.length > 0 && (
              <HStack gap={3} align="center">
                <Text type="supporting" color="secondary">
                  {recorte.join(' · ')}
                </Text>
                <Button label="Mostrar todos" variant="secondary" onClick={mostrarTodos} />
              </HStack>
            )}
            <FiltrosEditais
              linha={linha}
              ufs={ufs}
              onLinha={l => mudar('linha', l === null ? [] : [l])}
              onUfs={u => mudar('uf', u)}
            />
            {carga.estado === 'carregando' && (
              <Carregando rotulo="Carregando editais" alturas={[160, 160, 160]} />
            )}
            {carga.estado === 'erro' && (
              <ErroAoCarregar
                titulo="Não foi possível carregar os editais"
                descricao="Verifique a conexão e tente de novo."
                onTentarDeNovo={tentarDeNovo}
              />
            )}
            {carga.estado === 'pronto' &&
              (carga.dados.editais.length === 0 ? (
                <EmptyState
                  title="Nenhum edital aberto com esses filtros"
                  description="Troque a linha de serviço ou as UFs para ver mais editais."
                />
              ) : (
                <Grid columns={COLUNAS} gap={4}>
                  {carga.dados.editais.map(e => (
                    <CartaoEdital
                      key={e.id_publico}
                      edital={e}
                      agora={agora}
                      comAderencia={!carga.dados.perfil_exemplo}
                      href={`/edital/${encodeURIComponent(e.id_publico)}${sufixo}`}
                    />
                  ))}
                </Grid>
              ))}
          </VStack>
        </LayoutContent>
      }
    />
  );
}
