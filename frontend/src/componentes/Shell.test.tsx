// @vitest-environment jsdom
import {cleanup, screen} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {fonteFalsa, renderizar} from '../testes/apoio';
import {Shell} from './Shell';

afterEach(cleanup);

describe('selo do Shell', () => {
  it('mostra a hora da última atualização em Brasília, lendo o status uma vez', async () => {
    const status = vi.fn(async () => ({atualizado_em: '2026-11-26T16:09:37Z'}));
    renderizar(<Shell>conteúdo</Shell>, {fonte: fonteFalsa({status})});
    expect(await screen.findByText('atualizado às 13:09')).toBeTruthy();
    expect(status).toHaveBeenCalledTimes(1);
  });

  it('sem coleta concluída diz "sem atualização"', async () => {
    renderizar(<Shell>conteúdo</Shell>, {fonte: fonteFalsa()});
    expect(await screen.findByText('sem atualização')).toBeTruthy();
  });
});
