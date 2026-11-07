# Backend Agentic Collection Skeleton Implementation Plan

> **For agentic workers** — Gunakan skill `superpowers:executing-plans` untuk menjalankan rencana per tugas. Langkah memakai checkbox. Implementasi fitur dimulai setelah rencana ditinjau.

**Goal** — Menyediakan backend PostgreSQL untuk portfolio CollectFlow, simulasi collection, percakapan, keputusan compliance, antrean review, audit, dan akses MCP.

**Architecture** — REST dan MCP menjadi adapter menuju service domain yang sama. Service menegakkan aturan dan memiliki transaksi PostgreSQL. Simulator lokal menghasilkan alur demo yang dapat diuji sebelum integrasi Hermes.

**Tech Stack** — Python 3.12+, FastAPI, Pydantic, uv, psycopg, PostgreSQL, Alembic dengan SQLAlchemy untuk migration, pytest, Ruff, dan Docker Compose. SDK MCP ditambahkan pada tugas MCP.

**Spec** — Brief dan kontrak dalam dokumen ini menjadi spesifikasi skeleton yang ikut repository. Baca `AGENTS.md` dan `README.md`. Bila workspace lengkap tersedia, `../00_Product_Overview.md` menjelaskan arah produk. Rencana frontend berada di repository `Hackathon-Frontend` pada path dokumen yang sama.

## Brief

Operator memilih account dan memberi instruksi kepada manager. Backend menjalankan simulasi manager, knowledge, collection, dan QA dengan langkah yang tercatat. Operator dapat membaca draft, keputusan compliance, percakapan, dan audit, lalu meminta pengiriman simulasi. Hardship, dispute, dan human request memindahkan kasus ke review manusia dan menghentikan contact otomatis.

Registry memperlihatkan enam role, yaitu `manager`, `collection`, `knowledge`, `qa`, `improvement`, dan `copilot`. Simulator memakai empat role pertama. Dua role lainnya ditampilkan sebagai belum tersedia. Setiap respons menyatakan mode runtime aktual. Hermes menjadi milestone integrasi tersendiri setelah MCP berfungsi.

Portfolio dan dashboard existing tetap berfungsi. Nama aplikasi tetap CollectFlow. Data borrower sintetis dan channel yang tersedia adalah `simulated_chat`. UI/frontend hanya mengonsumsi REST. Agent mengakses service yang sama melalui MCP.

## Status dan urutan kerja

| Bagian | Status | Pemilik |
| --- | --- | --- |
| Fondasi PostgreSQL, Compose, migration baseline, import data lama | Selesai dan terverifikasi, 27 test PostgreSQL lulus, Compose serta host startup lulus | Backend database |
| Schema collection dan service domain | Direncanakan | Backend |
| REST collection | Direncanakan | Backend |
| MCP lokal dan koneksi Hermes | Direncanakan | Backend/integrasi agent |
| Layar agent dan collection | Lihat rencana frontend | Frontend |

Urutan dependency adalah fondasi database → schema collection → service dan REST → MCP → verifikasi lintas-repo. Fitur UI dapat dikerjakan dari kontrak usulan setelah backend menyepakati schema, kemudian diverifikasi terhadap OpenAPI aktual.

## Global Constraints

- PostgreSQL menjadi database runtime sesuai keputusan user. SQLite lama hanya menjadi sumber import.
- Pertahankan field account, enum aging `current|early|late|critical`, pagination `{items,total,limit,offset}`, limit 1–100, offset nonnegatif, dan urutan account `due_date,id`.
- Dashboard menghitung seluruh portfolio. Nominal berupa integer Rupiah nonnegatif. Due date berupa tanggal kalender. Aging dihitung di backend dengan `Asia/Jakarta`.
- Event memakai timestamp UTC timezone-aware. Clock dapat diinjeksi agar pengujian batas waktu deterministik.
- Backend memeriksa eligibility, waktu contact, verified recipient, guardrail, dan dispatch gate sebelum generation serta saat dispatch. Data policy wajib yang hilang memblokir operasi.
- Case handling state terpisah dari aging. Hardship, dispute, dan human request membuat review dan pause contact secara atomik.
- REST dan MCP memakai izin server, transaksi, dan idempotency service yang sama. Role atau `approved=true` dari payload tidak memberi izin.
- Demo memakai loopback, borrower sintetis, dan channel simulasi. Secret berada pada environment backend. Log hanya memuat ID, correlation ID, hasil, dan durasi yang aman.
- Migration memiliki DDL. Seed eksplisit dan idempotent. Data lama dipertahankan melalui import tervalidasi, lalu schema berikutnya memakai migration Alembic.
- `main.py` merangkai aplikasi. Fondasi database memindahkan query accounts ke repository dan aging ke service, memperbarui seluruh consumer, lalu menghapus `app/store.py`.

## Review Focus

1. Import database lama menjaga seluruh ID, balance, due date, ownership, dan constraint. Tugas B0 menguji preservation dan rollback.
2. Retry dan dua dispatch bersamaan menghasilkan satu pengiriman simulasi. Tugas B2 menguji key sama, key berbeda, dan konflik payload.
3. Account, policy, isi draft, atau approval berubah setelah generation. Tugas B2 menguji revalidasi dan expiry.
4. Database gagal di tengah escalation atau dispatch. Tugas B2 menguji rollback case, review, message, dan audit secara bersama.
5. Discovery MCP sesuai schema runtime dan izin eksekusi. Tugas B4 menguji handshake, batas hasil, akses ditolak, timeout, dan cancellation.

## Kontrak lintas-repo

Kontrak berikut adalah usulan implementasi. Pydantic dan `/openapi.json` menjadi sumber kontrak setelah tersedia. Perubahan harus diperbarui pada typed client frontend sebelum integrasi.

| Operasi | HTTP dan hasil |
| --- | --- |
| Registry agent | `GET /api/v1/agents` → `{items: AgentDefinition[]}` |
| Memulai simulasi | `POST /api/v1/accounts/{account_id}/runs` → `RunResult` |
| Membaca run | `GET /api/v1/runs/{run_id}` → `RunResult` |
| Membaca percakapan | `GET /api/v1/accounts/{account_id}/messages?limit=20&offset=0` → `Page<Message>` |
| Simulasi inbound | `POST /api/v1/accounts/{account_id}/messages` → `Message` |
| Simulasi dispatch | `POST /api/v1/messages/{message_id}/simulate-dispatch` → `DispatchResult` |
| Membaca review | `GET /api/v1/reviews?limit=20&offset=0` → `Page<ReviewItem>` |
| Membaca audit | `GET /api/v1/accounts/{account_id}/audit?limit=20&offset=0` → `Page<AuditEvent>` |
| Membaca policy demo | `GET /api/v1/policies/current` → `PolicySnapshot` |
| Membaca kelayakan contact | `GET /api/v1/accounts/{account_id}/contact-policy` → `PolicyDecision` |

Semua DTO memakai `snake_case`. `Page<T>` memakai `{items,total,limit,offset}`. List baru berurutan `created_at,id` menaik dan memakai limit 1–100 serta offset nonnegatif. Agent registry berurutan menurut enam role pada brief. Write menolak field tak dikenal.

| DTO | Field dan nilai |
| --- | --- |
| `AgentDefinition` | `id`, `name`, `role`, `description`, `runtime_mode` (`simulated|live|unavailable`), `connection_status` (`ready|disconnected|unavailable`) |
| `RunRequest` | `instruction` string 1–2000 karakter setelah trim, `idempotency_key` UUID, `scenario` (`reminder|hardship|dispute|human_request`) |
| `RunStep` | `id`, `role`, `outcome` (`completed|blocked|failed`), `summary` |
| `RunResult` | `id`, `account_id`, `runtime_mode`, `status` (`completed|blocked|failed`), `steps: RunStep[]`, `draft_message_id` nullable, `reason_codes: string[]`, `created_at` |
| `InboundMessageRequest` | `text` string 1–2000 karakter setelah trim, `idempotency_key` UUID, `scenario` dengan enum yang sama seperti run |
| `Message` | `id`, `account_id`, `run_id` nullable, `channel=simulated_chat`, `direction` (`inbound|outbound`), `text`, `status` (`received|draft|simulated_sent|blocked`), `created_at` |
| `DispatchRequest` | `idempotency_key` UUID |
| `DispatchResult` | `message_id`, `status` (`simulated_sent|blocked`), `reason_codes: string[]`, `audit_event_id` |
| `ReviewItem` | `id`, `account_id`, `run_id` nullable, `reason` (`hardship|dispute|human_request`), `status=open`, `created_at` |
| `AuditEvent` | `id`, `account_id`, `run_id` nullable, `action`, `outcome`, `reason_codes`, `correlation_id`, `created_at`, `previous_hash` nullable untuk genesis, `event_hash` |
| `PolicySnapshot` | `id`, `version`, `timezone`, `contact_days: number[]` dengan ISO Senin=1, `contact_start`, `contact_end`, `holiday_coverage_start`, `holiday_coverage_end`, `holidays: string[]`, `max_proactive_contacts_per_local_date`, `approved_templates: {id,text}[]` |
| `PolicyDecision` | `account_id`, `allowed: boolean`, `reason_codes: string[]`, `policy_version` nullable bila policy hilang, `contact_state` (`active|paused_human_review`), `evaluated_at` |

Reason code awal adalah `policy_missing`, `holiday_coverage_missing`, `outside_contact_window`, `recipient_unverified`, `case_paused`, `frequency_limit`, `guardrail_rejected`, `approval_expired`, `approval_invalid`, dan `policy_changed`. Frontend menyediakan label Inggris dan fallback aman untuk code baru.

Business rejection menghasilkan hasil `blocked` yang tercatat. Resource hilang memakai HTTP 404, input invalid memakai 422, key sama dengan payload berbeda memakai 409. Error HTTP baru memakai `detail={code,message,correlation_id}`. Client tetap menerima `detail` string pada error endpoint existing. Request write berhasil dan replay memakai HTTP 200.

Idempotency key terikat caller tepercaya, operasi, resource target, dan hash request canonical. Key sama mengembalikan hasil semula. Resource baru atau payload berbeda dengan key sama menghasilkan 409. Dua key berbeda yang mengirim message sama tetap dibatasi unique delivery per message.

Fixture policy demo memakai `Asia/Jakarta`, Senin–Sabtu, `08:00` inklusif hingga `18:00` eksklusif, serta satu proactive contact per account per tanggal lokal. Cakupan kalender fixture adalah 7–8 November 2026 dengan daftar libur kosong. Tanggal di luar cakupan memblokir contact. Clock allowed adalah 7 November 2026 pukul 10:00 Jakarta. Fixture ini dipakai untuk simulasi dan pengujian. Konfigurasi policy produk memerlukan keputusan tersendiri.

## Peta file dan kepemilikan

Semua path relatif terhadap root backend.

| Area | File dan tanggung jawab |
| --- | --- |
| Fondasi database | `app/db/connection.py`, `app/db/import_sqlite.py`, `alembic.ini`, `migrations/env.py`, `migrations/versions/` untuk koneksi, import, dan riwayat schema |
| Accounts | `app/modules/accounts/service.py`, `repository.py` untuk lookup dan fakta account yang dipakai REST serta MCP |
| Agent registry | `app/modules/agents/schemas.py`, `service.py`, `router.py` |
| Run | `app/modules/runs/schemas.py`, `repository.py`, `router.py` dan orkestrasi `app/workflows/collection.py` |
| Percakapan | `app/modules/conversations/schemas.py`, `service.py`, `repository.py`, `router.py` |
| Compliance | `app/modules/compliance/schemas.py`, `service.py`, `repository.py`, `router.py`, `demo_policy.py` |
| Review | `app/modules/reviews/schemas.py`, `service.py`, `repository.py`, `router.py` |
| Audit | `app/modules/audit/schemas.py`, `service.py`, `repository.py`, `router.py` |
| Idempotency lintas workflow | `app/shared/idempotency.py`, `app/shared/errors.py`, `app/shared/context.py` untuk caller tepercaya, serta `app/shared/schemas.py` untuk `Page[T]` |
| Clock lintas domain | `app/shared/clock.py` |
| Adapter simulator/channel | `app/integrations/agents/simulator.py`, `app/integrations/channels/simulated.py` |
| MCP | `app/mcp/server.py`, `app/mcp/tools/accounts.py`, `compliance.py`, `conversations.py`, dan `app/mcp/schemas/` sesuai kebutuhan mapping |

### Tugas B0 — Selesaikan dan verifikasi fondasi PostgreSQL

**Pemilik** — Backend database. Pekerjaan ini dijalankan terpisah dari fitur skeleton.

**File** — `pyproject.toml`, `uv.lock`, `app/config.py`, `app/seed.py`, `app/main.py`, `app/modules/accounts/repository.py`, `app/modules/accounts/service.py`, `app/modules/dashboard/service.py`, `app/db/connection.py`, `app/db/import_sqlite.py`, `alembic.ini`, `migrations/env.py`, revision baseline di `migrations/versions/`, `compose.yaml`, `Dockerfile`, `.env.example`, `.github/workflows/checks.yml`, `README.md`, `AGENTS.md`, `tests/conftest.py`, `tests/test_store.py`, `tests/test_api.py`, `tests/test_import_sqlite.py`, `tests/test_database_config.py`. Hapus `app/store.py` setelah seluruh consumer berpindah.

**Interface** — Konfigurasi runtime memakai `DATABASE_URL`. Host lokal memakai PostgreSQL port 5432. Compose menyediakan `postgres`, `migrate` one-shot, dan `backend`. Service `postgres-test` pada profile test memakai port host 5433. Psycopg menjadi driver runtime. Alembic memakai SQLAlchemy untuk migration.

- [x] Tambahkan dependency dan lockfile, migration baseline accounts, serta readiness check tanpa DDL startup. Pertahankan seluruh kontrak endpoint existing.
- [x] Sediakan Compose dengan healthcheck PostgreSQL, migration sebelum backend, volume data, dan binding port loopback. Pisahkan database test dari database demo.
- [x] Implementasikan `uv run python -m app.db.import_sqlite <path>` untuk membaca sumber SQLite secara read-only dan memvalidasi seluruh record sebelum commit satu transaksi PostgreSQL. Konflik data harus abort dengan pesan aman. Jalankan ulang secara idempotent tanpa menimpa data yang berbeda.
- [x] Uji baseline kosong, seed dua kali, import preservation, source tidak berubah, invalid row, konflik destination, rollback, constraint, aging, search, pagination, dan summary existing pada PostgreSQL sementara.
- [x] Verifikasi jalur Compose melalui `docker compose up --build -d --wait`, `docker compose ps -a`, `docker compose exec backend uv run --no-sync python -m app.seed`, dan HTTP health/accounts/dashboard. Jalankan seed dua kali dan pastikan tetap delapan account.
- [x] Verifikasi jalur host melalui `uv sync --locked`, `uv run alembic upgrade head`, dan `uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000` dengan `DATABASE_URL` mengarah ke PostgreSQL demo. Jalankan suite dengan database test sesuai README aktual.
- [x] Catat hasil `uv run pytest` dan `uv run ruff check .`, status Docker/HTTP, serta keterbatasan tooling sebelum menandai fondasi selesai.

**Bukti verifikasi fondasi** — Agent database melaporkan `uv sync --locked`, `uv run ruff check .`, `uv run alembic heads`, dan `docker compose config --quiet` lulus. Root agent mengulang suite terakhir secara independen dan mendapat 27 passed, 0 skipped, serta satu warning deprecation Starlette/httpx existing. Root juga memverifikasi Ruff, sync lockfile, status Compose, dan HTTP portfolio.

Compose `up --build -d --wait` berhasil, PostgreSQL/backend healthy, dan migrate exit 0. Seed container menghasilkan delapan row pada run pertama dan nol row baru pada run kedua. HTTP `/health`, `/ready`, search, pagination, detail, 404, 422, serta dashboard dengan total Rp21.500.000 lulus. Full JSON delapan account tetap sama setelah container PostgreSQL direkreasi dengan named volume yang sama.

Jalur host `uv run alembic upgrade head/current` dan Uvicorn juga diverifikasi memakai PostgreSQL Compose. Smoke Uvicorn memakai port sementara 8001 dan prosesnya dihentikan setelah pemeriksaan. Port normal README tetap 8000. Database SQLite developer lama tidak ditemukan, sehingga import diverifikasi memakai fixture dengan pemeriksaan byte sumber. Data portfolio lama milik developer belum diimpor.

Perintah suite yang diverifikasi adalah berikut.

```sh
docker compose --profile test up -d --wait postgres-test
TEST_DATABASE_URL=postgresql://collectflow_test:local-test-password@127.0.0.1:5433/collectflow_test uv run pytest -q
```

Semua perintah pytest pada tugas berikut memakai `TEST_DATABASE_URL` yang sama atau PostgreSQL test terisolasi sesuai README. Database demo tetap terpisah.

### Tugas B1 — Tambahkan schema collection dan kontrak Pydantic

**Pemilik** — Backend. Bergantung pada B0.

**File** — Tambah `migrations/versions/0002_add_collection_skeleton.py`, schemas pada modul di peta file, `app/shared/schemas.py`, `app/db/connection.py`, `app/seed.py`, `tests/migrations/test_collection_schema.py`, dan `tests/unit/modules/test_contracts.py`. Sesuaikan parent revision dengan baseline aktual.

**Interface** — DTO mengikuti tabel kontrak. PostgreSQL menyimpan account contact state/verified recipient, runs/steps, messages, review items, approval, idempotency, simulated deliveries, dan audit events. Seluruh FK mengarah pada ID account existing.

- [ ] Tulis test upgrade baseline berisi delapan account ke head. Assert seluruh field lama tetap sama, foreign key aktif, dan unique delivery serta idempotency menolak duplikasi.
- [ ] Jalankan `uv run pytest tests/migrations/test_collection_schema.py tests/unit/modules/test_contracts.py` dan pastikan gagal karena schema/DTO belum tersedia.
- [ ] Tambahkan revision terpisah dengan `TIMESTAMPTZ` untuk event dan integer Rupiah. Update `REQUIRED_REVISION` pada `app/db/connection.py` dari `0001_accounts` ke revision collection dan uji readiness pada schema tertinggal serta schema head. Account existing mendapat state awal yang memblokir contact sampai recipient/policy demo disiapkan secara eksplisit.
- [ ] Tambahkan DTO Pydantic dengan enum, UUID key, panjang string, pagination, dan `extra=forbid` pada write. Lengkapi seed eksplisit untuk recipient sintetis dan contact state tanpa mengubah row account existing.
- [ ] Jalankan kembali test tugas ini. Expected seluruh preservation, constraint, input invalid, dan schema serialization lulus. Review diff lalu buat commit tugas ini saat implementasi.

### Tugas B2 — Implementasikan service dan simulator deterministik

**Pemilik** — Backend. Bergantung pada B1.

**File** — Service/repository pada accounts, runs, conversations, compliance, reviews, audit, `app/workflows/collection.py`, adapter simulator/channel, clock/error/idempotency shared, `app/config.py`, `.env.example`, `tests/unit/modules/test_compliance.py`, `tests/integration/test_collection_workflow.py`, `tests/integration/test_dispatch_concurrency.py`, `tests/unit/modules/test_audit.py`.

**Interface** — `start_demo_run(account_id, request: RunRequest, caller: CallerContext, clock: Clock) -> RunResult`, `receive_demo_message(account_id, request: InboundMessageRequest, caller, clock) -> Message`, `simulate_dispatch(message_id, request: DispatchRequest, caller, clock) -> DispatchResult`, `evaluate_contact_policy(account_id, caller, clock) -> PolicyDecision`. `CallerContext` didefinisikan di `app/shared/context.py` dan clock di `app/shared/clock.py`. `Clock.now() -> datetime` mengembalikan UTC timezone-aware. Context berasal dari server. Service workflow memiliki transaksi. Repository menerima koneksi transaksi yang sama.

- [ ] Tulis test policy hilang, coverage libur hilang, Minggu, batas 08:00/18:00, recipient belum verified, case paused, dan frequency limit. Assert blocked tanpa draft/delivery.
- [ ] Tulis test hardship/dispute/human request yang membuat satu inbound atau run, satu review open, pause case, dan audit. Escalation tetap diterima di luar jendela contact dan tidak membuat outbound contact. Inject failure di tengah transaksi dan assert seluruh write rollback.
- [ ] Jalankan test di atas dan pastikan gagal karena service belum tersedia.
- [ ] Implementasikan pemeriksaan service dan bounded simulator empat role. Template demo memakai placeholder fakta, kemudian server merender nominal/recipient dari account terverifikasi. Registry melaporkan availability aktual semua role.
- [ ] Implementasikan deterministic guardrail dan approval bertanda tangan HMAC yang terikat hash isi, account, recipient, policy version, dan expiry. Konfigurasi secret di backend. Kandidat live-model nanti wajib melewati classifier terkonfigurasi dan fail closed saat classifier gagal.
- [ ] Implementasikan satu jalur simulated dispatch. Lock state account/message sebelum revalidasi agar frequency limit tetap berlaku pada message berbeda yang bersamaan. Commit delivery, outcome idempotency, dan audit dalam transaksi yang sama.
- [ ] Tambahkan test key sama/replay, konflik payload, dua key untuk message sama, dua message/account sama, perubahan policy/state/recipient/content, approval invalid/expired, dan rollback dispatch. Assert maksimal satu pengiriman yang diizinkan policy.
- [ ] Tambahkan hash chain audit per account dengan locking saat append. Uji dua append bersamaan, tampering pada salinan event, dan replay audit yang menjaga jumlah message/delivery.
- [ ] Jalankan `uv run pytest tests/unit/modules tests/integration`. Expected semua aturan dan concurrency lulus pada PostgreSQL sementara. Review diff lalu buat commit tugas ini saat implementasi.

### Tugas B3 — Sediakan REST dan publikasikan kontrak ke frontend

**Pemilik** — Backend, dengan sinkronisasi frontend. Bergantung pada B2.

**File** — Router modul pada peta file, `app/main.py`, `tests/integration/test_collection_api.py`, `tests/test_api.py`, `README.md`.

**Interface** — Endpoint dan error mengikuti tabel kontrak. GET contact policy memakai `evaluate_contact_policy` yang sama dengan dispatch. Semua router memetakan domain error tanpa SQL/stack trace.

- [ ] Tulis test route/DTO, 404, 422, 409, pagination timestamp/ID stabil, extra field ditolak, dan caller role palsu. Tulis test CORS POST hanya untuk origin yang diizinkan.
- [ ] Jalankan `uv run pytest tests/integration/test_collection_api.py tests/test_api.py` dan pastikan endpoint baru gagal sebelum implementasi.
- [ ] Daftarkan router, context caller lokal tepercaya, dan CORS `GET,POST` dengan header yang benar-benar dipakai. Return blocked sebagai domain outcome yang tercatat. Pisahkan GET read dari write agar reload aman.
- [ ] Jalankan kembali test tugas ini dan inspeksi `/openapi.json`. Sinkronkan seluruh DTO, enum, route, dan error dengan `src/features/*/types.ts` serta `api.ts` pada frontend.
- [ ] Update README dengan kontrak dan demo scenario yang teruji. Review diff lalu buat commit tugas ini saat implementasi.

### Tugas B4 — Expose service melalui MCP stdio

**Pemilik** — Backend/integrasi agent. Bergantung pada B3.

**File** — `pyproject.toml`, `uv.lock`, `app/mcp/server.py`, tools/schema pada peta file, `tests/mcp/test_protocol.py`, `tests/mcp/test_permissions.py`, `tests/mcp/test_rest_parity.py`, `README.md`.

**Interface** — Tool `get_account`, `get_approved_sop`, `get_contact_history`, `evaluate_contact_policy`, dan `simulate_dispatch`. Service `get_account(account_id, caller, clock) -> Account` diekspor accounts, `get_current_policy(caller) -> PolicySnapshot` diekspor compliance, dan `get_contact_history(account_id, limit, offset, caller) -> Page[Message]` diekspor conversations. `get_approved_sop` memetakan policy serta approved templates dari service compliance. Tool write memakai `DispatchRequest` dan service B2. Target entry point setelah dibuat adalah `uv run python -m app.mcp.server` melalui stdio lokal.

- [ ] Tambahkan maintained MCP SDK dan lockfile. Tulis SDK-client test initialize, discovery, input/output schema, valid call, unknown account, dan tool error dengan kode stabil.
- [ ] Tulis test izin read/write, payload role palsu, input berlebih, hasil paginated, timeout, cancellation, serta duplicate dispatch lintas REST dan MCP. Jalankan `uv run pytest tests/mcp` dan pastikan gagal sebelum adapter dibuat.
- [ ] Implementasikan registry tools dengan structured output dan annotation sesuai perilaku. Adapter memanggil service langsung. I/O psycopg sinkron di-offload dari handler async. Stdout hanya protokol, log stderr aman, cleanup saat cancel/shutdown.
- [ ] Jalankan `uv run pytest tests/mcp`. Expected handshake, schema runtime, akses ditolak, parity gate, dan idempotency lulus. Catat SDK/transport aktual serta command teruji di README.
- [ ] Bila Hermes tersedia, verifikasi versi dan format konfigurasi aktual, hubungkan manager, lakukan discovery/read, dan uji allowed serta blocked dispatch. Catat milestone Hermes terpisah bila runtime belum tersedia. Review diff lalu buat commit tugas ini saat implementasi.

### Tugas B5 — Verifikasi demo lintas-repo

**Pemilik** — Backend dan frontend. Bergantung pada B3 dan layar frontend. MCP dinilai melalui B4.

**File** — `tests/integration/test_demo_journey.py`, `README.md`, `.env.example` bila konfigurasi berubah.

- [ ] Tulis integration test account → run → draft → allowed dispatch → conversation → audit. Replay dispatch dan assert jumlah delivery tetap satu. Tambahkan blocked contact dan hardship → review → paused case.
- [ ] Jalankan `uv run pytest` dan `uv run ruff check .` dengan PostgreSQL test terpisah. Jalankan `uv run alembic heads` dan pastikan satu head.
- [ ] Jalankan frontend sesuai rencananya. Verifikasi allowed, blocked, dan hardship melalui UI normal, lalu reload untuk memeriksa persistence. Pastikan portfolio/search/pagination/dashboard lama tetap benar.
- [ ] Laporkan migration/import, HTTP, MCP handshake, browser flow, dan Hermes berdasarkan hasil aktual masing-masing. Review diff lalu buat commit tugas ini saat implementasi.

## Batas penerimaan

Skeleton selesai ketika allowed reminder, blocked contact, dan escalation manusia tersimpan dan dapat dibaca melalui REST/UI, disertai gate deterministik dan audit. MCP selesai ketika pengujian protokol SDK dan parity service lulus. Hermes selesai ketika runtime lokal benar-benar terhubung dan menjalankan operasi teruji.

WhatsApp, email, telepon, voice, rekonsiliasi pembayaran, deployment improvement otomatis, cakupan policy produksi, autentikasi remote/multi-tenant, dan evaluasi produk penuh menjadi milestone berikutnya.

## Verifikasi dokumen

Path source, README, panduan repo, schema existing, dan plan gabungan awal telah dibaca. Dokumen ini memisahkan tanggung jawab backend dan mempertahankan kontrak portfolio. Checkbox fitur masih berupa rencana. Fondasi database sudah diverifikasi melalui PostgreSQL aktual, HTTP, Compose, host startup, migration, import fixture, serta persistence named volume. Seluruh checkbox B1–B5 masih berupa rencana. Penyusunan dokumen ini memeriksa konsistensi path dan kontrak tanpa mengimplementasikan fitur agent/UI.
