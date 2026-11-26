import type {Page} from '@playwright/test';

// A fixture exemplo.json tem prazos que vencem em 05/12/2026: o relógio da
// página fica parado em um dia em que todos ainda estão abertos.
export async function congelarRelogio(page: Page): Promise<void> {
  await page.clock.setFixedTime(new Date('2026-11-26T12:00:00-03:00'));
}
