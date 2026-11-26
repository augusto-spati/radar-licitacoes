import type {ComponentProps} from 'react';
import {HashRouter, Link, Route, Routes} from 'react-router';
import {InternationalizationProvider} from '@astryxdesign/core/i18n';
import {LinkProvider} from '@astryxdesign/core/Link';
import ptBR from '@astryxdesign/core/locales/pt-BR.generated.js';
import {Theme} from '@astryxdesign/core/theme';
import {neutralTheme} from '@astryxdesign/theme-neutral/built';
import {Shell} from './componentes/Shell';
import {rotas} from './rotas';

type PropsDoLink = Omit<ComponentProps<typeof Link>, 'to'> & {href?: string};

// Os links do Astryx recebem `href`; o Link do roteador quer `to`.
export function LinkDoRoteador({href = '/', ...resto}: PropsDoLink) {
  return <Link to={href} {...resto} />;
}

// O catálogo pt-BR traduz também os textos internos do Astryx
// ("pular para o conteúdo", botão do menu no celular).
export function App() {
  return (
    <InternationalizationProvider locale="pt-BR" messages={{'pt-BR': ptBR}}>
      <Theme theme={neutralTheme}>
        <HashRouter>
          <LinkProvider component={LinkDoRoteador}>
            <Shell>
              <Routes>
                {rotas.map(({caminho, elemento: Elemento}) => (
                  <Route key={caminho} path={caminho} element={<Elemento />} />
                ))}
              </Routes>
            </Shell>
          </LinkProvider>
        </HashRouter>
      </Theme>
    </InternationalizationProvider>
  );
}
