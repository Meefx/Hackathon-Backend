"""Raw public archives require reviewer authorization and authenticated scope."""

import hashlib
import json
from io import BytesIO
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pypdf import PdfWriter

from app.core.clock import FrozenClock
from app.modules.knowledge_sources import repository, service
from app.trust.app import create_trust_app
from app.trust.db import connection
from app.trust.errors import TrustError
from app.trust.security import Actor, require
from app.trust.settings import TrustSettings

PREFIX = "/api/v1/trust/knowledge/sources"
REVIEWERS = ("knowledge", "qa", "compliance", "admin")
NON_REVIEWERS = ("personal", "process", "manager", "operator", "officer")


def auth(client, role="knowledge", principal="cf"):
    response = client.post(
        "/api/v1/trust/auth/demo-login", json={"role": role, "principal": principal}
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.fixture
def source_archive(trust_dsn, trust_now):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    body = output.getvalue()
    digest = hashlib.sha256(body).hexdigest()
    pages = [{"number": 1, "text": "Synthetic regulatory source awaiting reviewer examination."}]
    extraction_digest = hashlib.sha256(
        json.dumps(pages, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    source_id = uuid4()
    with connection(trust_dsn) as conn:
        conn.execute(
            """INSERT INTO trust.source_artifacts
            (sha256,payload,content_type,page_count,stored_at)
            VALUES (%s,%s,'application/pdf',1,%s)""",
            (digest, body, trust_now),
        )
        conn.execute(
            """INSERT INTO trust.source_records
            (id,principal,product,source_key,title,publisher,source_url,final_url,
             source_version,source_class,artifact_sha256,manifest_sha256,legal_status,
             effective_from,effective_until,metadata,created_by,created_at,extracted_pages,
             extraction_sha256,extraction_method,extraction_review_required)
            VALUES (%s,'cf','spaylater','synthetic-source','Synthetic review source',
             'Synthetic regulator','https://regulator.example/source.pdf',
             'https://regulator.example/source.pdf','synthetic-v1','primary',%s,%s,
             'review_required',NULL,NULL,%s,'synthetic-curator',%s,%s,%s,'ocr',true)""",
            (
                source_id,
                digest,
                "a" * 64,
                Jsonb({"synthetic": True, "status_notes": "Review required before use."}),
                trust_now,
                Jsonb(pages),
                extraction_digest,
            ),
        )
        conn.execute(
            """INSERT INTO trust.source_provisions
            (source_id,principal,product,provision_key,locator,page_start,page_end,
             knowledge_document_id,stage_status,reason_code,text_sha256,metadata)
            VALUES (%s,'cf','spaylater','synthetic-article','Synthetic article 1',1,1,
             NULL,'quarantined','SOURCE_EXTRACTION_REVIEW_REQUIRED',%s,%s)""",
            (source_id, extraction_digest, Jsonb({"synthetic": True})),
        )
    return source_id, body, digest


@pytest.fixture
def source_client(trust_dsn, trust_now, source_archive):
    settings = TrustSettings(trust_dsn, "j" * 48, "a" * 48, demo_auth_enabled=True)
    with TestClient(create_trust_app(settings, FrozenClock(trust_now))) as client:
        yield client


@pytest.mark.parametrize("role", REVIEWERS + NON_REVIEWERS)
@pytest.mark.parametrize("capability", ("source:read", "source:download"))
def test_source_capabilities_are_reviewer_only(role, capability):
    actor = Actor("synthetic-reviewer", role, "cf", "spaylater")
    if role in REVIEWERS:
        require(actor, capability)
    else:
        with pytest.raises(TrustError) as error:
            require(actor, capability)
        assert error.value.code == "CAPABILITY_DENIED"
        assert error.value.status == 403


def test_reviewers_can_inspect_quarantine_and_download_exact_pdf(
    source_client, source_archive, trust_dsn
):
    source_id, body, digest = source_archive
    for role in REVIEWERS:
        headers = auth(source_client, role)
        listing = source_client.get(PREFIX, headers=headers)
        assert listing.status_code == 200
        page = listing.json()
        assert page["total"] == 1
        assert page["limit"] == 25
        assert page["offset"] == 0
        assert page["items"][0]["id"] == str(source_id)
        assert page["items"][0]["artifact_sha256"] == digest
        assert page["items"][0]["extraction_review_required"] is True
        assert "payload" not in page["items"][0]

        detail = source_client.get(f"{PREFIX}/{source_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["legal_status"] == "review_required"
        assert detail.json()["provisions"][0]["stage_status"] == "quarantined"
        assert detail.json()["provisions"][0]["knowledge_document_id"] is None
        assert detail.json()["provisions"][0]["knowledge_status"] is None

        download = source_client.get(f"{PREFIX}/{source_id}/download", headers=headers)
        assert download.status_code == 200
        assert download.content == body
        assert download.headers["content-type"] == "application/pdf"
        assert download.headers["content-disposition"] == (
            f'attachment; filename="source-{source_id}.pdf"'
        )
        assert download.headers["x-content-sha256"] == hashlib.sha256(download.content).hexdigest()
        assert download.headers["etag"] == f'"{digest}"'
        assert download.headers["x-content-type-options"] == "nosniff"
        assert download.headers["cache-control"] == "no-store"
    with connection(trust_dsn) as conn:
        events = conn.execute(
            "SELECT actor_role,payload FROM trust.audit_events WHERE event_type='source.downloaded'"
        ).fetchall()
    assert {event["actor_role"] for event in events} == set(REVIEWERS)
    assert all(
        event["payload"] == {"source_id": str(source_id), "artifact_sha256": digest}
        for event in events
    )


def test_download_rejects_changed_bytes_before_recording_success(
    source_client, source_archive, trust_dsn, monkeypatch
):
    source_id, body, _ = source_archive
    read = repository.download

    def corrupted_read(conn, who, identifier):
        row = read(conn, who, identifier)
        return {**row, "payload": body + b"tampered"}

    monkeypatch.setattr(repository, "download", corrupted_read)
    response = source_client.get(f"{PREFIX}/{source_id}/download", headers=auth(source_client))
    assert response.status_code == 503
    assert response.json()["code"] == "SOURCE_INTEGRITY_FAILURE"
    assert "application/pdf" not in response.headers["content-type"]
    with connection(trust_dsn) as conn:
        assert (
            conn.execute("SELECT count(*) AS total FROM trust.audit_events").fetchone()["total"]
            == 0
        )


def test_download_audit_failure_releases_no_pdf(source_client, source_archive, monkeypatch):
    source_id, _, _ = source_archive

    def unavailable_audit(*args, **kwargs):
        raise psycopg.OperationalError("synthetic audit outage")

    monkeypatch.setattr(service, "append_event", unavailable_audit)
    response = source_client.get(f"{PREFIX}/{source_id}/download", headers=auth(source_client))
    assert response.status_code == 503
    assert response.json()["code"] == "DATABASE_UNAVAILABLE"
    assert "application/pdf" not in response.headers["content-type"]
    assert "synthetic audit outage" not in response.text


def test_unauthenticated_and_non_reviewers_cannot_access_archives(source_client, source_archive):
    source_id, _, _ = source_archive
    paths = (PREFIX, f"{PREFIX}/{source_id}", f"{PREFIX}/{source_id}/download")
    for path in paths:
        response = source_client.get(path)
        assert response.status_code == 401
        assert response.json()["code"] == "AUTHENTICATION_REQUIRED"
    for role in NON_REVIEWERS:
        headers = auth(source_client, role)
        for path in paths:
            response = source_client.get(path, headers=headers)
            assert response.status_code == 403
            assert response.json()["code"] == "CAPABILITY_DENIED"
            assert "Synthetic review source" not in response.text


def test_source_scope_hides_other_principal_and_unknown_identifiers(source_client, source_archive):
    source_id, _, _ = source_archive
    headers = auth(source_client, principal="ldn")
    listing = source_client.get(PREFIX, headers=headers)
    assert listing.status_code == 200
    assert listing.json()["items"] == []
    assert listing.json()["total"] == 0
    for suffix in ("", "/download"):
        foreign = source_client.get(f"{PREFIX}/{source_id}{suffix}", headers=headers)
        missing = source_client.get(f"{PREFIX}/{uuid4()}{suffix}", headers=headers)
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()


def test_source_pagination_and_identifier_validation_are_bounded(source_client, source_archive):
    headers = auth(source_client)
    for suffix in ("limit=0", "limit=101", "offset=-1", "offset=9223372036854775808"):
        response = source_client.get(f"{PREFIX}?{suffix}", headers=headers)
        assert response.status_code == 422
        assert response.json()["code"] == "INVALID_REQUEST"
    for suffix in ("", "/download"):
        response = source_client.get(f"{PREFIX}/invalid-uuid{suffix}", headers=headers)
        assert response.status_code == 422
        assert "invalid-uuid" not in response.text
    beyond = source_client.get(f"{PREFIX}?limit=1&offset=1", headers=headers)
    assert beyond.status_code == 200
    assert beyond.json() == {"items": [], "total": 1, "limit": 1, "offset": 1}


def test_source_openapi_declares_binary_download_and_explicit_response_schemas(source_client):
    paths = source_client.get("/openapi.json").json()["paths"]
    assert paths[PREFIX]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SourcePage"
    }
    detail = paths[PREFIX + "/{source_id}"]["get"]
    assert detail["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SourceDetail"
    }
    download = paths[PREFIX + "/{source_id}/download"]["get"]
    assert download["responses"]["200"]["content"] == {
        "application/pdf": {"schema": {"type": "string", "format": "binary"}}
    }
    assert download["security"] == [{"HTTPBearer": []}]
