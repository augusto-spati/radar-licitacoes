import {Button} from '@astryxdesign/core/Button';
import {EmptyState} from '@astryxdesign/core/EmptyState';
import {Layout, LayoutContent} from '@astryxdesign/core/Layout';

// Rota coringa: endereço que nenhuma rota da v1 reconhece.
export function NaoEncontrada() {
  return (
    <Layout
      contentWidth={1000}
      content={
        <LayoutContent padding={4}>
          <EmptyState
            title="Página não encontrada"
            description="O endereço não existe ou foi digitado de forma incompleta."
            actions={<Button label="Voltar para a lista" variant="primary" href="/editais" />}
          />
        </LayoutContent>
      }
    />
  );
}
