"""Documentos de um edital da Compesa: portal SCL (WebRun) e, nas obras, a nuvem da Compesa.

Caminho, todo em GET (o WAF do portal rejeita POST fora de contexto), visto em 27/11/2026:

1. O id do pacote de anexos sai da regra que o botão "Download do edital" chama, com o número
   da licitação (`bruto["numero"]`, ex. `LC 220/2026 CPL`) e o participante "NÃO IDENTIFICADO"
   (a opção sem cadastro do próprio portal): `executeRule.do?...&ruleName=Carrega o id do
   anexo do edital&P_0=<número>&P_1=NÃO IDENTIFICADO` responde
   `$mainform().document._ruleReturn = 13904;` (ou `''`: sem anexo no portal).
2. O pacote é `Upload/<ZIP_ANEXOS sem .ZIP><id>.ZIP` (`ZIP_ANEXOS` é campo oculto da grade,
   guardado pela coleta em `bruto["campos"]`): `LC220-2026CPL.ZIP` + 13904 →
   `Upload/LC220-2026CPL13904.ZIP`.
3. Nas compras o ZIP já traz os PDFs (`EDITAL-LC213-2026.pdf`, `ANEXO-VI-TERMO-REFERENCIA.pdf`).
   Nas obras ele traz só um `LINKEDITAL....doc` com o link público da nuvem
   (`https://nuvem.compesa.com.br/public.php?service=files&t=<32 hex>`), um ownCloud antigo. O
   `&download` desse link entrega a pasta (ou o ZIP compartilhado) inteira, sem Range: a da
   LC 168/2026 tem 59 MB, com o edital e o termo de referência em .docx.

Escolha: o edital e os termos de referência, até 3, PDF ou .docx; o resto (planilhas,
declarações, projetos, ZIP e RAR aninhados) fica de fora. Cada cliente só baixa do próprio host
(portal ou nuvem), inclusive nos redirects. O portal não responde de dentro da AWS: a análise
da Compesa roda onde a coleta dela roda (o notebook), com `radar analisar --fontes compesa`.
"""

import io
import re
import zipfile
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from radar.analise.arquivos_pncp import (
    MAX_DOCUMENTOS,
    TIMEOUT_SEGUNDOS,
    USER_AGENT,
    ArquivoPNCP,
    ClienteArquivosPNCP,
    ErroArquivos,
    GrandeDemais,
)
from radar.analise.normalizacao import sem_acento

BASE_PORTAL = "https://portalscl.compesa.com.br:8743/webrunstudio"
ORIGEM_PORTAL = ("https", "portalscl.compesa.com.br")
ORIGEM_NUVEM = ("https", "nuvem.compesa.com.br")
PAUSA_COMPESA_S = 2.0
LIMITE_ZIP_PORTAL = 60_000_000
LIMITE_PACOTE_NUVEM = 150_000_000
LIMITE_ENTRADA = 30_000_000  # descompactado, por documento escolhido
MAX_ENTRADAS = 5000
REGRA_ID_ANEXO = "Carrega o id do anexo do edital"
PARTICIPANTE_ANONIMO = "NÃO IDENTIFICADO"

_RETORNO_REGRA = re.compile(r"_ruleReturn\s*=\s*'?(\d*)'?\s*;")
_LINK_NUVEM = re.compile(
    rb"https://nuvem\.compesa\.com\.br/public\.php\?service=files&t=([0-9a-f]{32})"
)
_EXTENSOES = (".pdf", ".docx")
_EDITAL = re.compile(r"(^|[^a-z])edita(l)?([^a-z]|$)")
_TERMO_REFERENCIA = re.compile(r"termo[\W_]*(de[\W_]*)?refer|(^|[\W_])tr([\W_]|$)")
_TR_FORA = re.compile(r"seguranca|projeto|proj[\W_]*executivo")
_LIXO = re.compile(r"(^|/)(~\$|thumbs\.db$|__macosx/)")


@dataclass(frozen=True)
class _Escolhido:
    nome: str
    tipo: str


def _base(nome: str) -> str:
    return nome.rsplit("/", 1)[-1]


def legivel(nome: str) -> str:
    """Nome de entrada de ZIP sem a marca de UTF-8: o zipfile o lê em cp437. ZIP do Windows
    (o da LC 168/2026) grava mesmo na página OEM e já sai certo; quem grava UTF-8 sem a marca
    sai embaralhado e volta pelos bytes."""
    try:
        return nome.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return nome


def escolher_no_pacote(nomes: list[str]) -> list[_Escolhido]:
    """Edital(is) primeiro, depois termos de referência; até 3; PDF ou .docx; na ordem do nome."""
    editais: list[_Escolhido] = []
    termos: list[_Escolhido] = []
    for nome in sorted(nomes):
        if nome.endswith("/") or _LIXO.search(nome.lower()):
            continue
        base = sem_acento(_base(legivel(nome)))
        if not base.endswith(_EXTENSOES):
            continue
        if _EDITAL.search(base) and not base.startswith("anexo"):
            editais.append(_Escolhido(nome, "Edital"))
        elif _TERMO_REFERENCIA.search(base) and not _TR_FORA.search(base):
            termos.append(_Escolhido(nome, "Termo de referência"))
    return (editais + termos)[:MAX_DOCUMENTOS]


def _ler_pacote(dados: bytes) -> zipfile.ZipFile | None:
    try:
        pacote = zipfile.ZipFile(io.BytesIO(dados))
    except zipfile.BadZipFile:
        return None
    if len(pacote.infolist()) > MAX_ENTRADAS:
        raise ErroArquivos(f"pacote com mais de {MAX_ENTRADAS} arquivos")
    return pacote


def _ler_entrada(pacote: zipfile.ZipFile, nome: str) -> bytes:
    info = pacote.getinfo(nome)
    if info.file_size > LIMITE_ENTRADA:
        raise GrandeDemais(f"{_base(nome)}: {info.file_size} bytes descompactados")
    with pacote.open(info) as arquivo:
        dados = arquivo.read(LIMITE_ENTRADA + 1)
    if len(dados) > LIMITE_ENTRADA:  # tamanho declarado mentiroso
        raise GrandeDemais(f"{_base(nome)}: mais de {LIMITE_ENTRADA} bytes descompactados")
    return dados


def _link_da_nuvem(pacote: zipfile.ZipFile) -> str | None:
    for info in pacote.infolist():
        if info.is_dir() or info.file_size > LIMITE_ENTRADA:
            continue
        if not sem_acento(_base(info.filename)).endswith(
            (".doc", ".docx", ".rtf", ".txt", ".htm", ".html")
        ):
            continue
        achado = _LINK_NUVEM.search(pacote.read(info))
        if achado:
            return f"https://nuvem.compesa.com.br/public.php?service=files&t={achado.group(1).decode()}"
    return None


class DocumentosCompesa:
    """`listar(bruto)` acha e baixa os documentos; `baixar(arquivo)` os devolve da memória."""

    def __init__(self, portal: ClienteArquivosPNCP, nuvem: ClienteArquivosPNCP) -> None:
        self._portal = portal
        self._nuvem = nuvem
        self._baixados: dict[str, bytes] = {}

    def fechar(self) -> None:
        self._portal.fechar()
        self._nuvem.fechar()

    def baixar(self, url: str) -> bytes:
        try:
            return self._baixados[url]
        except KeyError:
            raise ErroArquivos(f"documento da Compesa não listado: {url}") from None

    def id_do_anexo(self, numero: str) -> str | None:
        consulta = urlencode(
            {
                "action": "executeRule",
                "pType": "2",
                "ruleName": REGRA_ID_ANEXO,
                "sys": "SCL",
                "formID": "8",
                "parentRID": "-1",
                "P_0": numero,
                "P_1": PARTICIPANTE_ANONIMO,
            }
        )
        resposta = self._portal.baixar(f"{BASE_PORTAL}/executeRule.do?{consulta}", 100_000)
        achado = _RETORNO_REGRA.search(resposta.decode("latin-1"))
        if achado is None:
            raise ErroArquivos(f"regra do anexo respondeu fora do formato: {resposta[:120]!r}")
        return achado.group(1) or None

    def listar(self, bruto: dict[str, Any] | None) -> list[ArquivoPNCP]:
        self._baixados.clear()  # só os documentos do edital atual ficam na memória
        numero = str((bruto or {}).get("numero") or "")
        campos = (bruto or {}).get("campos")
        nome_zip = str(campos.get("ZIP_ANEXOS") or "") if isinstance(campos, dict) else ""
        if not numero or not nome_zip.upper().endswith(".ZIP"):
            return []
        ident = self.id_do_anexo(numero)
        if ident is None:
            return []
        url_zip = f"{BASE_PORTAL}/Upload/{nome_zip[:-4]}{ident}.ZIP"
        pacote = _ler_pacote(self._portal.baixar(url_zip, LIMITE_ZIP_PORTAL))
        if pacote is None:
            raise ErroArquivos(f"pacote do portal não é ZIP: {url_zip}")
        escolhidos = escolher_no_pacote(pacote.namelist())
        if escolhidos:
            return self._guardar(url_zip, pacote, escolhidos)
        link = _link_da_nuvem(pacote)
        if link is None:
            return []
        dados = self._nuvem.baixar(f"{link}&download", LIMITE_PACOTE_NUVEM)
        da_nuvem = _ler_pacote(dados)
        if da_nuvem is None:
            # Compartilhamento de um arquivo só (PDF ou .docx): é o próprio documento.
            self._baixados[link] = dados
            return [
                ArquivoPNCP(
                    sequencial=1, titulo="Edital (nuvem da Compesa)", tipo_nome="Edital", url=link
                )
            ]
        return self._guardar(link, da_nuvem, escolher_no_pacote(da_nuvem.namelist()))

    def _guardar(
        self, origem: str, pacote: zipfile.ZipFile, escolhidos: list[_Escolhido]
    ) -> list[ArquivoPNCP]:
        arquivos: list[ArquivoPNCP] = []
        for sequencial, escolhido in enumerate(escolhidos, start=1):
            url = f"{origem}#{escolhido.nome}"
            try:
                self._baixados[url] = _ler_entrada(pacote, escolhido.nome)
            except GrandeDemais:
                continue  # o documento seguinte ainda pode servir
            arquivos.append(
                ArquivoPNCP(
                    sequencial=sequencial,
                    titulo=_base(legivel(escolhido.nome)),
                    tipo_nome=escolhido.tipo,
                    url=url,
                )
            )
        return arquivos


def criar_documentos_compesa() -> DocumentosCompesa:
    def cliente(origem: tuple[str, str], prazo_s: float) -> ClienteArquivosPNCP:
        http = httpx.Client(
            timeout=TIMEOUT_SEGUNDOS,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"},
        )
        return ClienteArquivosPNCP(
            http, pausa_s=PAUSA_COMPESA_S, origem=origem, prazo_download_s=prazo_s
        )

    # O pacote da nuvem pode ter 150 MB e vem inteiro (sem Range): prazo maior.
    return DocumentosCompesa(cliente(ORIGEM_PORTAL, 300.0), cliente(ORIGEM_NUVEM, 900.0))
