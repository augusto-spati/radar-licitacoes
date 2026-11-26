import type {ReactNode} from 'react';
import {useLocation} from 'react-router';
import {AppShell} from '@astryxdesign/core/AppShell';
import {TopNav, TopNavHeading, TopNavItem} from '@astryxdesign/core/TopNav';
import {rotas} from '../rotas';

type Props = {
  // Espaço do selo de atualização, preenchido na Task 5.
  selo?: ReactNode;
  children: ReactNode;
};

// Moldura fixa do app, a partir do template `shell-top-nav` do Astryx.
// No celular o AppShell troca a barra por um botão que abre a gaveta com o
// mesmo menu. O menu sai de `rotas` (as com noMenu); não crie itens à mão.
export function Shell({selo, children}: Props) {
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
          endContent={selo}
        />
      }>
      {children}
    </AppShell>
  );
}
