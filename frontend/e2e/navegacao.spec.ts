import {expect, test} from '@playwright/test';
import exemplo from '../src/dados/exemplo.json' with {type: 'json'};
import {congelarRelogio} from './relogio';

test('abre um edital filtrado e o Voltar mantém o filtro', async ({page}) => {
  await congelarRelogio(page);
  await page.goto('/');
  await expect(page.getByRole('heading', {level: 1, name: 'Editais abertos'})).toBeVisible();

  await page.getByRole('button', {name: 'Redes'}).click();
  await expect(page).toHaveURL(/#\/\?linha=redes$/);

  const primeiro = page.getByRole('link', {name: /^Abrir edital:/}).first();
  const rotulo = (await primeiro.getAttribute('aria-label')) ?? '';
  const edital = exemplo.editais.find(e => `Abrir edital: ${e.objeto}` === rotulo);
  expect(edital, 'o cartão veio da fixture').toBeDefined();
  // O link fica sob o conteúdo do cartão: toca-se no prazo do próprio cartão escolhido.
  await primeiro.locator('xpath=..').getByText(/^encerra/).click();

  await expect(page).toHaveURL(/#\/edital\//);
  await expect(page.getByRole('heading', {level: 1, name: edital?.orgao_nome})).toBeVisible();

  await page.getByRole('link', {name: 'Voltar'}).click();
  await expect(page).toHaveURL(/#\/\?linha=redes$/);
  await expect(page.getByRole('heading', {level: 1, name: 'Editais abertos'})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Redes'})).toHaveAttribute('aria-pressed', 'true');
});
