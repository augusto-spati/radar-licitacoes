import {Button} from '@astryxdesign/core/Button';
import {EmptyState} from '@astryxdesign/core/EmptyState';
import {Skeleton} from '@astryxdesign/core/Skeleton';
import {VStack} from '@astryxdesign/core/Stack';

/** Esqueleto enquanto carrega: um bloco por altura, anunciado pelo rótulo. */
export function Carregando({rotulo, alturas}: {rotulo: string; alturas: number[]}) {
  return (
    <VStack gap={4} role="status" aria-label={rotulo}>
      {alturas.map((altura, i) => (
        <Skeleton key={i} index={i} height={altura} />
      ))}
    </VStack>
  );
}

type PropsErro = {titulo: string; descricao?: string; onTentarDeNovo: () => void};

/** Falha ao carregar, com "Tentar de novo" para repetir a chamada. */
export function ErroAoCarregar({titulo, descricao, onTentarDeNovo}: PropsErro) {
  return (
    <EmptyState
      title={titulo}
      description={descricao}
      actions={<Button label="Tentar de novo" variant="primary" onClick={onTentarDeNovo} />}
    />
  );
}
