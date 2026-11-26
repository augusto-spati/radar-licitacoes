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

// A partir do template `login`: coluna centrada de até 400 px com marca,
// cartão e formulário. Só visual nesta etapa: "Entrar" vai para a lista.
export function Entrar() {
  const navegar = useNavigate();
  const [email, setEmail] = useState('');
  const [codigo, setCodigo] = useState('');
  const [codigoEnviado, setCodigoEnviado] = useState(false);

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

            <Banner
              status="info"
              title="O login real chega na próxima etapa"
              description="Por enquanto, qualquer e-mail e código abrem a lista."
            />

            <TextInput
              label="E-mail"
              value={email}
              onChange={setEmail}
              placeholder="voce@empresa.com.br"
              type="email"
              size="lg"
            />
            {!codigoEnviado ? (
              <Button
                label="Enviar código"
                variant="primary"
                size="lg"
                onClick={() => setCodigoEnviado(true)}
              />
            ) : (
              <VStack gap={4} hAlign="stretch">
                <TextInput
                  label="Código"
                  value={codigo}
                  onChange={setCodigo}
                  placeholder="Código de 6 dígitos"
                  size="lg"
                />
                <Button label="Entrar" variant="primary" size="lg" onClick={() => navegar('/')} />
              </VStack>
            )}
          </VStack>
        </Card>
      </VStack>
    </Center>
  );
}
