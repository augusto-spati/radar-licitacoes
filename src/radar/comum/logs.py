import json
import logging
import sys
from datetime import UTC, datetime

_CAMPOS_PADRAO = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys() | {"message", "asctime"}
)


def _ids_do_trace() -> dict[str, str]:
    """`trace_id`, `span_id` e `xray_trace_id` do span corrente, se houver um válido.

    Sem a API do OTel carregada não existe span: o log não paga o import dela.
    """
    otel = sys.modules.get("opentelemetry.trace")
    if otel is None:
        return {}
    contexto = otel.get_current_span().get_span_context()
    if not contexto.is_valid:
        return {}
    trace_id = f"{contexto.trace_id:032x}"
    return {
        "trace_id": trace_id,
        "span_id": f"{contexto.span_id:016x}",
        # Formato do console do X-Ray, para colar direto na busca.
        "xray_trace_id": f"1-{trace_id[:8]}-{trace_id[8:]}",
    }


class FormatadorJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        evento: dict[str, object] = {
            "momento": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "nivel": record.levelname,
            "logger": record.name,
            "evento": record.getMessage(),
        }
        evento.update(_ids_do_trace())
        for chave, valor in record.__dict__.items():
            if chave not in _CAMPOS_PADRAO:
                evento[chave] = valor
        if record.exc_info:
            evento["excecao"] = self.formatException(record.exc_info)
        return json.dumps(evento, ensure_ascii=False, default=str)


def configurar_logs(nivel: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(FormatadorJson())
    raiz = logging.getLogger()
    raiz.handlers = [handler]
    raiz.setLevel(nivel)
