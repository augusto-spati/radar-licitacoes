# Task 1 report

Implemented: src/radar/api/lambda_handler.py (Mangum, app once per container, SSM read lazy via boto3 only when RADAR_DATABASE_URL unset), docker/api-lambda.Dockerfile, docker/coletor.Dockerfile, .dockerignore, tests/unit/test_lambda_handler.py, mangum in pyproject/uv.lock.
boto3: NOT a project dependency; comes with the AWS Lambda Python base image (1.43.127 confirmed in image). mypy import uses a type: ignore.
Tests: RED (ModuleNotFoundError, 7 errors) then GREEN (7 passed). Full suite 102 passed; ruff, format, mypy, lint-imports OK.
Review Focus 4: both %2F and / rawPath reach the detail route with id "sanepar~OE-345/26" (domain lookup monkeypatched).
Docker: api image 624MB, coletor image 202MB; `docker run radar-coletor --help` shows CLI help; lambda image imports handler OK. Coletor installs the project (pip --no-deps .) so ENTRYPOINT ["radar"] works; contains config/, migrations/, alembic.ini.
Concerns: Mangum DeprecationWarning (asyncio.get_event_loop) from mangum 0.22 on py3.13.
