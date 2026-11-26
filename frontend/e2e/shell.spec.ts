import {expect, test} from '@playwright/test';
import {congelarRelogio} from './relogio';

// Orçamento absoluto, não proporcional: em duas execuções seguidas no container
// Linux a diferença medida foi de 0 pixel (fonte e renderização são fixas lá),
// então 20 pixels cobrem só ruído residual de antisserrilhamento. Um item de menu
// a mais (como "Ajuda") muda centenas de pixels e reprova; uma razão de 1% numa
// barra de ~1280x50 deixaria passar ~640.
const ORCAMENTO_PIXELS = 20;

test.beforeEach(async ({page}) => {
  await congelarRelogio(page);
  await page.goto('/');
  await expect(page.getByRole('link', {name: /^Abrir edital:/}).first()).toBeVisible();
});

// Guarda contra mudança de moldura/navegação por IA: a imagem é só do cabeçalho
// (marca, menu, botão da gaveta no celular); o selo de hora vem da fixture e
// fica mascarado. Mudança de navegação intencional exige regenerar a referência
// (`npx playwright test --update-snapshots`, no container Linux).
test('shell: cabeçalho e navegação', async ({page}) => {
  await expect(page.getByRole('banner')).toHaveScreenshot('shell.png', {
    mask: [page.getByText(/^atualizado às /)],
    maxDiffPixels: ORCAMENTO_PIXELS,
  });
});

// No celular o menu mora na gaveta: abre pelo botão e confere o painel de navegação.
test('shell: gaveta de navegação no celular', async ({page}, info) => {
  test.skip(info.project.name !== 'celular', 'a gaveta só existe no celular');
  await page.getByRole('button', {name: 'Abrir navegação'}).click();
  const gaveta = page.getByRole('dialog');
  await expect(gaveta).toBeVisible();
  // Só o painel da gaveta (marca, fechar, menu): o diálogo ocupa a tela toda e
  // mostraria os cartões da fixture atrás do fundo escurecido, e regerar o
  // exemplo.json não pode mudar o screenshot do shell.
  const painel = gaveta.locator(':scope > div').first();
  await expect(painel.getByRole('navigation', {name: 'Navegação'})).toBeVisible();
  await expect(painel).toHaveScreenshot('gaveta.png', {maxDiffPixels: ORCAMENTO_PIXELS});
});

test('nenhum elemento passa da largura da tela', async ({page}, info) => {
  const largura = info.project.use.viewport?.width ?? 0;
  const rolagem = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(rolagem).toBeLessThanOrEqual(largura);
});
