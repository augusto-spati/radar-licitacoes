import {expect, test} from '@playwright/test';
import exemplo from '../src/dados/exemplo.json' with {type: 'json'};
import {congelarRelogio} from './relogio';

const AGORA = new Date('2026-11-26T12:00:00-03:00');

test('abre um edital filtrado e o Voltar mantém o filtro', async ({page}) => {
  await congelarRelogio(page);
  await page.goto('/');
  await expect(page.getByRole('heading', {level: 1, name: 'Editais abertos'})).toBeVisible();

  await page.getByRole('button', {name: 'Redes'}).click();
  await expect(page).toHaveURL(/#\/\?linha=redes$/);

  // O primeiro cartão filtrado é o primeiro aberto com a linha "redes" da fixture (já em ordem de
  // prazo). Esperar por ele evita agir sobre o cartão da lista ainda sem filtro.
  const edital = exemplo.editais.find(
    e =>
      e.linhas.includes('redes') &&
      e.situacao_normalizada === 'aberta' &&
      e.data_encerramento_proposta !== null &&
      new Date(`${e.data_encerramento_proposta}-03:00`) > AGORA,
  );
  expect(edital, 'há edital de redes aberto na fixture').toBeDefined();
  const primeiro = page.getByRole('link', {name: /^Abrir edital:/}).first();
  await expect(primeiro).toHaveAttribute('aria-label', `Abrir edital: ${edital?.objeto}`);
  // O link fica sob o conteúdo do cartão: toca-se no prazo do próprio cartão escolhido.
  await primeiro.locator('xpath=..').getByText(/^encerra/).click();

  await expect(page).toHaveURL(/#\/edital\//);
  await expect(page.getByRole('heading', {level: 1, name: edital?.orgao_nome})).toBeVisible();

  await page.getByRole('link', {name: 'Voltar'}).click();
  await expect(page).toHaveURL(/#\/\?linha=redes$/);
  await expect(page.getByRole('heading', {level: 1, name: 'Editais abertos'})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Redes'})).toHaveAttribute('aria-pressed', 'true');
});
