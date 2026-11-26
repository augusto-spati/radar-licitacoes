// @vitest-environment jsdom
import {cleanup, fireEvent, screen} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import {fonteFalsa, renderizar} from '../testes/apoio';
import {Entrar} from './Entrar';

afterEach(cleanup);

describe('Entrar', () => {
  it('e-mail, código e "Entrar" levam à lista, com o aviso da próxima etapa', () => {
    renderizar(<Entrar />, {fonte: fonteFalsa(), endereco: '/entrar', padrao: '/entrar'});
    expect(screen.getByText('O login real chega na próxima etapa')).toBeTruthy();
    expect(screen.queryByLabelText('Código')).toBeNull();

    fireEvent.change(screen.getByLabelText('E-mail'), {target: {value: 'tio@fr.com.br'}});
    fireEvent.click(screen.getByRole('button', {name: 'Enviar código'}));
    fireEvent.change(screen.getByLabelText('Código'), {target: {value: '123456'}});
    fireEvent.click(screen.getByRole('button', {name: 'Entrar'}));

    expect(screen.getByRole('status', {name: 'endereço'}).textContent).toBe('/');
  });
});
