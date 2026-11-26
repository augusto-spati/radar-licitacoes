import {createContext, useContext} from 'react';
import {fonte, type Fonte} from './fonte';

// Costura para os testes: as telas leem a fonte daqui; em produção é `fonte`,
// nos testes um <FonteContext value={falsa}> injeta uma fonte sem rede.
export const FonteContext = createContext<Fonte>(fonte);

export const useFonte = (): Fonte => useContext(FonteContext);
