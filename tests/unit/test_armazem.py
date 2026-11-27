from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from radar.analise.armazem import ArmazemLocal, ArmazemS3, criar_armazem


class _Corpo:
    def __init__(self, dados: bytes) -> None:
        self._dados = dados

    def read(self) -> bytes:
        return self._dados


class S3Falso:
    def __init__(self) -> None:
        self.objetos: dict[tuple[str, str], bytes] = {}
        self.chamadas: list[tuple[str, str, str]] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> dict[str, Any]:  # noqa: N803
        self.chamadas.append(("put_object", Bucket, Key))
        self.objetos[(Bucket, Key)] = Body
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        self.chamadas.append(("get_object", Bucket, Key))
        if (Bucket, Key) not in self.objetos:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject")
        return {"Body": _Corpo(self.objetos[(Bucket, Key)])}


def test_armazem_local_ida_e_volta(tmp_path: Path) -> None:
    armazem = ArmazemLocal(tmp_path)
    armazem.guardar("documentos/abc.pdf", b"%PDF")
    assert armazem.ler("documentos/abc.pdf") == b"%PDF"
    assert (tmp_path / "documentos" / "abc.pdf").is_file()


def test_armazem_s3_usa_bucket_e_prefixo() -> None:
    s3 = S3Falso()
    armazem = ArmazemS3("radar-documentos-1", "pre/", cliente=s3)
    armazem.guardar("textos/abc.json", b"[]")
    assert s3.chamadas == [("put_object", "radar-documentos-1", "pre/textos/abc.json")]
    assert armazem.ler("textos/abc.json") == b"[]"


def test_ler_ausente_devolve_none(tmp_path: Path) -> None:
    assert ArmazemLocal(tmp_path).ler("nada") is None
    assert ArmazemS3("b", cliente=S3Falso()).ler("nada") is None


def test_criar_armazem(tmp_path: Path) -> None:
    assert isinstance(criar_armazem(str(tmp_path)), ArmazemLocal)
    s3 = criar_armazem("s3://meu-bucket/docs")
    assert isinstance(s3, ArmazemS3)
    assert (s3.bucket, s3.prefixo) == ("meu-bucket", "docs/")
    raiz = criar_armazem("s3://meu-bucket/")
    assert isinstance(raiz, ArmazemS3)
    assert raiz.prefixo == ""
