import {expect, test} from '@playwright/test';
import {congelarRelogio} from './relogio';

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
    // O recorte é só a barra (~60 px de altura): 1% dos pixels absorve o
    // antisserrilhamento da fonte entre execuções, mas um item de menu a mais
    // ou uma marca trocada mexe em bem mais que isso.
    maxDiffPixelRatio: 0.01,
  });
});

test('nenhum elemento passa da largura da tela', async ({page}, info) => {
  const largura = info.project.use.viewport?.width ?? 0;
  const rolagem = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(rolagem).toBeLessThanOrEqual(largura);
});
