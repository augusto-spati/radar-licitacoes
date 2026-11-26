import type {ReactNode} from 'react';
import {useLocation} from 'react-router';
import {AppShell} from '@astryxdesign/core/AppShell';
import {Text} from '@astryxdesign/core/Text';
import {TopNav, TopNavHeading, TopNavItem} from '@astryxdesign/core/TopNav';
import {useFonte} from '../dados/FonteContext';
import {formatarHora} from '../dados/formatos';
import type {Status} from '../dados/tipos';
import {type Carga, useCarga} from '../dados/useCarga';
import {rotas} from '../rotas';

type Props = {children: ReactNode};

// Selo de atualização: lê o status da fonte uma vez, ao abrir o app.
// Falha ao ler o status não é "sem atualização": mostra só o travessão.
function textoDoSelo(carga: Carga<Status>): string | null {
  if (carga.estado === 'carregando') return null;
  if (carga.estado === 'erro') return '—';
  const em = carga.dados.atualizado_em;
  return em === null ? 'sem atualização' : `atualizado às ${formatarHora(em)}`;
}

function Selo() {
  const fonte = useFonte();
  const {carga} = useCarga(() => fonte.status(), 'status');
  const texto = textoDoSelo(carga);
  if (texto === null) return null;
  return (
    <Text type="supporting" color="secondary">
      {texto}
    </Text>
  );
}

// Moldura fixa do app, a partir do template `shell-top-nav` do Astryx.
// No celular o AppShell troca a barra por um botão que abre a gaveta com o
// mesmo menu. O menu sai de `rotas` (as com noMenu); não crie itens à mão.
export function Shell({children}: Props) {
  const {pathname} = useLocation();
  return (
    <AppShell
      variant="surface"
      height="auto"
      contentPadding={4}
      topNav={
        <TopNav
          label="Navegação principal"
          heading={<TopNavHeading heading="Radar FR" headingHref="/" />}
          centerContent={rotas
            .filter(rota => rota.noMenu)
            .map(rota => (
              <TopNavItem
                key={rota.caminho}
                label={rota.nome}
                href={rota.caminho}
                isSelected={pathname === rota.caminho}
              />
            ))}
          endContent={<Selo />}
        />
      }>
      {children}
    </AppShell>
  );
}
