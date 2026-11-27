"""PDF mínimo com texto, gerado no teste (para não commitar PDF): uma página por lista de linhas.

Helvetica com WinAnsiEncoding: acentos do português saem certos no `extract_text` do pypdf.
"""


def _escapar(linha: str) -> bytes:
    bruto = linha.encode("cp1252", errors="replace")
    return bruto.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def pdf_com_texto(paginas: list[list[str]]) -> bytes:
    objetos: list[bytes] = []
    n_paginas = len(paginas)
    # 1 catálogo, 2 páginas, 3 fonte, depois (página, conteúdo) para cada página.
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_paginas))
    objetos.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objetos.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_paginas} >>".encode())
    objetos.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    for i, linhas in enumerate(paginas):
        conteudo = (
            b"BT /F1 7 Tf 10 TL 20 580 Td "
            + b" ".join(b"(" + _escapar(linha) + b") Tj T*" for linha in linhas)
            + b" ET"
        )
        objetos.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        objetos.append(
            f"<< /Length {len(conteudo)} >>\nstream\n".encode() + conteudo + b"\nendstream"
        )
    saida = bytearray(b"%PDF-1.4\n")
    posicoes = []
    for numero, corpo in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += f"{numero} 0 obj\n".encode() + corpo + b"\nendobj\n"
    inicio_xref = len(saida)
    saida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    for posicao in posicoes:
        saida += f"{posicao:010d} 00000 n \n".encode()
    saida += (
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{inicio_xref}\n%%EOF\n"
    ).encode()
    return bytes(saida)
