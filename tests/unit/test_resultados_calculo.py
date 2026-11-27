from decimal import Decimal

from radar.editais import ItemResultado, interpretar_itens, resumir


def _item(n=1, sit="homologado", est="100", hom="80", sig=False, nome="A", ni="11111111000111"):
    return ItemResultado(
        numero_item=n,
        descricao="x",
        unidade=None,
        quantidade=None,
        valor_unitario_estimado=None,
        valor_total_estimado=Decimal(est) if est else None,
        orcamento_sigiloso=sig,
        situacao=sit,
        fornecedor_ni=ni,
        fornecedor_nome=nome,
        valor_total_homologado=Decimal(hom) if hom else None,
    )


def test_desconto_ponderado():
    r = resumir([_item(1, est="100", hom="80"), _item(2, est="50", hom="45")])
    assert r.desconto == Decimal("0.1667")
    assert r.estimado_considerado == 150 and r.homologado_considerado == 125
    assert r.situacao == "homologada"


def test_sigiloso_ignorado_e_so_sigilosos_sem_desconto():
    r = resumir([_item(1, est="100", hom="80"), _item(2, est="0", hom="999", sig=True)])
    assert r.desconto == Decimal("0.2000")
    assert resumir([_item(1, sig=True, est="")]).desconto is None


def test_parcial_e_vencedor_maior_homologado():
    r = resumir(
        [
            _item(1, hom="80", nome="A", ni="1"),
            _item(2, hom="300", est="400", nome="B", ni="2"),
            _item(3, sit="em_andamento", hom=""),
        ]
    )
    assert r.situacao == "parcial"
    assert (r.vencedor_ni, r.vencedor_nome) == ("2", "B")


def test_situacoes_agregadas():
    assert resumir([]).situacao == "sem_itens"
    assert resumir([_item(sit="em_andamento")]).situacao == "em_andamento"
    assert resumir([_item(sit="fracassado"), _item(2, sit="fracassado")]).situacao == "fracassada"
    assert resumir([_item(sit="deserto")]).situacao == "deserta"
    assert resumir([_item(sit="anulado")]).situacao == "anulada"
    assert resumir([_item(), _item(2, sit="fracassado")]).situacao == "homologada"


def test_pessoa_fisica_mascarada_e_situacao():
    itens = interpretar_itens(
        [
            {
                "numeroItem": 1,
                "descricao": "d",
                "valorTotal": 100,
                "orcamentoSigiloso": False,
                "situacaoCompraItemNome": "Homologado",
                "resultados": [
                    {
                        "niFornecedor": "12345678901",
                        "nomeRazaoSocialFornecedor": "Fulano",
                        "valorTotalHomologado": 90,
                        "dataResultado": "2026-01-02T10:00:00",
                    }
                ],
            },
            {"numeroItem": 2, "situacaoCompraItemNome": "Anulado/Revogado/Cancelado"},
            {"numeroItem": 3, "situacaoCompraItemNome": "Algo novo"},
        ]
    )
    assert itens[0].fornecedor_nome == "Pessoa física" and itens[0].fornecedor_ni is None
    assert itens[0].valor_total_homologado == 90 and itens[0].data_resultado is not None
    assert [i.situacao for i in itens] == ["homologado", "anulado", "em_andamento"]


def test_decimal_rejeita_nan_e_infinito():
    itens = interpretar_itens(
        [
            {
                "numeroItem": 1,
                "valorTotal": "NaN",
                "quantidade": "Infinity",
                "situacaoCompraItemNome": "Homologado",
            }
        ]
    )
    assert itens[0].valor_total_estimado is None and itens[0].quantidade is None


def test_itens_de_pessoa_fisica_nao_se_somam_no_vencedor():
    def pf(n, hom):
        return _item(n, hom=hom, nome="Pessoa física", ni=None)

    r = resumir([pf(1, "60"), pf(2, "50"), _item(3, hom="80", nome="Acme", ni="9")])
    # soma de PF (110) não pode vencer: cada item conta à parte
    assert r.vencedor_nome == "Acme"


def test_vencedor_agrupa_por_ni():
    r = resumir(
        [
            _item(1, hom="40", nome="A Ltda", ni="1"),
            _item(2, hom="40", nome="A LTDA", ni="1"),
            _item(3, hom="70", nome="B", ni="2"),
        ]
    )
    assert r.vencedor_ni == "1"


def test_mistos():
    assert resumir([_item(sit="fracassado"), _item(2, sit="deserto")]).situacao == "fracassada"
    assert resumir([_item(), _item(2, sit="anulado")]).situacao == "homologada"
