#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DATABASE_URL:?Set DATABASE_URL to the intended local PostgreSQL + pgvector database}"
uv run python - <<'PY'
import hashlib
import os
import tarfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from app.config import Settings

Settings.model_config['env_file'] = None
bundle = Path('deployment/graceline-kb-public-sources.tar.gz')
expected = Path('deployment/graceline-kb-public-sources.sha256').read_text().split()[0]
if hashlib.sha256(bundle.read_bytes()).hexdigest() != expected:
    raise SystemExit('Knowledge bundle hash mismatch')
with tarfile.open(bundle, 'r:gz') as archive:
    for member in archive.getmembers():
        path = Path(member.name)
        if not member.isfile() or not path.is_relative_to('data/knowledge_sources') or '..' in path.parts:
            raise SystemExit('Unsafe knowledge archive entry')
    archive.extractall('.', filter='data')
config = Config('alembic.ini')
config.attributes['database_url'] = os.environ.get('TRUST_DATABASE_URL') or os.environ['DATABASE_URL']
command.upgrade(config, 'head')
print('Verified KB bundle and database schema. Staging public sources as drafts.')
PY
uv run python -m app.modules.knowledge_sources.ingest --use-cache
