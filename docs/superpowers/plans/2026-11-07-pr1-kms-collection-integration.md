# PR #1 KMS and Collection Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mengintegrasikan KMS, evidence governance, dan kontrol keselamatan PR #1 ke backend collection yang sedang dikerjakan, dengan satu jalur dispatch dan struktur modul sesuai domain.

**Architecture:** KMS menjadi producer di `app/modules/knowledge/`; `knowledge_context` tetap memiliki snapshot/citation per run. Adapter in-process menghubungkan keduanya. Runtime collection, worker, review, dan channel memakai service existing yang diperkuat dengan invariant PR; facade trust tidak menjadi runtime kedua.

**Tech Stack:** Python 3.12+, FastAPI/Pydantic, psycopg, PostgreSQL 17 + pgvector, Alembic, uv, SDK MCP, pytest/Ruff; pertahankan tambahan psycopg pool, PyJWT, dan mypy PR bila dipakai. Frontend tetap Next.js/TypeScript.

**Spec:** [Rencana backend existing](2026-11-07-agentic-collection-skeleton.md), terutama B1/B3/B4/B5/B7; [rencana lintas-repo](../../../../docs/superpowers/plans/2026-11-07-agentic-collection-system-plan.md), terutama batas KM dan M4; [product overview](../../../../00_Product_Overview.md); [PR #1](https://github.com/Meefx/Hackathon-Backend/pull/1) pada `77cc1b4f715d700d994967d221551437ea64c71b`.

**Status:** Implementasi disetujui user pada 7 November 2026. History PR sudah digabungkan pada branch kerja melalui commit `db3021f`. Producer, gateway, migration, gate kanonis dan frontend telah diimplementasikan. Verifikasi akhir dan catatan perubahan berada pada bagian hasil eksekusi di akhir dokumen.

## Global Constraints

- Dua repository terpisah; Git, dependency, dan checks dijalankan dari repo masing-masing.
- Demo memakai data sintetis dan channel simulasi. Implementasi kontak nyata berada di luar batas demo.
- Uang adalah integer Rupiah. Aging tetap memakai kalender `Asia/Jakarta`; contact memakai timezone borrower yang diverifikasi backend.
- Alur dependency: `router -> service -> repository -> db`. Adapter MCP dan REST memakai service domain yang sama.
- Alembic satu-satunya pemilik DDL. Revision yang sudah dibagikan/diterapkan tidak diedit atau dihapus.
- Required knowledge/policy/gate yang gagal memblokir outbound; inbound hardship/dispute/human request tetap diterima kapan pun.
- KMS producer milik kontribusi teman; perubahan public service dilakukan lewat boundary eksplisit, bukan saling mengimpor repository internal.
- Secret tetap di backend. Jangan commit `.env`, token, database, atau data pribadi. Jangan menimpa perubahan lokal yang sudah ada.
- Tidak mengklaim semantic retrieval atau live classifier dari hash embedding dan `MockLayer2` PR.

## Review Focus

1. Retirement bersamaan dengan dispatch: receipt hanya boleh commit sebelum retirement atau ditolak setelahnya; tidak boleh ada celah antara validasi dan commit. Task 5.
2. Identitas/scope tidak tersedia atau role diklaim model: gagal tertutup sebelum retrieval/write/replay. Task 4 dan 6.
3. Critical context melebihi DTO atau hash/version berbeda: tidak dipotong diam-diam; evidence dan provenance harus utuh. Task 4 dan 7.
4. Upgrade dari salah satu branch dan database berisi portfolio: satu Alembic head, data/history tetap utuh, test tidak menyentuh DB demo. Task 2.
5. Waktu/token berubah saat menunggu lock, template disuspend, atau borrower memiliki dua account: gate mengecek ulang waktu/state dan budget borrower setelah lock. Task 5.

## Temuan review dan posisi terhadap current plan

PR berbasis `b517c53`, sama dengan commit HEAD lokal. Namun branch lokal `feat/agentic-collection-system` mempunyai banyak implementasi yang belum di-commit di kedua repo. Status awal dan checkbox pada plan existing belum mencerminkan seluruh kode tersebut; jangan memulai B1–B7 dari nol atau menganggap semuanya sudah terverifikasi.

GitHub menyatakan PR mergeable terhadap `main`. Sembilan file tracked berubah baik lokal maupun di PR: `.env.example`, `AGENTS.md`, `README.md`, `app/config.py`, `app/db/connection.py`, `app/main.py`, `compose.yaml`, `pyproject.toml`, dan `uv.lock`. Ini overlap yang perlu resolusi manual, bukan bukti sembilan konflik tekstual Git.

| Area | Backend lokal | PR #1 | Keputusan integrasi |
| --- | --- | --- | --- |
| KM | Gateway fixture/unavailable, snapshot dan citation per run | Ingestion, versioning, scoped retrieval, QA/approval, retirement, template dependencies | Isi B4/M4 dengan producer PR melalui gateway existing |
| Runtime | Account, manager session, durable run/worker, draft, review | UUID trust case, outreach pipeline, conversation sendiri | Runtime collection existing menjadi pemilik operasional |
| Compliance | Gate/attestation dan simulated dispatch tersedia; aturan masih lebih sederhana | Kalender, timezone, freshness, recipient, daily/weekly borrower budget, exact-text gate | Pindahkan kontrol PR ke pemilik compliance/dispatch existing, pertahankan aturan yang lebih ketat |
| Evidence | Maksimal 5 sumber, excerpt 4.000 karakter | Critical context hingga 64 chunks / 24.000 karakter | Tambahkan evidence context bounded; jangan buang pengecualian sumber |
| Audit | Hash chain per account | Hash chain global trust | Pemilik event dibatasi per domain dan ditautkan; tidak ada dua kebenaran dispatch |
| Migration | `0001_accounts -> 0002_collection_state` | `0001_accounts -> 0002_trust` | Pertahankan dua revision, tambahkan merge revision dan revision integrasi |
| Test isolation | Schema acak di dedicated `_test` database | Database acak karena nama `trust.*` tetap | Database disposable per test untuk suite gabungan |

### Koneksi milestone

| Current plan | Pekerjaan tambahan di sini | Dampak |
| --- | --- | --- |
| B1 / M1 | Merge migration, pgvector, schema integrasi, test isolation | Melengkapi persistence existing tanpa mengganti portfolio |
| B3 / M2 | Satukan aturan PR, template binding, race-safe dispatch, audit ownership | Memperkuat jalur demo yang sudah ada |
| B4 / M4 | Public producer service, adapter, scope, mapping evidence dan revocation | Menggantikan fixture secara eksplisit saat mode `in_process` dipilih |
| B5 / M4 | Retrieval/candidate tetap lewat service dan izin MCP existing | Menjaga parity REST/MCP; agent tidak mendapat approval/dispatch privilege |
| B6 / M5 | Konsumsi evidence nyata dan pelabelan dependency | Hermes/classifier live tetap acceptance terpisah; PR tidak menyelesaikannya |
| B7 / M6 dan frontend F4 | Full journey, critical evidence UI, reload/pagination, runbook | Membuktikan backend gabungan dan consumer aktual |

### Pilihan pendekatan

Rekomendasi: integrasi bertahap sambil mempertahankan history PR, kemudian konsolidasi domain sebelum dipakai oleh backend utama. Direct merge lalu mengaktifkan dua runtime menambah dua state machine, gate, budget, dan audit dispatch. Mengambil retrieval saja juga tidak cukup: retirement, template governance, critical context, serta kontrol kontak PR harus tetap menjadi acceptance criteria.

## Struktur akhir dan tanggung jawab

```text
app/modules/knowledge/
  router.py, schemas.py          # Dokumen/search/template HTTP dan kontrak producer
  service.py, repository.py      # Lifecycle dokumen, QA/approval/retirement dan SQL
  retrieval.py                  # Scoped retrieval, ranking, critical expansion
  processing.py                 # Chunking dan hash projection nonsemantic
  templates.py                  # Approved template dan dependency governance
  audit.py                      # Event lifecycle KMS; mempertahankan chain trust existing
  authorization.py              # Actor/capability producer; tidak membaca klaim model
app/modules/knowledge_context/
  contracts.py, service.py       # Gateway DTO, immutable evidence dan citation per run
  repository.py                 # SQL snapshot dan bounded history
app/integrations/knowledge/
  gateway.py                    # Pemilihan mode fixture/in_process/unavailable
  trust_adapter.py              # Mapping producer ke gateway, termasuk transactional guard
  scope.py                      # Resolve caller/account ke principal/product/role terverifikasi
app/modules/compliance/
  service.py, repository.py      # Policy, recipient, kalender, freshness, budget
  screening.py, rendering.py     # Aturan deterministik PR dan fakta server
app/modules/conversations/
  service.py, repository.py      # Draft, template/evidence binding, pesan dan persistence
  dispatch.py                   # Satu gate + satu entry point ke ChannelGateway
app/modules/audit/              # Event run/contact/review per account dan verifikasi
app/modules/trust/              # Facade kompatibilitas tipis, tanpa aturan/SQL runtime kedua
app/shared/                    # Caller, clock dan error lintas-domain yang sudah ada
```

Ekstraksi hanya untuk area yang disentuh. Jangan membuat file kosong atau merapikan seluruh modul manager/runs/reviews di luar kebutuhan integrasi. `app/trust/` dan `app/services/dispatch_gate.py` boleh menjadi shim transisi selama pemindahan, tetapi tidak boleh menyisakan implementasi dispatch aktif kedua. Console/CLI standalone menjadi demo tooling dan memakai service kanonis; portal utama tetap Next.js.

### Kontrak dan keputusan yang dikunci

- `KnowledgeGateway.query_approved(KnowledgeQuery, caller) -> KnowledgeResult` dan `validate_references(list[KnowledgeSource], at, caller) -> ReferenceValidation` tetap tersedia.
- Factory zero-argument `app.integrations.knowledge.trust_adapter:create_gateway` mengembalikan gateway melalui konfigurasi `KNOWLEDGE_SERVICE_FACTORY` existing. Factory/import tidak membuka pool atau koneksi; query memakai connection bounded yang ditutup pada akhir operasi, sedangkan transactional guard memakai connection milik dispatch. Bila service admin memakai pool, lifecycle tetap dimiliki app startup/shutdown dan tidak diasumsikan tersedia di proses worker/MCP.
- Tambahkan capability transaksi: `lock_references(connection: Connection, references: list[KnowledgeSource], caller: Caller) -> LockedKnowledgeReferences`; objek hasil menyediakan `validate(at: datetime) -> ReferenceValidation`. Implementasi melalui public service producer, bukan SQL producer di modul consumer. Lock hidup sampai transaksi receipt selesai. In-process wajib memakai database PostgreSQL yang sama; konfigurasi DB KMS terpisah ditolak untuk dispatch atomik.
- Mode `in_process` tanpa capability transaksi memblokir dispatch dengan `knowledge_atomic_validation_unavailable`; tidak jatuh ke fixture. Fixture immutable menyediakan guard lokal tersendiri.
- `KnowledgeSource.content_hash` existing tetap `digest(excerpt)` (SHA-256 canonical JSON string). Tambahkan optional `document_hash` dan `snippet_hash` untuk SHA-256 raw UTF-8 producer; jangan menyamakan ketiganya. Citation ID versi baru terikat document ID/version, snippet ID, dan snippet hash.
- Tambahkan `critical_context` dan `verification_profile` optional/default ke `KnowledgeResult`/`KnowledgeSnapshot`. Context memuat document/version, chunk ID, locator, text dan chunk hash, maksimal 64 chunks dan total 24.000 karakter. Primary `sources` tetap maksimal 5, excerpt maksimal 4.000. Over-budget menghasilkan blocked reason `critical_context_requires_review`; tidak truncation otomatis.
- Snapshot baru memakai `snapshot_hash_version=2` dan `digest({"version": 2, "sources": ..., "critical_context": ..., "verification_profile": ...})`. Snapshot lama tetap versi 1 dengan `digest(sources)`. Approval mengikat versi/hash dan himpunan snapshot/citation yang benar; alteration critical context/profile harus membatalkan integritas. Metadata versi baru additive dengan default 1 untuk consumer/history lama.
- `approved_at`, effective dates, versi dan hash berasal dari record producer, bukan timestamp/rekaan adapter. Semua timestamp timezone-aware. Query deadline benar-benar membatasi kerja database; waktu dibaca ulang sesudah lock.
- URI `synthetic://` disimpan sebagai provenance dengan scheme yang diizinkan secara eksplisit, tetapi tidak menjadi hyperlink aktif. HTTP(S) tetap satu-satunya link yang dapat diklik UI. Jangan mengubah URI menjadi URL palsu.
- Caller existing belum membawa principal/product/role producer. Resolve dari konfigurasi/record backend yang terikat account dan assignment. Default mapping demo dibuat oleh seed eksplisit untuk account sintetis; account tanpa mapping ditolak. Role `collection/improvement/copilot` membutuhkan mapping capability yang terdokumentasi; tidak ada alias ke admin/compliance.
- Draft dengan `template_id` harus menyimpan ID/hash/dependency binding template approved dan dicek ulang saat dispatch. ID ini saat ini diterima `DraftCandidate` tetapi belum dipakai. Draft bebas template tetap memakai snapshot evidence dan guardrail yang sama.
- Render placeholder PR `{{amount_due}}` dan kontrak existing `{outstanding_amount}` lewat mapping renderer eksplisit. Jangan mengedit teks artifact approved agar hash terlihat cocok; hash template input dan pesan final disimpan terpisah.
- Audit global KMS memiliki lifecycle dokumen/template/retrieval; audit per-account memiliki run/contact/review/dispatch. Tautkan event/correlation/source IDs secara eksplisit, verifikasi kedua chain, dan jangan rewrite history. Tidak mengklaim satu total ordering lintas kedua chain.
- Namespace HTTP portfolio/collection existing dan enum `simulated_sent` dipertahankan. Trust KMS API tetap dapat memakai `/api/v1/trust/knowledge` dan `/templates` sebagai facade. Route legacy trust case/outreach/conversation hanya aktif bila ada mapping account eksplisit dan memanggil service kanonis; tanpa mapping ditolak dengan kode stabil `legacy_case_mapping_required`. Jangan otomatis menyalin identitas/fakta UUID case menjadi authority portfolio.
- Cutover database trust yang sudah berisi data mempertahankan legacy attempts/dispatch/conversation sebagai history read-only. Mapping menyimpan legacy case/borrower/dispatch/request ID stabil; successful legacy attempts tidak dikirim ulang. Budget borrower membaca gabungan history dengan deduplikasi ID/link, bukan mulai dari nol. Hold/dispute/escalation legacy harus diproyeksikan ke pause/review kanonis sebelum case dapat diaktifkan. Case/history yang belum terpetakan tetap diblokir; mapping tidak otomatis membuktikan recipient atau mandate.
- Raw conversation tetap terpisah dari KMS dan dibatasi ke operator yang diberi akses/officer/compliance; principal agent menerima redacted context. Pertahankan sanitizer dan capability test PR ketika menghubungkan history MCP; jangan memperluas akses raw karena caller berada dalam proses yang sama.

## Task 1 — Amankan baseline dan tetapkan contract delta

**Files:** Baca seluruh status Git; update dokumen plan existing hanya untuk status/evidence dan tautan addendum ini. Tambahkan `docs/integrations/knowledge-service.md` saat implementasi kontrak dimulai.

**Interfaces:** Consumes branch lokal dan PR pinned di atas. Produces baseline yang dapat direstorasi serta daftar perubahan DTO/endpoint/izin yang disepakati.

- [ ] Catat `git status`, HEAD dan diff kedua repo. Simpan checkpoint hanya source yang sudah direview, termasuk file aplikasi untracked; jangan `git add .`, stash/reset, atau commit secret. Gunakan checkout aktif bila masih sesuai dan tidak ada pekerjaan lain yang berjalan di file sama.
- [ ] Jalankan baseline Ruff/pytest backend dan checks frontend yang relevan sebelum merge; simpan hasil terpisah dari hasil PR. Baseline gagal dicatat dan diperbaiki dalam task pemiliknya, tidak dinyatakan sebagai regresi PR tanpa bukti.
- [ ] Kunci kontrak di atas, scope demo, mapping role/purpose, legacy route behavior, template provenance, dan domain audit. Buat tests acceptance bernama pada task berikut sebelum mengubah perilaku.
- [ ] Rekonsiliasi status B1–B7/M4 dari kode + hasil checks aktual; bedakan implemented, verified, dan pending. Commit checkpoint/dokumen secara terpilih.

## Task 2 — Gabungkan history, infrastruktur dan migration

**Files:** `.env.example`, `AGENTS.md`, `README.md`, `app/config.py`, `app/db/connection.py`, `app/main.py`, `compose.yaml`, `.github/workflows/checks.yml`, `pyproject.toml`, `uv.lock`, `migrations/env.py`, `tests/conftest.py`; create `migrations/versions/0004_collection_trust_merge.py`, `tests/migrations/test_collection_trust_merge.py`.

**Interfaces:** Produces satu migration head `0004_collection_trust_merge` dengan parents `("0003_review_holds", "0002_trust")`, shared Settings/connect/pool, dan disposable migrated database fixture. Revision integrasi task berikut akan melanjutkan head ini.

- [ ] Setelah baseline aman, gabungkan commit PR pada branch integrasi dengan history penulis tetap tersimpan. Resolve sembilan file overlap secara manual; pertahankan worker/Hermes/MCP dan timeout configurable lokal serta KMS/pool/auth PR. Regenerate lockfile dari union dependency, jangan memilih salah satu `uv.lock`.
- [ ] Tambah tests `test_upgrade_from_collection_head_preserves_data`, `test_upgrade_from_trust_head_preserves_data`, dan `test_empty_database_reaches_single_head`. Assert field portfolio, run/snapshot/review, approved KMS/hash-chain tetap identik setelah upgrade.
- [ ] Pertahankan kedua `0002` beserta frozen SQL PR tanpa mengubah parent. Tambahkan merge revision kosong; update readiness untuk revision baru dan schema collection + trust/vector. Startup/seed tetap tanpa DDL; `/health` tetap hidup saat KMS unavailable.
- [ ] Gunakan image `pgvector/pgvector:pg17` untuk dev/test/CI. Pindahkan fixture gabungan ke database acak per test pada base `_test` dengan CREATEDB; hapus hanya database yang fixture buat. Pastikan unit tests tidak ikut meminta DB.
- [ ] Jalankan `uv sync --locked`, `uv run alembic heads`, migration tests dan suite baseline. Expected satu head dan preservation/rollback lulus. Dokumentasikan kebutuhan pgvector, privilege test, backup/restore; jangan migrate database demo pada tahap review. Commit hasil task.

## Task 3 — Rapikan producer KMS tanpa mengubah invariant

**Files:** Ekstrak `app/trust/knowledge.py`, bagian knowledge `processing.py`, `security.py`, `audit.py` dan bagian knowledge/template `app/modules/trust/{router,service,schemas}.py` ke struktur `app/modules/knowledge/` di atas; sanitizer conversation menuju `app/modules/conversations/redaction.py`. Pertahankan shim kompatibilitas sementara bila diperlukan. Move tests producer ke `tests/unit/modules/knowledge/` dan `tests/integration/modules/knowledge/`.

**Interfaces:** Public service producer menyediakan lifecycle document/template, scoped search, lookup metadata approved, dan lock+validation evidence memakai connection milik caller. Transport tidak mengakses repository langsung. Actor native producer tetap tervalidasi.

- [ ] Jalankan characterization tests PR untuk independent QA/human approval, immutable source/chunk, scoped retrieval, bounded pagination, critical expansion, dan retirement yang mensuspend dependency ber-weight rendah sekalipun.
- [ ] Pisahkan lifecycle, retrieval, templates, processing, authorization dan SQL sesuai tanggung jawab. Service menentukan keputusan dan satu pemilik transaksi; repository menerima connection, tanpa commit sendiri.
- [ ] Pertahankan behavior, raw hashes, approval binding, global audit lock dan urutan lock selama ekstraksi. Tidak memasukkan conversation/PII ke knowledge corpus dan tidak menjadikan retrieved text executable policy.
- [ ] Jalankan seluruh tests knowledge/security/processing PR dan Ruff. Expected seluruh invariant tetap lulus. Perbarui import/tests secara nyata, tanpa melemahkan assertion atau menambah test yang hanya mengecek nama folder. Commit hasil task.

## Task 4 — Hubungkan gateway, scope, dan evidence persistence

**Files:** `app/integrations/knowledge/{gateway,trust_adapter,scope}.py`, `app/modules/knowledge_context/{contracts,service,repository}.py`, public metadata/validation service producer, `app/modules/integrations/service.py`, `app/workflows/collection.py`; create `migrations/versions/0005_knowledge_collection_integration.py`, `tests/integration/test_knowledge_contract.py`; update seed di helper domain bila membesar.

**Interfaces:** Implements `KnowledgeGateway` dan transactional extension di atas. Migration `0005` menambah account-to-corpus/borrower authority mapping, legacy identity/history links, critical context/profile/hash-version snapshot, dan template/evidence binding message. Columns tambahan memakai explicit INSERT lists; history existing tidak dihapus atau diberi approval baru.

- [ ] Tulis tests `test_scope_comes_from_backend_assignment`, `test_missing_scope_denies`, `test_approved_metadata_and_three_hashes_map_exactly`, `test_versioned_citations_do_not_collide`, `test_synthetic_uri_is_nonclickable_provenance`, `test_critical_context_is_preserved_or_blocked`, `test_critical_context_tampering_changes_v2_hash`, `test_deadline_and_naive_timestamp_fail_closed`. Assert top_k 1–5, approved/effective source saja, <=64 chunks/24.000 chars, snapshot immutable sesudah retirement dan snapshot/hash v1 tetap terbaca.
- [ ] Migration mapping account menyimpan principal/product/borrower UUID dan authority/contact metadata yang diperlukan task 5; seed hanya untuk account sintetis eksplisit. Caller/model tidak boleh memilih corpus/role/mandate lewat argument. Missing binding menjadi denied, bukan default privileged identity.
- [ ] Implementasikan mapping retrieval dengan metadata source yang benar, additional critical context/profile, tiga hash terpisah, deterministic versioned citation ID, deadline database dan error/reason mapping. Reference validation memeriksa document/version/chunk/hash/access/approval/effectivity, bukan document ID saja.
- [ ] Simpan additive evidence/profile dan template binding melalui migration; perbaiki citation lookup agar menghasilkan binding tepat ke snapshot(s) yang benar, bukan selalu hash snapshot terbaru ketika citation berasal dari retrieval lama. Persist binding untuk legacy approval secara kompatibel atau tandai membutuhkan approval ulang; jangan rewrite audit/snapshot lama.
- [ ] Pengisian mapping legacy dilakukan sebagai operasi data eksplisit, idempotent dan teruji sesudah schema migration, bukan auto-import startup. Preserve old idempotency/dispatch IDs dan transfer pause/review sebelum activation. Uji `test_cutover_counts_legacy_contacts_once`, `test_legacy_delivered_attempt_never_resends` dan `test_cutover_preserves_holds`; case unmapped tetap denied.
- [ ] Test `demo_fixture` dan `in_process` terpisah; tidak ada automatic fallback. Registry hanya menyatakan healthy sesudah probe aktual, dengan reason tersanitasi. Jalankan `uv run pytest tests/integration/test_knowledge_contract.py tests/migrations -q` dan commit.

## Task 5 — Satukan guardrail, template dan dispatch atomik

**Files:** `app/modules/compliance/{service,repository,screening,rendering}.py`, `app/modules/conversations/{service,repository,dispatch}.py`, native producer lock/validate service, `app/integrations/channels/`, policy/authority fixture; tests `tests/integration/test_collection_safety.py`, `tests/integration/test_knowledge_dispatch.py` dan invariant PR `test_safety.py`, `test_trust_pipeline.py`, `test_dispatch_authorization.py`.

**Interfaces:** `dispatch.simulate_dispatch(settings, message_id, request, caller, clock) -> DispatchResult` menjadi entry point kanonis; facade lama dapat mempertahankan signature dengan adapter clock. Semua pemanggil mendapatkan fresh clock setelah blocking locks. `ChannelGateway` hanya menerima `AuthorizedIntent` dari gate ini.

- [ ] Tulis regressions `test_retirement_racing_dispatch_has_one_authoritative_order`, `test_template_suspension_blocks_prior_draft`, `test_clock_after_lock_blocks_expired_or_outside_window`, `test_two_accounts_share_borrower_budget`, `test_atomic_guard_unavailable_blocks`, dan `test_audit_failure_rolls_back_receipt`. Assert concurrent retry <=1 receipt, exact text/version binding, serta authority diperiksa sebelum replay.
- [ ] Terapkan satu urutan lock lintas writer: global KMS/audit -> document ID terurut -> template -> account/case -> borrower budget -> message/request -> account audit. Initial read untuk menemukan referensi tidak dipercaya sebagai final authority. Setelah lock, baca ulang message/snapshot/template binding; perubahan discovery membatalkan/retry bounded tanpa mengirim.
- [ ] Panggil `lock_references` pada connection receipt yang sama sebelum account locks. Sesudah semua authority locks, ambil clock baru dan validasi reference, approval expiry, policy/recipient/pause/frequency/template sekali lagi. Jangan memanggil provider/jaringan atau membuka transaksi KMS terpisah di dalam transaksi dispatch. Retirement memakai ordering yang sama.
- [ ] Bawa aturan PR ke policy kanonis: Mon–Sat, non-holiday termasuk collective leave, borrower timezone verified, 08:00 <= time < 20:00, calendar coverage, borrower-owned verified recipient/opt-out, fakta tidak future dan maksimal 24 jam, daily cap dan maksimal 3 proactive contacts dalam rolling 168 jam. Pertahankan cap existing bila lebih ketat; budget dihitung per borrower lintas account. Missing authority/calendar memblokir. Test boundary dalam clock simulasi, tanpa mengarang kalender legal produksi.
- [ ] Satukan deterministic rendering/screening PR dengan attestation existing. Template-backed candidate terikat template approved + dependency/hash, exact rendered text dan source snapshot. Candidate tanpa template tetap lewat seluruh gate. Live model tetap blocked bila classifier live belum tersedia; `MockLayer2` tidak dihitung live acceptance.
- [ ] Route semua simulated delivery ke gate kanonis; adapt invariant tests PR ke entry point ini sambil mempertahankan assertions. Hilangkan jalur channel aktif dari legacy gate. Jalankan suite safety, revocation/concurrency dan rollback; commit sesudah lulus.

## Task 6 — Hubungkan facade REST/MCP dan audit berdasarkan pemilik domain

**Files:** `app/modules/trust/`, `app/modules/collection/{router,service}.py` pada endpoint yang disentuh, `app/modules/conversations/redaction.py`, `app/modules/audit/`, `app/modules/knowledge/audit.py`, `app/main.py`, `app/mcp/{schemas/collection,tools/knowledge,tools/accounts}.py`, standalone launcher/demo; `tests/test_trust_api_contract.py`, `tests/mcp/test_service_parity.py`, `tests/mcp/test_protocol.py`.

**Interfaces:** Portfolio/collection REST existing tetap kompatibel. Trust KMS routes memanggil producer service. Trust runtime compatibility memanggil canonical collection services hanya dengan mapping account sah. MCP tetap tidak mengekspos approval/dispatch kepada principal agent.

- [ ] Test facade mengembalikan kontrak/error yang didokumentasikan; tanpa account mapping, legacy case/outreach route mengembalikan `legacy_case_mapping_required` dan zero receipt/state operasional baru. Dengan mapping sintetis sah, dispatch memakai gate/receipt/budget collection yang sama.
- [ ] Pisahkan route/service producer dari runtime. Hilangkan SQL snapshot dari router yang disentuh; facade hanya memanggil public services. Mount auth/error/size middleware tanpa mengubah kontrak portfolio atau menelan handler domain existing.
- [ ] Pertahankan chain global KMS dan chain account untuk event yang berbeda. Tambah correlation/source event references untuk navigasi provenance; dispatch tidak ditulis sebagai sukses di dua operational ledger. Test integrity masing-masing chain dan rollback lintas-domain yang diperlukan pada connection sama.
- [ ] Perbarui MCP DTO/outputSchema, tool description dan roundtrip structured output agar critical context, profile dan hash-version tidak dibuang saat Pydantic serialization. Test `test_critical_context_survives_protocol_roundtrip` dan sanitizer/permission history: agent tidak dapat membaca raw conversation atau mengubah izin melalui retrieved text. Label step workflow mengikuti `source_mode` aktual, bukan selalu menyebut demo fixture.
- [ ] Uji izin sebelum replay, unknown scope/role, lintas account/corpus, SDK initialize/discovery/call dan parity REST/MCP. Penolakan knowledge tidak mematikan portfolio/inbound review. Console/demo memakai service kanonis dan tidak mendaftarkan jalur bypass. Commit hasil task.

## Task 7 — Sinkronkan consumer frontend dan evidence browser

**Files (frontend):** `src/features/knowledge/types.ts`, `api.ts`, `components/knowledge-evidence.tsx`, consumer run/integrations terkait, `tests/e2e/behavior.spec.ts`, `tests/smoke/backend-journey.spec.ts`; tambah `tests/smoke/knowledge-module-journey.spec.ts` bila perlu suite tersendiri.

**Interfaces:** Endpoint paginated `/api/v1/runs/{id}/knowledge` dan status/enum existing dipertahankan. DTO frontend menerima additive critical context/profile/raw provenance hashes; backend tetap pemilik keputusan.

- [ ] Tambah browser cases `knowledge_module` dengan nullable metadata, critical context/profile, source `synthetic://` non-clickable, unavailable/revoked reason dan snapshot yang tetap sama setelah reload.
- [ ] Perbaiki citation resolution lintas halaman snapshot: citation yang belum termuat tidak ditampilkan sebagai invalid; user dapat memuat halaman sumber yang tepat. Uji citation pada halaman >20 snapshots. Hindari mengambil seluruh history tanpa batas.
- [ ] Render bounded critical context dan actual retrieval profile tanpa mengklaim semantic/live verification. Jangan membuat screen ingestion/admin baru atau endpoint Next.js yang menduplikasi backend.
- [ ] Pertahankan smoke `demo_fixture` existing sebagai skenario tersendiri. Tambah live in-process KM smoke terhadap backend/worker/DB nyata: approved source -> draft -> retirement -> dispatch blocked. Jalankan `npm run typecheck`, `npm run build`, `npm run test:e2e` dan smoke sesuai konfigurasi repo; commit frontend terpisah.

## Task 8 — Acceptance gabungan dan persiapan merge

**Files:** README/AGENTS/env example kedua repo sesuai dampak, `docs/integrations/knowledge-service.md`, plan existing untuk status/evidence, CI/runbook dan PR description integrasi.

**Interfaces:** Produces branch integrasi yang dapat direview, evidence checks lokal/CI, satu migration head dan daftar batas eksternal yang masih pending.

- [ ] Jalankan dari backend: `uv sync --locked`, `uv run ruff check .`, `uv run mypy app`, `uv run alembic heads`, lalu full `uv run pytest` dengan `TEST_DATABASE_URL` ke dedicated `_test` PostgreSQL + pgvector. Jangan menyembunyikan error mypy akibat union kode; perbaiki typing yang diperlukan.
- [ ] Jalankan full REST + MCP journey menggunakan KMS actual in-process: ingest -> QA independen -> approval -> scoped retrieval -> snapshot -> approved-template/freeform candidate -> gate -> simulated receipt -> retirement -> next dispatch blocked. Sertakan concurrent retirement/retry, rollback audit, reload dan worker restart. Semua tetap sintetis.
- [ ] Verifikasi migration dari kedua `0002` ke `0005`, schema kosong, serta preservation data/hash existing. Restore/upgrade diuji pada disposable copy; tidak ada downgrade/destructive cleanup database berisi data.
- [ ] Dokumentasikan enabled/configured/healthy secara jujur, migration/seed/worker/MCP startup dan independent secrets. Perbarui plan existing dengan evidence; Hermes aktual, classifier live, semantic embeddings, 50-persona evaluation dan real provider tidak otomatis dianggap selesai oleh PR ini.
- [ ] Periksa final diff, status kedua repo dan secret/conflict-marker checks. Review branch gabungan sebelum merge ke `main`; hasil CI PR lama bukan bukti untuk branch gabungan. Ringkas perubahan API trust dan frontend yang benar-benar terjadi.

## Bukti review awal dan batas verifikasi saat perencanaan

- Git status kedua repo, README/AGENTS, plan, implementation serta tests relevan dibaca. PR diunduh sebagai snapshot sementara; checkout lokal tidak diganti.
- GitHub PR #1 head `77cc1b4` dilaporkan mergeable terhadap `main`.
- [CI run 42341632995](https://github.com/Meefx/Hackathon-Backend/actions/runs/42341632995): Ruff `All checks passed`, mypy `no issues found in 40 source files`, pytest **358 passed, 1 warning in 18.95s**. Ini hasil branch PR, bukan hasil gabungan dengan worktree lokal.
- Static inspection migration graph gabungan menghasilkan dua head: `0002_collection_state` dan `0002_trust`; belum menjalankan upgrade database gabungan.
- `git diff --check` seluruh worktree menemukan trailing whitespace existing pada `compose.yaml:25`; file aplikasi tersebut tidak diubah pada review ini. Pemeriksaan whitespace dokumen baru dilakukan terpisah.
- Belum menjalankan ulang suite aplikasi lokal, browser, migration, atau integration journey pada turn perencanaan ini. Pemeriksaan dokumen cukup path/kontrak/konsistensi; tests aplikasi dijadwalkan saat implementasi.
- Postman CLI tersedia, tetapi `postman status` mengembalikan `unknown command 'status'`. Tidak membuat workspace/collection baru untuk review source ini; FastAPI/Pydantic dan suite existing tetap kontrak yang dijalankan.

## Hasil yang diminta untuk review user

User menyetujui integrasi producer KMS PR dan runtime collection kanonis pada turn berikutnya. Implementasi berjalan pada branch kerja dengan verifikasi yang dicatat pada hasil eksekusi.


## Hasil eksekusi 7 November 2026

- History PR77cc1b4 digabungkan melalui db3021f, kemudian producer/gateway, gate kanonis dan consumer frontend dikonsolidasikan.
- Migration head `0005_knowledge_collection` menggabungkan `0002_trust` dan `0003_review_holds` melalui merge revision0004. Test preservation mencakup portfolio, collection run/snapshot v1 serta approved source/chunk dan hash-chain KMS.
- Suite backend gabungan lulus501tests dengan satu deprecation warning Starlette/httpx sebelum pemeriksaan tambahan lifecycle opsional dan serialisasi snapshot lama. Perubahan terakhir diverifikasi dengan suite terarah API, migration dan protokol.
- Ruff dan mypy seluruh111sourcefiles lulus. Frontend typecheck, production build terisolasi dan42browserfixturetests lulus.
- Browser journey KMS aktual lulus dengan PostgreSQLpgvector disposable, API8010, worker dan frontendproduction3110. Approved critical source masuk snapshotv2, reload/retirement mempertahankan snapshot, dispatch ditolak dengan knowledge_reference_invalid.
- Reviewer memeriksa producer, transaksi gate, frontend dan integrasi akhir. Perbaikan mencakup deadline perSQL, importcycle, mapping-before-replay, resume hold, cachecitation saat versi berubah, lifecycle KMS opsional dan kompatibilitas snapshotv1.
- Driver PostgreSQL memakai minimum connect timeout dua detik. Deadline lebih pendek dapat terlampaui selama pembentukan koneksi sampai batas minimum itu. Deadline statement/lock dan pemeriksaan sebelum commit memakai sisa budget.
- Tests dan browser acceptance memakai data sintetis terisolasi. Konfigurasi in-process diaktifkan melalui factory pada runbook. Acceptance semantic retrieval, classifier live dan Hermes mengikuti milestone tersendiri.

Runbook operasional berada di [knowledge-service.md](../../integrations/knowledge-service.md).
