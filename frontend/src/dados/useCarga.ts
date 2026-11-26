import {useCallback, useEffect, useState} from 'react';

export type Carga<T> =
  | {estado: 'carregando'}
  | {estado: 'erro'}
  | {estado: 'pronto'; dados: T};

/**
 * Roda `carregar` quando `chave` muda e de novo a cada `tentarDeNovo`.
 * Resposta de uma chamada antiga (filtro trocado no meio) é descartada.
 */
export function useCarga<T>(carregar: () => Promise<T>, chave: string) {
  const [carga, setCarga] = useState<Carga<T>>({estado: 'carregando'});
  const [tentativa, setTentativa] = useState(0);

  useEffect(() => {
    let vale = true;
    setCarga({estado: 'carregando'});
    carregar().then(
      dados => vale && setCarga({estado: 'pronto', dados}),
      () => vale && setCarga({estado: 'erro'}),
    );
    return () => {
      vale = false;
    };
    // `carregar` muda a cada render; quem decide recarregar é a chave.
  }, [chave, tentativa]);

  const tentarDeNovo = useCallback(() => setTentativa(n => n + 1), []);
  return {carga, tentarDeNovo};
}
