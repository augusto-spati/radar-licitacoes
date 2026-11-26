import {MultiSelector} from '@astryxdesign/core/MultiSelector';
import {HStack, VStack} from '@astryxdesign/core/Stack';
import {ToggleButton, ToggleButtonGroup} from '@astryxdesign/core/ToggleButton';
import {LINHAS} from '../dados/formatos';

const TODAS = 'todas';
const UFS = ['SP', 'PR', 'PE', 'MG'];

type Props = {
  linha: string | null;
  ufs: string[];
  onLinha: (linha: string | null) => void;
  onUfs: (ufs: string[]) => void;
};

// Abas de filtro do template `library`: os botões quebram linha no celular
// (HStack com wrap no lugar do OverflowList) e a UF vem num multisseletor.
export function FiltrosEditais({linha, ufs, onLinha, onUfs}: Props) {
  return (
    <VStack gap={3}>
      <ToggleButtonGroup
        label="Linha de serviço"
        value={linha ?? TODAS}
        onChange={v => onLinha(typeof v === 'string' && v !== TODAS ? v : null)}>
        <HStack gap={1} wrap="wrap">
          <ToggleButton label="Todas" value={TODAS} size="lg" />
          {Object.entries(LINHAS).map(([valor, nome]) => (
            <ToggleButton key={valor} label={nome} value={valor} size="lg" />
          ))}
        </HStack>
      </ToggleButtonGroup>
      <MultiSelector
        label="UF"
        isLabelHidden
        placeholder="Todas as UFs"
        options={UFS}
        value={ufs}
        onChange={onUfs}
        triggerDisplay="labels"
        hasClear
        size="lg"
        width="100%"
      />
    </VStack>
  );
}
