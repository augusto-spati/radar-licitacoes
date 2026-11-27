"""Dado bruto fora do banco: PDFs e textos extraídos, numa pasta local ou no S3."""

from pathlib import Path
from typing import Any, Protocol


class Armazem(Protocol):
    def guardar(self, chave: str, dados: bytes) -> None: ...

    def ler(self, chave: str) -> bytes | None: ...


class ArmazemLocal:
    def __init__(self, raiz: Path) -> None:
        self.raiz = raiz

    def guardar(self, chave: str, dados: bytes) -> None:
        caminho = self.raiz / chave
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(dados)

    def ler(self, chave: str) -> bytes | None:
        caminho = self.raiz / chave
        return caminho.read_bytes() if caminho.is_file() else None


class ArmazemS3:
    def __init__(self, bucket: str, prefixo: str = "", cliente: Any | None = None) -> None:
        self.bucket = bucket
        self.prefixo = prefixo
        self._cliente = cliente

    @property
    def cliente(self) -> Any:
        if self._cliente is None:
            import boto3  # type: ignore[import-untyped]

            self._cliente = boto3.client("s3")
        return self._cliente

    def guardar(self, chave: str, dados: bytes) -> None:
        self.cliente.put_object(Bucket=self.bucket, Key=self.prefixo + chave, Body=dados)

    def ler(self, chave: str) -> bytes | None:
        try:
            resposta = self.cliente.get_object(Bucket=self.bucket, Key=self.prefixo + chave)
        except Exception as erro:  # ClientError do botocore, sem importá-lo aqui
            codigo = getattr(erro, "response", {}).get("Error", {}).get("Code")
            if codigo in ("NoSuchKey", "404"):
                return None
            raise
        dados: bytes = resposta["Body"].read()
        return dados


def criar_armazem(destino: str) -> Armazem:
    """`s3://bucket/prefixo` ou uma pasta local."""
    if destino.startswith("s3://"):
        bucket, _, prefixo = destino.removeprefix("s3://").partition("/")
        prefixo = prefixo.strip("/")
        return ArmazemS3(bucket, f"{prefixo}/" if prefixo else "")
    return ArmazemLocal(Path(destino).expanduser())
