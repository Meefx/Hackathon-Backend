# Backend Agentic Collection System Implementation Plan

> **For agentic workers** — Gunakan superpowers:executing-plans atau superpowers:subagent-driven-development sesuai metode eksekusi yang dipilih user. Jalankan tugas per milestone dengan bukti verifikasi.

**Goal** — Menyediakan collection workflow persisten, Hermes bridge, MCP HTTP, konsumsi knowledge service teman, dan skeleton channel di atas PostgreSQL existing.

**Architecture** — REST dan MCP memakai service domain yang sama. Worker dari kode/image backend mengerjakan job PostgreSQL dengan lease dan checkpoint. Hermes menghasilkan reasoning/candidate, sedangkan compliance, review, serta dispatch dimiliki service backend.

**Tech Stack** — Python 3.12+, FastAPI, Pydantic, psycopg, PostgreSQL 17, Alembic, uv, pytest, Ruff, Docker Compose. HTTP client runtime dan maintained Python MCP SDK ditambahkan bersama kebutuhan yang diuji.

**Spec** — Brief, kontrak v2, dan acceptance di dokumen ini dapat dipakai saat repo di-clone sendiri. Baca AGENTS.md dan README.md. Rencana frontend berada pada path docs/superpowers/plans/2026-11-07-agentic-collection-skeleton.md di repo frontend. Master workspace bernama 2026-11-07-agentic-collection-system-plan.md.

## Status yang diperiksa

Fondasi PostgreSQL, Alembic baseline 0001_accounts, Compose, seed/import, portfolio, dan dashboard sudah diimplementasikan. Turn sebelumnya memverifikasi 27 tests PostgreSQL, migration, Compose, HTTP, serta volume persistence. Pada penyusunan rencana v2 ini hanya dilakukan inspeksi read-only.

Fitur di bawah seluruhnya target implementasi. Hermes belum ditemukan pada lingkungan inspeksi. Modul KM teman belum berada di source yang diperiksa. Nama aplikasi tetap CollectFlow.

## Global Constraints

- Pertahankan GET portfolio/dashboard, field account, status aging, integer Rupiah, search/filter sebelum pagination, total seluruh portfolio, dan urutan due_date,id.
- Aging memakai kalender Asia/Jakarta. Event disimpan TIMESTAMPTZ UTC. Clock diinjeksikan untuk test.
- Demo memakai borrower sintetis dan simulated_chat. WhatsApp/email/phone menyediakan skeleton unavailable yang dapat diuji.
- Izin berasal dari server context. Profile/model output, role argument, dan approved=true dari model tidak memberi kewenangan.
- REST dan MCP memakai service, transaksi, idempotency, dan guard yang sama. Domain tidak mengimpor transport/SDK.
- Setiap schema change memakai revision setelah baseline. Satu pemilik migration menjaga satu head dan memperbarui REQUIRED_REVISION.
- Transaksi dan row lock bersifat singkat. Panggilan model/runtime/classifier/channel berjalan di luar transaksi database.
- Required policy/knowledge/gate yang gagal memblokir outbound. Inbound escalation tetap diterima di luar jam contact.
- Secret melalui Settings/env backend. Return/log hanya data yang dibutuhkan, ID, correlation, safe outcome, dan durasi.
- Scope pertama local demo. Shared/remote deployment memerlukan desain autentikasi operator/tenant tersendiri.
- KM ingestion, indexing, document storage, dan publication dimiliki teman user. Adapter kita memanggil public service dalam proses backend yang sama.

## Review Focus

1. Migration menjaga portfolio lama dan membatasi defaults case baru sampai recipient/policy eksplisit tersedia. B1 memiliki tests preservation/constraint.
2. Lease habis, runtime restart, dan respons submission hilang dapat meninggalkan hasil ambigu. B2/B6 menguji reconciliation, fencing, dan exact replay.
3. Policy, recipient, candidate, atau knowledge approval berubah sebelum dispatch. B3/B4 menguji revalidasi serta rollback.
4. Dua message/account berbeda dan dua worker berjalan bersamaan. B2/B3 menguji account lock, unique receipt, dan stale worker.
5. MCP/Hermes dapat mengembalikan hasil schema invalid atau mengklaim role yang berbeda. B5/B6 menguji autentikasi, assignment, provenance, finalization, dan runtime aktual.

## Kontrak bersama versi 2

Kontrak ini adalah target implementasi. Pydantic dan OpenAPI backend menjadi kontrak HTTP yang dijalankan. Kontrak MCP diuji melalui discovery SDK. Endpoint portfolio existing mempertahankan field, enum, pagination, urutan, dan satuan uang.

| DTO | Field dan nilai |
| --- | --- |
| StartRunRequest | instruction string 1–2000 karakter, task_type follow_up/collector_brief/improvement_review, runtime_mode simulated/hermes, idempotency_key UUID, manager_session_id UUID nullable. demo_scenario reminder/hardship/dispute/human_request hanya diterima dalam mode simulated. |
| RunAccepted | run_id, manager_session_id, status queued, version, created_at. POST berhasil dan replay mengembalikan HTTP 202 serta identitas run yang sama. |
| RunSnapshot | id, account_id, manager_session_id, status queued/running/completed/blocked/failed/cancelled/interrupted, outcome nullable, requested_runtime_mode, actual_runtime_mode nullable, phase, steps, artifacts, reason_codes, version, session_turn_index, latest_event_seq, created_at, started_at nullable, finished_at nullable, heartbeat_at nullable, cancellation_requested_at nullable, allowed_actions. |
| RunStep | id, sequence, role, status pending/running/completed/blocked/failed/skipped, summary, evidence_origin backend_verified/runtime_reported/agent_reported, started_at nullable, finished_at nullable. |
| RunEvent | sequence, run_id, type, safe_summary, evidence_origin, created_at. Sequence monotonik per run. |
| AgentDefinition | id, role manager/collection/knowledge/qa/improvement/copilot, name, description, configured, available, runtime_mode simulated/hermes/unavailable, capabilities, unavailable_reason nullable, checked_at nullable. |
| IntegrationStatus | id, kind runtime/knowledge/mcp/guardrail/channel, implemented, configured, health healthy/unavailable/unknown, execution_mode simulated/hermes/in_process/unavailable, capabilities, disabled_reason nullable, checked_at nullable. Seluruh konfigurasi respons sudah disanitasi. |
| KnowledgeSnapshot | id, run_id, status ready/no_results/unavailable/error, source_mode knowledge_module/demo_fixture, required, retrieved_at, snapshot_hash, sources, reason_code nullable. |
| KnowledgeSource | document_id, document_version, snippet_id, citation_id, title, excerpt, content_hash, approved_at, effective_from nullable, effective_until nullable, source_uri nullable. |
| Message | id, account_id, run_id nullable, channel simulated_chat/whatsapp/email/phone, direction inbound/outbound, text, status received/draft/simulated_sent/blocked, version, citation_ids, created_at. |
| DraftCandidate | run_id, account_id, execution_attempt_id, template_id nullable, text_with_placeholders, citation_ids, idempotency_key. Backend merender fakta dan membuat message ID. |
| DispatchRequest | idempotency_key UUID, expected_message_version. Server mengambil approval dan recipient dari state resmi. |
| DispatchResult | message_id, status simulated_sent/blocked/unavailable, reason_codes, audit_event_id. |
| ReviewItem | id, account_id, run_id nullable, reason hardship/dispute/human_request/operator_takeover, status open/resolved, version, created_at, resolved_at nullable. |
| ReviewResolution | decision keep_paused/request_resume, note 1–1000 karakter, expected_version, idempotency_key. Hasil berasal dari service backend. |
| PolicyDecision | account_id, case_version, allowed, reason_codes, policy_version nullable, contact_state active/paused_human_review/paused_operator, evaluated_at. |
| ManagerSession | id, account_id, title, created_at, updated_at, last_run_id nullable. |
| ManagerTurn | id, session_id, turn_index, run_id nullable, role operator/manager, content, created_at. Content berisi instruksi atau ringkasan final yang aman. |
| RunArtifact | id, run_id, kind draft/qa_report/collector_brief/improvement_proposal, text, message_id nullable, citation_ids, review_state pending/approved/rejected nullable, created_at. QA/proposal merupakan advisory evidence. |
| FinalRuntimeResult | application_run_id, task_type, summary, artifact_ids, candidate_message_id nullable, citation_ids, review_requested boolean. Semua reference diverifikasi ke data milik run. |
| KnowledgeQuery | query 1–1000 karakter, purpose collection_draft/qa_check/copilot/improvement_review, locale id-ID, top_k 1–5, deadline_at. Identity/corpus permission berasal dari server context. |
| ReferenceValidation | valid, checked_at, invalid_citation_ids, reference_version. Semantik snapshot/revocation disepakati dengan pemilik KM. |
| CaseActionRequest | expected_case_version, idempotency_key, note 1–1000 karakter. Dipakai takeover/resume oleh operator berizin. |
| CaseActionResult | account_id, case_version, contact_state, action_applied, reason_codes, review_id nullable. |
| ReviewResolutionResult | review ReviewItem, case CaseActionResult. Penyelesaian review dan hasil permintaan resume dipisahkan. |
| GuardrailResult | decision allow/block/unavailable, policy_version, evidence_id, classifier_mode live/demo_fixture, evaluated_at. |
| AuditEvent | id, account_id, run_id nullable, action, actor_kind, outcome, reason_codes, correlation_id, created_at, previous_hash nullable, event_hash. |

List biasa memakai items,total,limit,offset dengan limit 1–100. Event polling memakai items,next_sequence,has_more. Tanggal event memakai UTC timezone-aware. Status handling case terpisah dari aging current/early/late/critical. Outcome run dapat berupa draft_created, escalated, brief_created, atau proposal_created.

HTTP 404 untuk resource hilang, 422 untuk input invalid, 409 untuk konflik key/payload atau version stale, dan 403 untuk caller yang tidak berizin. Write menolak field tak dikenal. Error baru memakai detail berisi code,message,correlation_id. Client tetap mendukung error detail string pada endpoint portfolio existing.

Reason code awal mencakup policy_missing, holiday_coverage_missing, outside_contact_window, recipient_unverified, case_paused, frequency_limit, guardrail_rejected, approval_expired, approval_invalid, policy_changed, guardrail_unavailable, knowledge_unavailable, no_approved_context, knowledge_reference_invalid, runtime_unavailable, runtime_interrupted, result_invalid, channel_unavailable, dan version_conflict. UI menyediakan fallback aman untuk code baru.

Registry agents/integrations memakai items dengan urutan tetap. History run/session berurutan created_at,id menurun; percakapan/audit menaik. Manager turn berurutan turn_index lalu operator sebelum manager. Default pagination adalah 20. allowed_actions dapat memuat cancel, reconcile, dan retry_safe_step sesuai izin/state. RecoverRunRequest memakai action reconcile/retry_safe_step, expected_version, dan idempotency_key.

Contact state diturunkan dari hold aktif. Open review menghasilkan paused_human_review; jika seluruh review sudah resolved tetapi operator hold masih aktif, state menjadi paused_operator; tanpa hold state menjadi active. Seluruh alasan aktif tetap dicatat walaupun UI menampilkan satu contact_state.

## Endpoint target

| Operasi | REST |
| --- | --- |
| Registry agent dan dependency | GET /api/v1/agents; GET /api/v1/integrations |
| Submit tugas account | POST /api/v1/accounts/{account_id}/runs → HTTP 202 RunAccepted |
| Run snapshot dan history | GET /api/v1/runs/{run_id}; GET /api/v1/accounts/{account_id}/runs |
| Event dan recovery | GET /api/v1/runs/{run_id}/events?after_sequence=0&limit=100; POST /api/v1/runs/{run_id}/cancel; POST /api/v1/runs/{run_id}/recover |
| Manager history | GET /api/v1/accounts/{account_id}/manager-sessions; GET /api/v1/manager-sessions/{session_id}/messages |
| Evidence knowledge | GET /api/v1/runs/{run_id}/knowledge |
| Percakapan | GET dan POST /api/v1/accounts/{account_id}/messages |
| Dispatch demo | POST /api/v1/messages/{message_id}/simulate-dispatch |
| Policy dan eligibility | GET /api/v1/policies/current; GET /api/v1/accounts/{account_id}/contact-policy |
| Takeover dan resume case | POST /api/v1/accounts/{account_id}/takeover; POST /api/v1/accounts/{account_id}/resume → CaseActionResult |
| Review manusia | GET /api/v1/reviews; GET /api/v1/reviews/{review_id}; POST /api/v1/reviews/{review_id}/resolve |
| Audit | GET /api/v1/accounts/{account_id}/audit |

GET history run mendukung request_key UUID yang dibatasi account dan caller. Ini menemukan submission yang responsnya hilang. Cancel/recover memakai idempotency_key dan expected_version. Recovery action dipilih dari allowed_actions backend dan ditolak bila execution masih ambigu. Manager session hanya dapat dipakai pada account dan caller yang memilikinya. Simulasi inbound memakai text, idempotency_key, serta demo_scenario yang eksplisit.

## Interface service dan kepemilikan

| Area | Pemilik dan file yang dituju |
| --- | --- |
| Account facts | app/modules/accounts/service.py dan repository.py existing |
| Handling state/recipient | app/modules/cases/schemas.py, service.py, repository.py |
| Run/session/history | app/modules/runs/schemas.py, service.py, repository.py, router.py; app/modules/manager/ untuk session/turn |
| Orkestrasi dan worker | app/workflows/collection.py; app/workers/collection.py |
| Conversations | app/modules/conversations/ dengan schemas/service/repository/router |
| Policy/guardrail | app/modules/compliance/ dan app/integrations/guardrail/ |
| Knowledge consumption | app/modules/knowledge_context/contracts.py, service.py, repository.py; app/integrations/knowledge/gateway.py |
| Review dan audit | app/modules/reviews/ dan app/modules/audit/ |
| Registry integration/channel | app/modules/integrations/; app/integrations/channels/ |
| Runtime | app/integrations/hermes/client.py, runtime.py, schemas.py; app/integrations/agents/simulated.py |
| MCP HTTP | app/mcp/server.py, auth.py, schemas/, tools/accounts.py, knowledge.py, conversations.py, reviews.py |
| Shared yang benar-benar lintas domain | app/shared/clock.py, errors.py, caller.py, idempotency.py |

Buat file hanya ketika tanggung jawabnya diperlukan. Router memetakan domain error. Repository menerima connection milik transaksi use case. App factory merangkai lifecycle dan router.

Interface utama yang diekspor adalah start_run(account_id, request, caller, clock) -> RunAccepted; get_run(run_id, caller) -> RunSnapshot; receive_message(account_id, request, caller, clock) -> Message; submit_draft(candidate, caller, clock) -> Message; simulate_dispatch(message_id, request, caller, clock) -> DispatchResult; resolve_review(review_id, request, caller, clock) -> ReviewResolutionResult; takeover_case(account_id, request, caller, clock) -> CaseActionResult; request_resume(account_id, request, caller, clock) -> CaseActionResult. Service case mengekspor evaluate_contact_policy dan service audit mengekspor append_event dalam transaksi use case.

## B0 — Fondasi PostgreSQL yang sudah tersedia

- [x] PostgreSQL runtime, Alembic baseline, Docker Compose database/migrate/backend.
- [x] Seed idempotent, source-preserving legacy importer, constraints, aging, API compatibility.
- [x] Verifikasi turn implementasi sebelumnya mencatat 27 tests lulus, Ruff lulus, HTTP healthy, seed 8 lalu 0, dan account JSON bertahan setelah container database direkreasi.

## B1 — Kunci kontrak dan tambah collection schema

**Pemilik** — Subagent backend domain, root untuk kontrak, pemilik KM untuk persetujuan interface. **File** — app/modules/*/schemas.py sesuai peta; migrations/versions/0002_collection_state.py; app/db/connection.py; app/seed.py; tests/migrations/test_collection_state.py.

- [ ] Finalisasi DTO/endpoints v2 bersama frontend. Pisahkan run submission, persisted manager turn, execution status, dan dispatch.
- [ ] Sepakati public KnowledgeGateway dan validator references dengan teman. Simpan adapter boundary milik kita agar file implementation KM tetap dimiliki teman.
- [ ] Tulis tests baseline berisi delapan account -> head yang menjaga semua field lama, foreign key, unique idempotency, unique receipt/message, state handling, dan urutan event.
- [ ] Tambahkan case/verified recipient, policy version, manager sessions/turns, runs/steps/events/jobs/leases, draft/approval, messages, reviews, knowledge evidence, dispatch intent/receipt, dan audit. TIMESTAMPTZ untuk event; BIGINT untuk uang.
- [ ] Update REQUIRED_REVISION dan explicit synthetic seed. Case existing belum eligible sampai policy/recipient tersedia. Policy fixture menunjukkan coverage dan demo clock yang dipakai.
- [ ] Jalankan tests migration, existing HTTP/store tests, dan Ruff. Expected account preservation dan satu migration head lulus.

## B2 — Run persisten dan worker yang dapat dipulihkan

**Pemilik** — Backend domain. **File** — runs dan manager modules; app/workers/collection.py; app/workflows/collection.py; compose.yaml; tests/integration/test_run_lifecycle.py, test_worker_recovery.py.

- [ ] Tulis tests POST202, exact replay, key/payload conflict, manager session/account binding, version monotonic, event pagination, dan lookup request_key.
- [ ] Persist input/config snapshot yang aman serta job dalam satu transaksi. Simpan secret credential sebagai reference identity, bukan token dalam snapshot.
- [ ] Nomori run per manager session berdasarkan admission. Maksimal satu run running per session; run berikutnya queued sampai run sebelumnya terminal dan outcome diketahui. History dibentuk saat run mulai, berurutan turn_index dan hanya memakai hasil tervalidasi run sebelumnya. Run interrupted menahan kelanjutan session sampai rekonsiliasi selesai. Uji dua submit bersamaan dan pastikan history final pertama masuk ke task kedua.
- [ ] Implementasikan claim PostgreSQL dengan lease, heartbeat, fencing token, serta checkpoint. Worker lama ditolak ketika mencoba commit setelah fence diganti. Model/network calls berlangsung di luar transaksi.
- [ ] Terapkan queued/running/terminal states, cooperative cancellation, dan recover action dari backend. Runtime unknown dipetakan ke interrupted dengan reason dan dispatch tertahan.
- [ ] Uji restart sebelum submission, sesudah admission, sesudah candidate ditulis, serta sebelum receipt tersimpan. Replay exact payload/key hanya ketika runtime capability dan retention membenarkannya. Ambiguous execution tidak membuat admission baru secara otomatis.
- [ ] Tambahkan worker service dari image backend yang sama. Definisikan bounded concurrency, deadline, dan shutdown cleanup pada Settings. API menerima tugas saat worker offline dan menampilkan antrean persisten.
- [ ] Jalankan lifecycle/concurrency tests pada schema PostgreSQL terisolasi. Expected dua worker tidak menggandakan efek dan browser dapat melanjutkan run melalui ID.

## B3 — Workflow simulator, compliance, review, dan audit

**Pemilik** — Backend domain. **File** — cases/conversations/compliance/reviews/audit modules; integrations/agents/simulated.py; integrations/guardrail/; tests/unit/modules/ dan tests/integration/test_collection_workflow.py, test_dispatch_concurrency.py.

- [ ] Tulis tests allowed reminder, blocked contact, hardship/dispute/human request, dan operator takeover. Escalation di luar contact window tetap membuat review/pause tanpa outbound.
- [ ] Implementasikan task follow_up, collector_brief, dan improvement_review melalui runtime interface. Simulator mencatat mode simulated serta role/artefak yang benar-benar dijalankan.
- [ ] Periksa eligibility, local contact time, holiday coverage, verified recipient, pause, dan frequency sebelum outbound generation. Worker mengambil required knowledge snapshot melalui gateway sebelum men-submit outbound task ke runtime; kegagalan menghentikan task sebelum model dipanggil. Knowledge agent dapat meminta tambahan konteks approved. Render fakta uang/identitas dari server, validasi placeholder dan citation.
- [ ] Validasi candidate dengan deterministic rules dan classifier adapter untuk live-model text. Candidate boleh persisted sebagai draft karantina, tetapi follow_up menjadi blocked dengan guardrail_unavailable ketika classifier wajib belum siap. Artifact menyertakan evidence classifier; approval untuk candidate mode hermes memerlukan classifier_mode live. QA agent report disimpan sebagai input advisory dengan provenance.
- [ ] Terbitkan HMAC attestation yang terikat content/version, account, recipient, policy version, knowledge references, dan expiry. Rekam approval manusia secara terpisah bila dibutuhkan.
- [ ] Dispatch mengambil lock account/message, memeriksa ulang state dan approval, serta mencatat simulated receipt/idempotency/audit secara atomik. Dua key untuk message sama dan dua message untuk account sama mengikuti frequency limit.
- [ ] Simpan hold reason aktif secara terpisah agar beberapa review/manual hold dapat hidup bersamaan. Takeover membuat operator hold/review, membatalkan izin side effect run aktif, dan meminta runtime stop. Uji race terhadap generation/dispatch.
- [ ] Implementasikan review resolve dengan expected_version. keep_paused menyelesaikan review dan mempertahankan operator hold. request_resume menyelesaikan review lalu memanggil service request_resume yang sama dengan endpoint case resume. Service hanya melepas hold yang diotorisasi setelah semua open review/alasan pause lain dan policy terbaru lolos. Gagal resume tidak membatalkan fakta bahwa review sudah diselesaikan; return ReviewResolutionResult menunjukkan keduanya. Request resume terpisah dapat dipakai setelah keep_paused. Resume tidak langsung mengirim pesan.
- [ ] Serialisasi append hash chain audit per account. Test tampering pada salinan, append paralel, dan replay tanpa side effect.
- [ ] Jalankan workflow/gate/review/concurrency tests. Expected stale approval, missing policy, rollback, replay, dan parallel dispatch memberi outcome deterministik.

## B4 — Integrasikan service knowledge teman

**Pemilik** — Subagent integrasi untuk adapter; pemilik KM untuk service internal. **File** — knowledge_context module; app/integrations/knowledge/gateway.py; tests/integration/test_knowledge_contract.py.

KnowledgeGateway.query_approved(request, caller) -> KnowledgeResult menerima query 1–1000 karakter, purpose collection_draft/qa_check/copilot/improvement_review, locale, top_k 1–5, serta deadline. Identity/corpus permission berasal dari caller backend. Hasil dipetakan ke KnowledgeSnapshot. Gateway.validate_references(references, at, caller) -> ReferenceValidation memeriksa approval/version freshness sebelum dispatch.

- [ ] Sepakati mapping ke service internal teman dan timeout/cancellation semantics yang didukung. Adapter fixture eksplisit memberi source_mode demo_fixture untuk test.
- [ ] Tulis contract tests untuk approved/effective snippets, batas ukuran, no_results, unavailable, error/timeout, reference revoked, dan mismatched citation/hash.
- [ ] Simpan immutable run evidence berisi sumber/versi/snippet/hash yang dipakai. Pin snapshot untuk continued manager turn bila masih valid. Source URI diperlakukan sebagai data dan divalidasi sebelum ditampilkan.
- [ ] Required knowledge yang gagal memblokir generation/dispatch, sementara inbound escalation dan portfolio tetap tersedia.
- [ ] Verifikasi adapter terhadap implementation KM teman saat tersedia. Report fixture tests dan real service contract tests secara terpisah.

## B5 — Bangun MCP Streamable HTTP

**Pemilik** — Subagent Hermes/MCP. **File** — app/mcp/, app/main.py lifecycle, app/config.py, pyproject.toml/uv.lock, tests/mcp/test_protocol.py, test_permissions.py, test_service_parity.py.

Target transport adalah HTTP di /mcp dengan SDK terpelihara. Tool awal adalah get_account, get_contact_policy, get_contact_history, retrieve_approved_context, submit_draft_candidate, submit_run_artifact, dan request_human_review. Read output dibatasi. Tool write memerlukan active run/account assignment serta execution_attempt_id yang diverifikasi service.

- [ ] Tambahkan SDK dan lockfile. Mount lifecycle/session manager melalui app factory; offload blocking psycopg dari handler async.
- [ ] Pisahkan token backend->Hermes dan Hermes->MCP. Verifikasi bearer principal, target assignment, active run/fence, Origin yang sesuai, dan izin saat eksekusi.
- [ ] MCP principal agent boleh membaca konteks, submit candidate, dan request review. Service berwenang menerbitkan approval dan dispatch. Argumen role/approved/tenant/run tidak mengganti identity.
- [ ] Gunakan SDK client melalui transport nyata untuk initialize/discovery/call, structured result, error schema, invalid input, timeout, cleanup cancellation, akses tanpa token, dan cross-target denial.
- [ ] Uji parity REST/MCP terhadap gate, recipient, pause, idempotency, dan error. Prompt injection pada knowledge/tool result tidak dapat mengganti izin backend.
- [ ] Dokumentasikan endpoint, transport, SDK/protocol yang diuji, dan contoh konfigurasi Hermes yang telah diverifikasi. Readiness menampilkan MCP capability aktual.

## B6 — Hubungkan Hermes aktual dan profile specialist

**Pemilik** — Subagent Hermes/MCP, root untuk runtime acceptance. **File** — app/integrations/hermes/; runtime interface; Settings/env example; tests/integration/test_hermes_contract.py; versioned configuration examples setelah diverifikasi.

AgentRuntime mengekspor probe(), submit(request,idempotency_key), get_status(external_run_id), dan request_stop(external_run_id). Adapter menyimpan external run/session IDs, actual runtime, safe error code, usage, serta correlation. Interface ini juga dipakai SimulatedAgentRuntime.

- [ ] Spike versi/build, autentikasi, capabilities, run/status/stop support, profile routing, dan resolved tool inventory pada Hermes yang tersedia.
- [ ] Gunakan Runs HTTP API yang didukung runtime, polling, serta profile URL/credential yang dikonfigurasi server. Validasi exact request/key replay dan canonical request snapshot.
- [ ] Uji bounded business run dengan nonempty caller-owned history agar manager menunggu child dan menyatukan hasil. Persist manager history di backend dan batasi konteks per account.
- [ ] Verifikasi named collection/knowledge/QA/copilot/improvement routes sesuai task. Catat actual profile evidence. Profile/tool inheritance tidak dijadikan batas otorisasi.
- [ ] submit_run_artifact menerima RunArtifact untuk QA report, collector brief, atau improvement proposal yang sesuai task/run. FinalRuntimeResult merujuk artefak yang sudah persisted; summary dan seluruh ID/citation divalidasi. Validasi final artifact schema dan citation. Hermes turn completed yang belum mempunyai hasil domain valid tetap belum menyelesaikan business run.
- [ ] Uji malformed output, runtime unavailable, token/profile salah, stop yang belum selesai, expired external replay window, interrupted/unknown, dan reconciliation. Automatic fallback ke simulator tidak dilakukan.
- [ ] Tambahkan classifier policy sebagai integration dependency tersendiri dengan mode/configuration/health yang aktual. Spike input-output provider/classifier sesuai pilihan overview sebelum mengaktifkan approval live. Uji classifier sehat, unavailable, timeout, dan hasil invalid.
- [ ] Buktikan follow_up Hermes aktual menghasilkan knowledge/collection/QA delegation dan draft yang lolos classifier live, collector_brief menghasilkan bukti copilot, serta improvement_review menghasilkan bukti improvement. Manager dan seluruh specialist memakai route/profile yang terkonfigurasi. Simpan runtime-backed route/session evidence; role yang hanya diklaim model belum terverifikasi. Satu tindakan tambahan harus ditolak gate. Catat outcome dan runtime/model aktual yang tersedia dari provider.
- [ ] Bila runtime/credential belum tersedia, adapter/protocol dapat diverifikasi dengan fixture, sementara acceptance Hermes aktual tetap pending.

Dokumentasi Runs API dan HTTP MCP menjadi referensi spike, dengan detail kompatibilitas ditentukan versi yang dipasang. [Hermes API](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) · [Hermes MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)

## B7 — Skeleton channel, status integrasi, dan pengujian akhir

**Pemilik** — Backend domain untuk channel; root untuk integrasi. **File** — app/integrations/channels/contracts.py, simulated.py, whatsapp.py, email.py, phone.py; integrations registry/router; compose.yaml; README.md; tests/integration/test_channel_skeletons.py, test_demo_journey.py.

ChannelGateway.resolve(channel) mengembalikan adapter dengan capabilities(), dispatch(authorized_intent), dan normalize_verified_event(event). Intent dibentuk service dari account/recipient/message resmi. Provider runtime nyata akan mengimplementasikan receipt/idempotency serta verified webhook boundary pada milestone berikutnya.

- [ ] Implementasikan SimulatedChatAdapter dan tiga Unavailable adapter bernama WhatsApp/Email/Phone. Ketiganya mengembalikan channel_unavailable dan tidak membuat delivery receipt.
- [ ] Registry memisahkan implemented, configured, health, capabilities, checked_at, dan disabled_reason. Credential dan URL yang mengandung secret tidak dikembalikan.
- [ ] Tulis tests channel unsupported/missing config, no fake success, duplicate intent, dan timeout/ambiguous provider contract. Voice capability phone skeleton tetap unavailable.
- [ ] Uji full REST journey account->session/run->knowledge->draft->gate->simulated delivery->audit dan escalation->review->explicit resolution. Uji refresh/restart/cancel dan exact retry.
- [ ] Jalankan uv run pytest, uv run ruff check ., migration heads/current pada database test, MCP protocol suite, serta actual Hermes smoke bila tersedia.
- [ ] Dokumentasikan development host, Compose backend/worker, topology host/container, migrate/seed/start/stop, status integration, dan recovery. Jalankan smoke browser bersama frontend.
- [ ] Laporkan bukti portfolio, workflow, KM fixture/actual, MCP, Hermes actual, dan channel skeleton secara terpisah.

## Penerimaan backend

Backend selesai ketika data account existing terpelihara, workflow/review/dispatch/audit persisten, six role tasks tersedia sesuai konfigurasi, knowledge references tervalidasi, MCP HTTP bekerja melalui SDK client, dan actual Hermes menghasilkan artefak melalui bridge. Jalur simulator mempunyai acceptance sendiri. Koneksi KM aktual mengikuti ketersediaan service milik teman.

Scope lanjutan mencakup provider outbound nyata, live voice, payment reconciliation, remote multi-tenant auth, serta penerapan proposal improvement. Dokumen ini diperbarui pada tahap rencana; source aplikasi dan dependency tetap pada baseline yang diinspeksi.
