// @vitest-environment jsdom
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Text } from "@astryxdesign/core/Text";
import type { Painel as DadosPainel } from "../dados/tipos";
import { edital, fixarRelogio, fonteFalsa, renderizar } from "../testes/apoio";
import { Painel } from "./Painel";

// 26/11/2026 12:00 em Brasília.
const AGORA = new Date("2026-11-26T12:00:00-03:00");

fixarRelogio(AGORA);

const DECIDIR_REAL =
  "/editais?encerra_em_dias=20&veredito=apto&veredito=verificar&veredito=sem_analise";

const proximo = edital({
  id_publico: "pncp~1",
  objeto: "Rede de esgoto no Centro",
  data_encerramento_proposta: "2026-12-05T08:00:00",
  primeiro_visto_em: "2026-11-25T10:00:00-03:00",
  veredito: "verificar",
  motivo_verificar: "atestados",
});
const distante = edital({
  id_publico: "sanepar~2",
  fonte: "sanepar",
  objeto: "Controle de perdas em Curitiba",
  data_encerramento_proposta: "2027-01-20T08:00:00",
});

function trintaEditais() {
  return Array.from({ length: 30 }, (_, i) =>
    edital({ id_publico: `pncp~${i}`, objeto: `Edital ${i}` }),
  );
}

function painel(parcial: Partial<DadosPainel> = {}): DadosPainel {
  return {
    atualizado_em: "2026-11-26T09:00:00-03:00",
    perfil_exemplo: false,
    coleta: [],
    contadores: { abertos: 5, decidir: 3, novos: 1 },
    secoes: [
      { chave: "decidir", total: 1, editais: [proximo] },
      { chave: "mais_adiante", total: 1, editais: [distante] },
    ],
    ...parcial,
  };
}

const mostrar = (dados: DadosPainel, endereco = "/") =>
  renderizar(<Painel />, {
    fonte: fonteFalsa({ painel: async () => dados }),
    endereco,
  });

const hrefDe = (nome: string) =>
  screen.getByRole("link", { name: nome }).getAttribute("href");

describe("Painel", () => {
  it("painel_mostra_contadores_com_links_para_a_lista", async () => {
    mostrar(painel());
    expect(
      await screen.findByRole("heading", { level: 1, name: "Painel" }),
    ).toBeTruthy();
    expect(
      await screen.findByRole("link", { name: "3 para decidir" }),
    ).toBeTruthy();
    expect(hrefDe("3 para decidir")).toBe(DECIDIR_REAL);
    expect(hrefDe("1 novos")).toBe("/editais?novos=true");
    expect(hrefDe("5 abertos ao todo")).toBe("/editais");
    expect(
      screen.queryByText("Aderência indisponível: perfil não carregado."),
    ).toBeNull();
    // Cartão sem a consulta do painel: o detalhe não herda filtros.
    expect(
      screen
        .getByRole("link", { name: "Abrir edital: Rede de esgoto no Centro" })
        .getAttribute("href"),
    ).toBe("/edital/pncp~1");
  });

  it("painel_secao_vazia_vira_uma_linha", async () => {
    mostrar(
      painel({
        secoes: [
          { chave: "decidir", total: 0, editais: [] },
          { chave: "mais_adiante", total: 0, editais: [] },
        ],
      }),
    );
    expect(await screen.findByText("Nada para decidir agora.")).toBeTruthy();
    expect(
      screen.queryAllByRole("link", { name: /^Abrir edital/ }),
    ).toHaveLength(0);
  });

  it("painel_selos_novo_e_verificar_com_motivo", async () => {
    mostrar(painel());
    const cartao = await screen.findByRole("link", {
      name: "Abrir edital: Rede de esgoto no Centro",
    });
    const dentro = within(cartao.parentElement as HTMLElement);
    expect(dentro.getByText("novo")).toBeTruthy();
    expect(dentro.getByText("verificar: Atestado técnico")).toBeTruthy();
  });

  it("painel_perfil_de_exemplo_esconde_aderencia", async () => {
    mostrar(painel({ perfil_exemplo: true }));
    expect(
      await screen.findByText("Aderência indisponível: perfil não carregado."),
    ).toBeTruthy();
    expect(screen.queryByText(/^verificar/)).toBeNull();
    expect(screen.queryByText(/sem análise/)).toBeNull();
    const href = hrefDe("3 para decidir");
    expect(href).toBe("/editais?encerra_em_dias=20");
    expect(href).not.toContain("veredito=");
    // O selo "novo" não depende do perfil.
    expect(screen.getByText("novo")).toBeTruthy();
  });

  it("painel_aviso_de_coleta_so_quando_falha", async () => {
    mostrar(painel());
    await screen.findByRole("link", { name: "3 para decidir" });
    expect(
      screen.queryByText(/desatualizad|falhou|leitura concluída/),
    ).toBeNull();
    cleanup();

    mostrar(
      painel({
        coleta: [
          {
            fonte: "sanepar",
            motivo: "falha",
            dados_de: "2026-11-24T06:10:00-03:00",
          },
        ],
      }),
    );
    expect(
      await screen.findByText(
        "A última leitura de Sanepar falhou; os editais dela são de 24/11/2026 06:10",
      ),
    ).toBeTruthy();
  });

  it("painel_aviso_de_coleta_com_varias_fontes_resume_e_detalha", async () => {
    mostrar(
      painel({
        coleta: [
          {
            fonte: "dae_jundiai",
            motivo: "atrasado",
            dados_de: "2026-11-24T06:10:00-03:00",
          },
          { fonte: "compesa", motivo: "atrasado", dados_de: null },
        ],
      }),
    );
    expect(
      await screen.findByText("2 fontes podem estar desatualizadas"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "detalhes" }));
    expect(
      await screen.findByText(
        "DAE Jundiaí pode estar desatualizado desde 24/11/2026 06:10",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText("Compesa ainda não teve nenhuma leitura concluída"),
    ).toBeTruthy();
  });

  it("painel_mais_adiante_comeca_recolhido_e_abre", async () => {
    mostrar(painel());
    await screen.findByRole("heading", { level: 2, name: "Mais adiante" });
    const botao = screen.getByRole("button", { name: "Mostrar mais adiante" });
    // O conteúdo fica no DOM e o CSS o esconde (o jsdom não aplica): vale o estado do gatilho.
    const conteudo = document.getElementById(
      botao.getAttribute("aria-controls") ?? "",
    );
    expect(botao.getAttribute("aria-expanded")).toBe("false");
    expect(conteudo?.hasAttribute("data-open")).toBe(false);
    expect(
      within(conteudo as HTMLElement).getByRole("link", {
        name: "Abrir edital: Controle de perdas em Curitiba",
      }),
    ).toBeTruthy();
    fireEvent.click(botao);
    expect(botao.getAttribute("aria-expanded")).toBe("true");
    expect(conteudo?.hasAttribute("data-open")).toBe(true);
  });

  it("painel_mostra_ver_todos_quando_a_secao_foi_cortada", async () => {
    const editais = trintaEditais();
    mostrar(
      painel({
        secoes: [
          { chave: "decidir", total: 40, editais },
          { chave: "mais_adiante", total: 0, editais: [] },
        ],
      }),
    );
    expect(
      await screen.findByRole("link", { name: "Ver os 40 na lista" }),
    ).toBeTruthy();
    expect(screen.getAllByRole("link", { name: /^Abrir edital/ })).toHaveLength(
      30,
    );
    expect(hrefDe("Ver os 40 na lista")).toBe(DECIDIR_REAL);
  });

  it("painel_mais_adiante_cortado_nao_promete_numero_que_a_lista_nao_tem", async () => {
    const editais = trintaEditais();
    mostrar(
      painel({
        secoes: [
          { chave: "decidir", total: 1, editais: [proximo] },
          { chave: "mais_adiante", total: 45, editais },
        ],
      }),
    );
    await screen.findByRole("link", { name: "Ver todos os abertos na lista" });
    expect(screen.queryByRole("link", { name: /Ver os 45/ })).toBeNull();
    expect(hrefDe("Ver todos os abertos na lista")).toBe("/editais");
  });

  it("painel_sem_corte_nao_mostra_ver_todos", async () => {
    mostrar(painel());
    await screen.findByRole("link", { name: "3 para decidir" });
    expect(screen.queryByRole("link", { name: /^Ver os/ })).toBeNull();
  });

  it("painel_erro_tem_tentar_de_novo", async () => {
    const chamada = vi.fn<() => Promise<DadosPainel>>();
    chamada
      .mockRejectedValueOnce(new Error("rede"))
      .mockResolvedValueOnce(painel());
    renderizar(<Painel />, { fonte: fonteFalsa({ painel: chamada }) });
    expect(
      await screen.findByText("Não foi possível carregar o painel"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    expect(
      await screen.findByRole("link", { name: "3 para decidir" }),
    ).toBeTruthy();
    expect(chamada).toHaveBeenCalledTimes(2);
  });

  it("link_antigo_com_linha_e_uf_vai_para_editais_com_os_mesmos_parametros", async () => {
    const chamada = vi.fn(async () => painel());
    renderizar(<Painel />, {
      fonte: fonteFalsa({ painel: chamada }),
      endereco: "/?linha=redes&uf=SP",
      outras: { "/editais": <Text type="body">Lista de editais</Text> },
    });
    expect(await screen.findByText("Lista de editais")).toBeTruthy();
    expect(screen.getByRole("status", { name: "endereço" }).textContent).toBe(
      "/editais?linha=redes&uf=SP",
    );
    expect(chamada).not.toHaveBeenCalled();
  });
});
