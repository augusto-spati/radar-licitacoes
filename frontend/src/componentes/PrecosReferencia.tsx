import { List, ListItem } from "@astryxdesign/core/List";
import { VStack } from "@astryxdesign/core/Stack";
import { Heading, Text } from "@astryxdesign/core/Text";
import { Token } from "@astryxdesign/core/Token";
import {
  formatarMesAno,
  formatarPrecoUnitario,
  nomeUnidade,
} from "../dados/formatos";
import type { Precos, ReferenciaPreco } from "../dados/tipos";
import type { Carga } from "../dados/useCarga";
import { Carregando, ErroAoCarregar } from "./EstadoCarga";

// Nomes de tela das chaves dos serviços-padrão (config/servicos.toml e
// src/radar/precos/servicos.py). Valor novo sem nome aqui aparece como veio da API.
const MATERIAIS: Record<string, string> = {
  pvc: "PVC",
  pead: "PEAD",
  fofo: "ferro fundido",
  ceramico: "cerâmico",
};
const ESCOPOS: Record<string, string> = {
  material: "só o tubo",
  assentamento: "só assentamento",
  completo: "fornecimento e assentamento",
};
const FAIXAS: Record<string, string> = {
  ate_1_5m: "até 1,5 m",
  "1_5_a_3m": "1,5 a 3 m",
  "3_a_6m": "3 a 6 m",
};

const nome = (nomes: Record<string, string>, valor: string) =>
  nomes[valor] ?? valor;
/** "14 processos (37 itens)"; sem o parêntese quando cada processo tem um item. */
function casos(processos: number, itens: number): string {
  const p = processos === 1 ? "1 processo" : `${processos} processos`;
  if (itens === processos) return p;
  return `${p} (${itens === 1 ? "1 item" : `${itens} itens`})`;
}

/** "PE"; fora da UF do edital, "PE — sem dados na UF do edital". Nunca "Brasil". */
function origem(r: ReferenciaPreco, ufEdital: string | null): string {
  const ufs = r.ufs.join(", ");
  if (r.abrangencia === "uf" || ufEdital === null) return ufs;
  const falta = r.ufs.includes(ufEdital) ? "poucos dados" : "sem dados";
  return `${ufs} — ${falta} na UF do edital`;
}

/** "Rede de água · PVC DN 150 · fornecimento e assentamento (m)". */
function titulo(r: ReferenciaPreco): string {
  const tubo = [
    r.material === null ? null : nome(MATERIAIS, r.material),
    r.dn === null ? null : `DN ${r.dn}`,
  ].filter((p) => p !== null);
  const partes = [
    r.rotulo,
    tubo.length === 0 ? null : tubo.join(" "),
    r.escopo === null ? null : nome(ESCOPOS, r.escopo),
    r.faixa === null ? null : nome(FAIXAS, r.faixa),
  ].filter((p) => p !== null);
  return `${partes.join(" · ")} (${nomeUnidade(r.unidade)})`;
}

/** "preços de 05/2025 a 06/2025" (ou de um mês só); sem data-base, nada. */
function periodo({
  data_base_inicio: inicio,
  data_base_fim: fim,
}: ReferenciaPreco): string | null {
  if (inicio === null || fim === null) return null;
  const [de, ate] = [formatarMesAno(inicio), formatarMesAno(fim)];
  return de === ate ? `preços de ${de}` : `preços de ${de} a ${ate}`;
}

function Referencia({
  referencia: r,
  ufEdital,
}: {
  referencia: ReferenciaPreco;
  ufEdital: string | null;
}) {
  // Estimado e contratado ficam em linhas separadas, cada um com a sua origem:
  // nunca um número que misture os dois. O contratado é só do TCE-PE, e vem com o
  // estimado dos mesmos itens, que é o que se compara com ele.
  const quando = periodo(r);
  const preco = (v: string | null) => formatarPrecoUnitario(v, r.unidade);
  return (
    <ListItem
      align="start"
      label={titulo(r)}
      endContent={
        r.poucos_casos ? (
          <Token label="poucos casos" size="sm" color="yellow" />
        ) : undefined
      }
      description={
        <VStack gap={1}>
          {r.processos_estimado > 0 && (
            <Text type="supporting">
              {`Estimado pelo órgão: ${preco(r.mediana_estimado)} · ` +
                `${casos(r.processos_estimado, r.itens_estimado)}, ${origem(r, ufEdital)}`}
            </Text>
          )}
          {r.processos_contratado > 0 && (
            <Text type="supporting">
              {`Contratado: ${preco(r.mediana_contratado)} · ` +
                `${casos(r.processos_contratado, r.itens_contratado)}, PE; ` +
                `estimado desses: ${preco(r.mediana_estimado_contratados)}`}
            </Text>
          )}
          {quando !== null && (
            <Text type="supporting" color="secondary">
              {quando}
            </Text>
          )}
        </VStack>
      }
    />
  );
}

const SEM_PRECOS = (
  <Text type="body" color="secondary">
    Sem preços de referência para as linhas deste edital.
  </Text>
);

function Lista({ precos }: { precos: Precos }) {
  if (precos.referencias.length === 0) return SEM_PRECOS;
  return (
    <VStack gap={2}>
      <List hasDividers>
        {precos.referencias.map((r, i) => (
          <Referencia key={i} referencia={r} ufEdital={precos.uf} />
        ))}
      </List>
      <Text type="supporting" color="secondary">
        Estimado é o preço do órgão, com BDI. Contratado: só Pernambuco
        (TCE-PE). Datas-base diferentes, sem correção monetária.
      </Text>
    </VStack>
  );
}

type Props = { carga: Carga<Precos | null>; onTentarDeNovo: () => void };

/** Bloco "Preços de referência" do detalhe: mediana por serviço, estimado e contratado à parte. */
export function PrecosReferencia({ carga, onTentarDeNovo }: Props) {
  return (
    <VStack gap={2}>
      <VStack gap={0}>
        <Heading level={2}>Preços de referência</Heading>
        <Text type="supporting" color="secondary">
          Mediana por unidade, nas linhas de serviço deste edital
        </Text>
      </VStack>
      {carga.estado === "carregando" && (
        <Carregando rotulo="Carregando preços" alturas={[80]} />
      )}
      {carga.estado === "erro" && (
        <ErroAoCarregar
          titulo="Não foi possível carregar os preços"
          onTentarDeNovo={onTentarDeNovo}
        />
      )}
      {carga.estado === "pronto" &&
        (carga.dados === null ? SEM_PRECOS : <Lista precos={carga.dados} />)}
    </VStack>
  );
}
