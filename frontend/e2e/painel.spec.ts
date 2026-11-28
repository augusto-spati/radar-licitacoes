import {expect, test} from '@playwright/test';
import {congelarRelogio} from './relogio';

test.beforeEach(async ({page}) => {
  await congelarRelogio(page);
});

test('painel abre em / e o contador de decidir leva à lista filtrada', async ({page}) => {
  await page.goto('/');
  await expect(page.getByRole('heading', {level: 1, name: 'Painel'})).toBeVisible();

  // O link do cartão fica sob o conteúdo: toca-se no rótulo do próprio contador.
  const contador = page.getByRole('link', {name: /para decidir$/});
  await expect(contador).toHaveAttribute('href', /#\/editais\?encerra_em_dias=20/);
  await contador.locator('xpath=..').getByText('para decidir', {exact: true}).click();
  await expect(page).toHaveURL(/#\/editais\?encerra_em_dias=20/);
  await expect(page.getByRole('heading', {level: 1, name: 'Editais abertos'})).toBeVisible();
});

test('link antigo #/?linha=redes abre a lista com o filtro', async ({page}) => {
  await page.goto('/#/?linha=redes');
  await expect(page).toHaveURL(/#\/editais\?linha=redes$/);
  await expect(page.getByRole('heading', {level: 1, name: 'Editais abertos'})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Redes'})).toHaveAttribute('aria-pressed', 'true');
});

// No celular (390 px) os contadores e os cartões do painel têm de caber sem rolagem horizontal.
test('painel e lista não passam da largura da tela', async ({page}, info) => {
  const largura = info.project.use.viewport?.width ?? 0;
  for (const rota of ['/#/', '/#/editais']) {
    await page.goto(rota);
    await expect(page.getByRole('heading', {level: 1})).toBeVisible();
    await expect(page.getByRole('link', {name: /^Abrir edital:/}).first()).toBeVisible();
    const rolagem = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(rolagem, rota).toBeLessThanOrEqual(largura);
  }
});
