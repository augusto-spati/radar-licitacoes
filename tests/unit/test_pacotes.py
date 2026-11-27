from radar.analise.pacotes import escolher_no_pacote


def test_edital_pelo_nome_da_modalidade_quando_nao_diz_edital() -> None:
    """Pregão Eletrônico 034/2026 (PNCP 07750478000188-1-000677): o ZIP traz o edital
    como "Pregão Eletrônico nº 034.2026 rede de esgoto José Mermejo.pdf", ao lado da planilha
    e da planta. Sem esta regra o pacote inteiro ficava sem documento útil."""
    nomes = [
        "COMPRASGOV PE 34.2026/",
        "COMPRASGOV PE 34.2026/PLAN.ORÇAMENTÁRIA RUA JOSE MERMEJO.pdf",
        "COMPRASGOV PE 34.2026/Pregão Eletrônico nº 034.2026 rede de esgoto José Mermejo.pdf",
        "COMPRASGOV PE 34.2026/Rede Coletora de Esgoto - José Mermejo-Folha 01.pdf",
    ]
    escolhidos = escolher_no_pacote(nomes)
    assert [e.nome for e in escolhidos] == [nomes[2]]
    assert escolhidos[0].tipo == "Edital"


def test_edital_explicito_vem_antes_do_nome_da_modalidade() -> None:
    nomes = ["Concorrencia 03-2026 - planilha.pdf", "Concorrencia 03-2026.pdf", "Edital CP 03.pdf"]
    assert [e.nome for e in escolher_no_pacote(nomes)] == [
        "Edital CP 03.pdf",
        "Concorrencia 03-2026.pdf",
    ]
