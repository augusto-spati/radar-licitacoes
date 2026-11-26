import {useSearchParams} from 'react-router';
import {EmptyState} from '@astryxdesign/core/EmptyState';
import {Grid} from '@astryxdesign/core/Grid';
import {Layout, LayoutContent, LayoutHeader} from '@astryxdesign/core/Layout';
import {VStack} from '@astryxdesign/core/Stack';
import {Heading} from '@astryxdesign/core/Text';
import {AvisoDesatualizado} from '../componentes/AvisoDesatualizado';
import {CartaoEdital} from '../componentes/CartaoEdital';
import {Carregando, ErroAoCarregar} from '../componentes/EstadoCarga';
import {FiltrosEditais} from '../componentes/FiltrosEditais';
import type {Filtros} from '../dados/fonte';
import {useFonte} from '../dados/FonteContext';
import {useCarga} from '../dados/useCarga';

// Uma coluna no celular (390 px), mais colunas conforme a tela abre.
const COLUNAS = {minWidth: 280, max: 3} as const;

// A partir do template `library`: cabeçalho, filtros e grade de cartões, com
// os estados de carregando, vazio e erro. Os filtros vivem na consulta da URL
// (#/?linha=redes&uf=SP&uf=PR), para o "Voltar" do detalhe trazê-los de volta.
export function Lista() {
  const fonte = useFonte();
  const [consulta, setConsulta] = useSearchParams();
  const linha = consulta.get('linha');
  const ufs = consulta.getAll('uf');
  const filtros: Filtros = linha === null ? {ufs} : {linha, ufs};
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
                      key={e.numero_controle_pncp}
                      edital={e}
                      agora={agora}
                      href={`/edital/${encodeURIComponent(e.numero_controle_pncp)}${sufixo}`}
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
