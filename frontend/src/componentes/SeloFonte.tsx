import {Token} from '@astryxdesign/core/Token';
import {NOMES_FONTE} from '../dados/formatos';
import type {FonteEdital} from '../dados/tipos';

/** De qual fonte (PNCP, Sanepar, Compesa, DAE Jundiaí) o edital veio. */
export function SeloFonte({fonte}: {fonte: FonteEdital}) {
  return <Token label={NOMES_FONTE[fonte]} size="sm" color="gray" />;
}
