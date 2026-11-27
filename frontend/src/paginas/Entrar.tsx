import {useState} from 'react';
import {useNavigate} from 'react-router';
import {Radar} from 'lucide-react';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Card} from '@astryxdesign/core/Card';
import {Center} from '@astryxdesign/core/Center';
import {Icon} from '@astryxdesign/core/Icon';
import {VStack} from '@astryxdesign/core/Layout';
import {Heading, Text} from '@astryxdesign/core/Text';
import {TextInput} from '@astryxdesign/core/TextInput';
import {confirmarCodigo, enviarCodigo, loginReal, type Desafio} from '../dados/sessao';

// Desafio do protótipo: com a fonte de exemplo nada vai ao Cognito.
const DESAFIO_VISUAL: Desafio = {email: '', session: ''};

// A partir do template `login`: coluna centrada de até 400 px com marca,
// cartão e formulário. Com a API, o código chega por e-mail (Cognito); com a
// fonte de exemplo a tela é só visual e "Entrar" vai direto para a lista.
export function Entrar() {
  const navegar = useNavigate();
  const real = loginReal();
  const [email, setEmail] = useState('');
  const [codigo, setCodigo] = useState('');
  const [desafio, setDesafio] = useState<Desafio | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [aguardando, setAguardando] = useState(false);

  async function executar(acao: () => Promise<void>) {
    // Enter no campo também dispara: não manda de novo enquanto espera.
    if (aguardando) return;
    setErro(null);
    setAguardando(true);
    try {
      await acao();
    } catch (e) {
      setErro(e instanceof Error ? e.message : String(e));
    } finally {
      setAguardando(false);
    }
  }

  const pedirCodigo = () =>
    executar(async () => {
      setCodigo('');
      setDesafio(real ? await enviarCodigo(email) : DESAFIO_VISUAL);
    });

  const entrar = () =>
    executar(async () => {
      if (real && desafio) await confirmarCodigo(desafio, codigo);
      navegar('/');
    });

  return (
    <Center axis="both" padding={6}>
      <VStack gap={4} hAlign="center" width="100%" maxWidth={400}>
        <VStack gap={2} hAlign="center">
          <Icon icon={Radar} size="lg" />
          <Text type="body" weight="bold" size="lg">
            Radar FR
          </Text>
        </VStack>

        <Card padding={8} width="100%">
          <VStack gap={4} hAlign="stretch">
            <VStack gap={1} hAlign="center">
              <Heading level={2} accessibilityLevel={1}>
                Entrar
              </Heading>
              <Text type="body" color="secondary" size="sm">
                Receba um código no seu e-mail para entrar
              </Text>
            </VStack>

            {!real && (
              <Banner
                status="info"
                title="Protótipo com dados de exemplo"
                description="Aqui qualquer e-mail e código abrem a lista."
              />
            )}
            {erro && <Banner status="error" title={erro} role="alert" />}
            {real && desafio && !erro && (
              <Banner status="info" title="Se este e-mail tiver acesso, o código chega em instantes." />
            )}

            <TextInput
              label="E-mail"
              value={email}
              onChange={setEmail}
              placeholder="voce@empresa.com.br"
              type="email"
              size="lg"
              autoComplete="email"
              autoCapitalize="none"
              hasSpellCheck={false}
              enterKeyHint={desafio ? 'next' : 'send'}
              onEnter={desafio ? undefined : pedirCodigo}
            />
            {!desafio ? (
              <Button
                label="Enviar código"
                variant="primary"
                size="lg"
                isLoading={aguardando}
                onClick={pedirCodigo}
              />
            ) : (
              <VStack gap={4} hAlign="stretch">
                <TextInput
                  label="Código"
                  value={codigo}
                  onChange={setCodigo}
                  placeholder="Código recebido por e-mail"
                  size="lg"
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  enterKeyHint="go"
                  onEnter={entrar}
                  // O campo aparece depois do clique: o foco vai para ele, não se perde no body.
                  hasAutoFocus
                />
                <Button
                  label="Entrar"
                  variant="primary"
                  size="lg"
                  isLoading={aguardando}
                  onClick={entrar}
                />
                <Button
                  label="Pedir outro código"
                  variant="ghost"
                  isLoading={aguardando}
                  onClick={pedirCodigo}
                />
              </VStack>
            )}
          </VStack>
        </Card>
      </VStack>
    </Center>
  );
}
