"""Exigências × perfil por regras determinísticas. O LLM não decide a aptidão."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from radar.analise.exigencias import Atestado, CategoriaEvidencia, Exigencias, ValorEvidencia
from radar.analise.modelos import Veredito
from radar.analise.normalizacao import normalizar_unidade, sem_acento
from radar.analise.perfil import Perfil

Situacao = Literal["atende", "nao_atende", "verificar", "informativo"]


class ItemComparacao(BaseModel):
    campo: str
    situacao: Situacao
    exigido: str
    perfil: str
    motivo: str
    trecho: str | None
    pagina: int | None


class Comparacao(BaseModel):
    veredito: Veredito
    itens: list[ItemComparacao]


def _reais(valor: Decimal | None) -> str:
    if valor is None:
        return "não informado"
    inteiro, _, centavos = f"{valor:,.2f}".partition(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


def _numero(valor: Decimal) -> str:
    return f"{valor.normalize():f}"


def _item(
    campo: str,
    situacao: Situacao,
    exigido: str,
    perfil: str,
    motivo: str,
    evidencia: ValorEvidencia | CategoriaEvidencia | Atestado | None,
) -> ItemComparacao:
    conflito = isinstance(evidencia, ValorEvidencia | CategoriaEvidencia) and evidencia.conflito
    if conflito and situacao != "informativo":
        situacao = "verificar"
        motivo = f"{motivo} (conflito: o edital traz valores diferentes; ficou o mais restritivo)"
    return ItemComparacao(
        campo=campo,
        situacao=situacao,
        exigido=exigido,
        perfil=perfil,
        motivo=motivo,
        trecho=None if evidencia is None else evidencia.trecho,
        pagina=None if evidencia is None else evidencia.pagina,
    )


def _monetario(campo: str, exigido: ValorEvidencia, do_perfil: Decimal | None) -> ItemComparacao:
    texto_exigido = _reais(exigido.numero)
    if exigido.observacao:
        texto_exigido = f"{texto_exigido} ({exigido.observacao})"
    if exigido.numero is None:
        return _item(
            campo, "verificar", texto_exigido, _reais(do_perfil), "valor exigido sem número",
            exigido,
        )  # fmt: skip
    if do_perfil is None:
        return _item(
            campo, "verificar", texto_exigido, _reais(None), "o perfil não informa", exigido
        )
    if do_perfil >= exigido.numero:
        return _item(campo, "atende", texto_exigido, _reais(do_perfil), "perfil ≥ exigido", exigido)
    return _item(campo, "nao_atende", texto_exigido, _reais(do_perfil), "perfil < exigido", exigido)


def _garantia(campo: str, exigido: ValorEvidencia, maxima: Decimal) -> ItemComparacao:
    texto_perfil = f"até {_numero(maxima)}%"
    if exigido.numero is None:
        return _item(
            campo, "verificar", "percentual não informado", texto_perfil,
            "garantia exigida sem percentual", exigido,
        )  # fmt: skip
    texto_exigido = f"{_numero(exigido.numero)}%"
    if exigido.numero <= maxima:
        return _item(
            campo, "atende", texto_exigido, texto_perfil, "dentro da garantia máxima", exigido
        )
    return _item(
        campo, "verificar", texto_exigido, texto_perfil,
        "acima da garantia máxima do perfil (decisão financeira)", exigido,
    )  # fmt: skip


def _visita(exigido: CategoriaEvidencia, perfil: Perfil) -> ItemComparacao:
    faz = "faz visita técnica" if perfil.faz_visita_tecnica else "não faz visita técnica"
    if exigido.valor == "obrigatoria" and not perfil.faz_visita_tecnica:
        return _item(
            "visita_tecnica", "nao_atende", exigido.valor, faz, "visita obrigatória", exigido
        )
    return _item("visita_tecnica", "atende", exigido.valor, faz, "sem impedimento", exigido)


def _consorcio(exigido: CategoriaEvidencia, perfil: Perfil) -> ItemComparacao:
    texto_perfil = "depende de consórcio" if perfil.depende_de_consorcio else "participa sozinha"
    if exigido.valor == "vedado" and perfil.depende_de_consorcio:
        return _item(
            "consorcio", "nao_atende", exigido.valor, texto_perfil, "consórcio vedado", exigido
        )
    return _item("consorcio", "atende", exigido.valor, texto_perfil, "sem impedimento", exigido)


def _me_epp(exigido: CategoriaEvidencia, perfil: Perfil) -> ItemComparacao:
    porte = f"porte {perfil.porte}"
    if exigido.valor == "exclusiva" and perfil.porte == "demais":
        return _item("me_epp", "nao_atende", exigido.valor, porte, "exclusiva para ME/EPP", exigido)
    if exigido.valor == "cota" and perfil.porte == "demais":
        return _item("me_epp", "atende", exigido.valor, porte, "concorre na cota ampla", exigido)
    return _item("me_epp", "atende", exigido.valor, porte, "sem impedimento", exigido)


def _documentos(exigidos: list[CategoriaEvidencia], perfil: Perfil) -> ItemComparacao:
    faltam = [d.valor for d in exigidos if d.valor not in perfil.documentos]
    exigido = ", ".join(d.valor for d in exigidos)
    primeiro = exigidos[0]
    if faltam:
        return _item(
            "documentos", "verificar", exigido, ", ".join(perfil.documentos),
            f"o perfil não lista: {', '.join(faltam)} (dá para tirar)", primeiro,
        )  # fmt: skip
    return _item(
        "documentos", "atende", exigido, ", ".join(perfil.documentos),
        "todos os documentos exigidos estão no perfil", primeiro,
    )  # fmt: skip


def _atestado(exigido: Atestado, perfil: Perfil) -> ItemComparacao:
    descricao = sem_acento(exigido.descricao)
    texto_exigido = exigido.descricao
    if exigido.quantidade is not None:
        texto_exigido += f" ({_numero(exigido.quantidade)} {exigido.unidade or ''})".rstrip()
    for atestado in perfil.atestados:
        if not any(sem_acento(termo) in descricao for termo in atestado.termos):
            continue
        unidade, fator = normalizar_unidade(atestado.unidade)
        quantidade = atestado.quantidade * fator
        texto_perfil = f"{atestado.descricao} ({_numero(quantidade)} {unidade})"
        if exigido.quantidade is None:
            return _item(
                "atestados", "atende", texto_exigido, texto_perfil,
                "atestado do perfil casa pelo termo", exigido,
            )  # fmt: skip
        if unidade == exigido.unidade and quantidade >= exigido.quantidade:
            return _item(
                "atestados", "atende", texto_exigido, texto_perfil,
                "atestado do perfil casa pelo termo, mesma unidade e quantidade suficiente",
                exigido,
            )  # fmt: skip
    return _item(
        "atestados", "verificar", texto_exigido, "nenhum atestado do perfil casa",
        "casamento por termo é aproximado: confira", exigido,
    )  # fmt: skip


def comparar(exigencias: Exigencias, perfil: Perfil, incompleta: bool) -> Comparacao:
    """Um item por exigência presente e o veredito, segundo o que foi extraído."""
    itens: list[ItemComparacao] = []
    e = exigencias
    if e.capital_social_minimo is not None:
        itens.append(
            _monetario("capital_social_minimo", e.capital_social_minimo, perfil.capital_social)
        )
    if e.patrimonio_liquido_minimo is not None:
        itens.append(
            _monetario(
                "patrimonio_liquido_minimo", e.patrimonio_liquido_minimo, perfil.patrimonio_liquido
            )
        )
    for campo, valor in (
        ("garantia_proposta_percentual", e.garantia_proposta_percentual),
        ("garantia_contratual_percentual", e.garantia_contratual_percentual),
    ):
        if valor is not None:
            itens.append(_garantia(campo, valor, perfil.garantia_maxima_percentual))
    if e.visita_tecnica is not None:
        itens.append(_visita(e.visita_tecnica, perfil))
    if e.consorcio is not None:
        itens.append(_consorcio(e.consorcio, perfil))
    if e.me_epp is not None:
        itens.append(_me_epp(e.me_epp, perfil))
    if e.prazo_execucao_dias is not None:
        prazo = e.prazo_execucao_dias
        texto = "não informado" if prazo.numero is None else f"{_numero(prazo.numero)} dias"
        itens.append(
            _item("prazo_execucao_dias", "informativo", texto, "-", "prazo de execução", prazo)
        )
    if e.documentos:
        itens.append(_documentos(e.documentos, perfil))
    itens.extend(_atestado(a, perfil) for a in e.atestados)

    situacoes = {i.situacao for i in itens}
    veredito: Veredito
    if "nao_atende" in situacoes:
        veredito = "inapto"
    elif "verificar" in situacoes or incompleta:
        veredito = "verificar"
    else:
        veredito = "apto"
    return Comparacao(veredito=veredito, itens=itens)
