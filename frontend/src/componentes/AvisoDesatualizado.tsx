import {Banner} from '@astryxdesign/core/Banner';
import {estaDesatualizado, formatarDataHora} from '../dados/formatos';

type Props = {atualizadoEm: string | null; agora: Date};

/** Aviso no topo da lista quando a última coleta passou do limite (ou não houve). */
export function AvisoDesatualizado({atualizadoEm, agora}: Props) {
  if (!estaDesatualizado(atualizadoEm, agora)) return null;
  return (
    <Banner
      status="warning"
      title="Os dados podem estar desatualizados"
      description={
        atualizadoEm === null
          ? 'Nenhuma coleta foi concluída ainda.'
          : `Última atualização em ${formatarDataHora(atualizadoEm)}.`
      }
    />
  );
}
