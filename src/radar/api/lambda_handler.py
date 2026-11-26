"""Ponto de entrada da AWS Lambda (HTTP API v2) via Mangum.

O app FastAPI é criado uma vez por container (nível do módulo); o engine usa NullPool
(`RADAR_DB_NULLPOOL=true`) e é preguiçoso: importar este módulo não abre conexão.

A URL do banco vem do SSM (`/radar/prod/database_url`) quando `RADAR_DATABASE_URL` não está
definida (`radar.comum.garantir_url_do_banco`, a mesma do processador).
"""

from mangum import Mangum

from radar.api.app import criar_app
from radar.comum import garantir_url_do_banco

garantir_url_do_banco()
app = criar_app()
handler = Mangum(app, lifespan="off")
