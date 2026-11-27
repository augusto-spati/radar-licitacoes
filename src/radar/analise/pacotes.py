"""ZIP de documentos de edital (Compesa e Sanepar): escolha por nome e leitura com limites.

Escolha, na ordem: edital, termos de referência e documentos de habilitação (a Sanepar põe
capital e patrimônio mínimos numa "Resolução de Habilitação Econômico-Financeira"); PDF ou
.docx; até 3 no total. Planilhas, declarações, projetos e ZIP/RAR aninhados ficam de fora.
"""

import io
import re
import zipfile
from dataclasses import dataclass

from radar.analise.arquivos_pncp import MAX_DOCUMENTOS, ErroArquivos, GrandeDemais
from radar.analise.normalizacao import sem_acento

LIMITE_ENTRADA = 30_000_000  # descompactado, por documento escolhido
MAX_ENTRADAS = 5000

_EXTENSOES = (".pdf", ".docx")
_EDITAL = re.compile(r"(^|[^a-z])edita(l)?([^a-z]|$)")
_TERMO_REFERENCIA = re.compile(r"termo[\W_]*(de[\W_]*)?ref|(^|[\W_])tr([\W_]|$)")
_TR_FORA = re.compile(r"seguranca|projeto|proj[\W_]*executivo")
_HABILITACAO = re.compile(r"habilita|qualificacao[\W_]*(economic|tecnic)")
_HABILITACAO_FORA = re.compile(r"declara|modelo|carta")
_LIXO = re.compile(r"(^|/)(~\$|thumbs\.db$|__macosx/)")


@dataclass(frozen=True)
class Escolhido:
    nome: str  # o nome da entrada como o zipfile o lê (chave para ler)
    tipo: str


def base_do_nome(nome: str) -> str:
    return nome.rsplit("/", 1)[-1]


def legivel(nome: str) -> str:
    """Nome de entrada de ZIP sem a marca de UTF-8: o zipfile o lê em cp437. ZIP do Windows
    (o da LC 168/2026 da Compesa) grava mesmo na página OEM e já sai certo; quem grava UTF-8
    sem a marca sai embaralhado e volta pelos bytes."""
    try:
        return nome.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return nome


def escolher_no_pacote(nomes: list[str], limite: int = MAX_DOCUMENTOS) -> list[Escolhido]:
    """Editais, depois termos de referência, depois habilitação; PDF ou .docx; até `limite`."""
    editais: list[Escolhido] = []
    termos: list[Escolhido] = []
    habilitacao: list[Escolhido] = []
    for nome in sorted(nomes):
        if nome.endswith("/") or _LIXO.search(nome.lower()):
            continue
        base = sem_acento(base_do_nome(legivel(nome)))
        if not base.endswith(_EXTENSOES):
            continue
        if _EDITAL.search(base) and not base.startswith("anexo"):
            editais.append(Escolhido(nome, "Edital"))
        elif _TERMO_REFERENCIA.search(base) and not _TR_FORA.search(base):
            termos.append(Escolhido(nome, "Termo de referência"))
        elif _HABILITACAO.search(base) and not _HABILITACAO_FORA.search(base):
            habilitacao.append(Escolhido(nome, "Habilitação"))
    return (editais + termos + habilitacao)[:limite]


def ler_pacote(dados: bytes) -> zipfile.ZipFile | None:
    """O ZIP, ou None se os bytes não são ZIP. Mais de MAX_ENTRADAS arquivos → ErroArquivos."""
    try:
        pacote = zipfile.ZipFile(io.BytesIO(dados))
    except zipfile.BadZipFile:
        return None
    if len(pacote.infolist()) > MAX_ENTRADAS:
        raise ErroArquivos(f"pacote com mais de {MAX_ENTRADAS} arquivos")
    return pacote


def ler_entrada(pacote: zipfile.ZipFile, nome: str) -> bytes:
    """Bytes de uma entrada, com limite de tamanho descompactado (declarado e real)."""
    info = pacote.getinfo(nome)
    if info.file_size > LIMITE_ENTRADA:
        raise GrandeDemais(f"{base_do_nome(nome)}: {info.file_size} bytes descompactados")
    with pacote.open(info) as arquivo:
        dados = arquivo.read(LIMITE_ENTRADA + 1)
    if len(dados) > LIMITE_ENTRADA:  # tamanho declarado mentiroso
        raise GrandeDemais(f"{base_do_nome(nome)}: mais de {LIMITE_ENTRADA} bytes descompactados")
    return dados
