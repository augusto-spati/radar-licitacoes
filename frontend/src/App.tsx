import {type ComponentProps, Suspense, useEffect} from 'react';
import {HashRouter, Link, matchPath, Route, Routes, useLocation} from 'react-router';
import {InternationalizationProvider} from '@astryxdesign/core/i18n';
import {LinkProvider} from '@astryxdesign/core/Link';
import ptBR from '@astryxdesign/core/locales/pt-BR.generated.js';
import {Theme} from '@astryxdesign/core/theme';
import {neutralTheme} from '@astryxdesign/theme-neutral/built';
import {Carregando} from './componentes/EstadoCarga';
import {Shell} from './componentes/Shell';
import {rotas} from './rotas';

type PropsDoLink = Omit<ComponentProps<typeof Link>, 'to'> & {href?: string};

// Os links do Astryx recebem `href`; o Link do roteador quer `to`.
export function LinkDoRoteador({href = '/', ...resto}: PropsDoLink) {
  return <Link to={href} {...resto} />;
}

const MARCA = 'Radar FR';

/** Título da aba por rota ("Edital · Radar FR"): leitor de tela, abas e histórico distinguem as páginas. */
export function tituloDaRota(pathname: string): string {
  const rota = rotas.find(r => matchPath(r.caminho, pathname));
  return rota ? `${rota.nome} · ${MARCA}` : MARCA;
}

function TituloDaAba() {
  const {pathname} = useLocation();
  useEffect(() => {
    document.title = tituloDaRota(pathname);
  }, [pathname]);
  return null;
}

// O catálogo pt-BR traduz também os textos internos do Astryx
// ("pular para o conteúdo", botão do menu no celular).
export function App() {
  return (
    <InternationalizationProvider locale="pt-BR" messages={{'pt-BR': ptBR}}>
      <Theme theme={neutralTheme}>
        <HashRouter>
          <TituloDaAba />
          <LinkProvider component={LinkDoRoteador}>
            <Shell>
              {/* As páginas chegam sob demanda (lazy em rotas.tsx); o Shell fica na tela. */}
              <Suspense fallback={<Carregando rotulo="Carregando página" alturas={[48, 320]} />}>
                <Routes>
                  {rotas.map(({caminho, elemento: Elemento}) => (
                    <Route key={caminho} path={caminho} element={<Elemento />} />
                  ))}
                </Routes>
              </Suspense>
            </Shell>
          </LinkProvider>
        </HashRouter>
      </Theme>
    </InternationalizationProvider>
  );
}
