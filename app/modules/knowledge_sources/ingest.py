"""Explicit curator CLI; downloads/archives public sources and stages draft excerpts."""

import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter

from app.config import Settings
from app.core.clock import SystemClock
from app.db.connection import check_ready, create_pool
from app.integrations.knowledge_sources.extract import SourceExtractionError
from app.integrations.knowledge_sources.fetch import SourceFetchError
from app.modules.knowledge_sources.preparation import ROOT, prepare_source
from app.modules.knowledge_sources.schemas import SourceSpec
from app.modules.knowledge_sources.service import SourceService
from app.trust.errors import TrustError
from app.trust.security import Actor


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, action="append", help="Curator-controlled JSON list"
    )
    parser.add_argument(
        "--use-cache", action="store_true", help="Use hash-pinned local PDFs if present"
    )
    parser.add_argument("--source", action="append", help="Restrict to selected source keys")
    args = parser.parse_args()
    manifests = args.manifest or sorted((ROOT / "app/policies/corpus").glob("*.json"))
    sources = [
        source
        for path in manifests
        for source in TypeAdapter(list[SourceSpec]).validate_json(path.read_text())
    ]
    if args.source:
        sources = [source for source in sources if source.key in args.source]
        if set(args.source) != {source.key for source in sources}:
            parser.error("Unknown source key")
    settings = Settings.from_environment()
    database_url = settings.trust_database_url or settings.database_url
    if not check_ready(database_url):
        raise TrustError("DATABASE_NOT_READY", 503)
    # No signing or approval capability is needed for this local curator operation.
    # The source service constructor's settings are unused by archive persistence.
    with create_pool(database_url) as pool:
        service = SourceService(None, pool, SystemClock())
        counts = {
            "sources": 0,
            "scoped_records": 0,
            "draft": 0,
            "quarantined": 0,
            "failed": 0,
            "replayed": 0,
        }
        for spec in sources:
            try:
                prepared = prepare_source(spec, use_cache=args.use_cache)
                counts["sources"] += 1
                for scope in spec.scopes:
                    actor = Actor("corpus-curator", "knowledge", scope.principal, scope.product)
                    result = service.stage(actor, prepared)
                    counts["scoped_records"] += 1
                    for key in ("draft", "quarantined", "replayed"):
                        counts[key] += result[key]
                    print(
                        json.dumps({"source_key": spec.key, "principal": scope.principal, **result})
                    )
            except (TrustError, SourceFetchError, SourceExtractionError) as exc:
                counts["failed"] += 1
                print(json.dumps({"source_key": spec.key, "error": exc.code}))
            except Exception:  # noqa: BLE001 - CLI boundary must redact provider/parser/DB errors
                counts["failed"] += 1
                # Never echo parser/network bodies, DSN, or exception text.
                print(json.dumps({"source_key": spec.key, "error": "SOURCE_PREPARATION_FAILED"}))
        print(json.dumps({"summary": counts, "automatic_approvals": 0}))
        return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
