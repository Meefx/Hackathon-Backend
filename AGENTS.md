# Backend Engineering Standards

## Scope dan kondisi saat ini

Berlaku untuk seluruh repository backend. Jika tersedia, baca juga
`../AGENTS.md` untuk aturan lintas-repo; panduan ini tetap berlaku saat repo
di-clone sendiri. Instruksi langsung user mengungguli panduan ini.

Stack aktual: Python 3.12+, FastAPI, Pydantic/pydantic-settings, PostgreSQL 17
melalui psycopg, Alembic, uv, pytest, dan Ruff. SQLAlchemy dipakai oleh Alembic
untuk migration, bukan ORM runtime. Docker Compose menyediakan PostgreSQL,
migration one-shot, backend, dan profil database test. Durable PostgreSQL queue/worker dan SDK MCP Streamable HTTP tersedia untuk demo sintetis.
MCP memakai listener terpisah pada port 8002; REST operator tetap lokal pada 8000.

Saat ini `app/main.py` merangkai HTTP, `app/schemas.py` mempertahankan kontrak
respons, `app/db/connection.py` memiliki transaksi/readiness,
`app/modules/accounts/` memiliki SQL dan aging, dan
`app/modules/dashboard/service.py` memiliki agregasi portfolio. `app/store.py`
sudah diganti oleh modul tersebut. Struktur berikut berlaku untuk penambahan
fitur dan ekstraksi bertahap pada area yang disentuh.

## Penempatan kode

Path berikut relatif terhadap root repository. Buat hanya yang dibutuhkan.

```text
app/
  main.py                        # App factory, lifespan, middleware, registrasi router
  config.py                      # Konfigurasi environment terpusat
  db/
    connection.py                # Connection/session dan lifecycle transaksi
  modules/
    accounts/
      __init__.py
      router.py                  # HTTP, validation boundary, dependency injection
      schemas.py                 # Request/response Pydantic milik accounts
      service.py                 # Use case dan aturan bisnis accounts
      repository.py              # SQL/persistence accounts
      models.py                  # Hanya jika ORM dipakai; bukan response model
    dashboard/
      router.py
      schemas.py
      service.py
  integrations/                  # Adapter provider AI/channel/external API
    mcp/                         # Client MCP eksternal jika diperlukan
  mcp/                           # MCP server milik backend; detail di bagian MCP
    server.py                    # Factory, registrasi capability, lifecycle
    tools/                       # Adapter tool per domain
    resources/                   # Resource read-only per domain
    prompts/                     # Template prompt yang disediakan ke client
    schemas/                     # Input/output kontrak MCP
  workflows/                     # Orkestrasi lintas domain bila diperlukan
  shared/                        # Error/clock/primitif yang benar-benar lintas domain
  seed.py                        # Entry point eksplisit data demo
alembic.ini                      # Konfigurasi Alembic yang sudah tersedia
migrations/
  env.py
  versions/                      # Riwayat migration seluruh database
tests/
  unit/modules/<module>/         # Aturan bisnis tanpa HTTP/database nyata
  integration/modules/<module>/  # Repository dan HTTP dengan DB sementara
  migrations/                    # Upgrade, preservation data, rollback bila didukung
  mcp/                           # Kontrak protokol, akses, dan perilaku MCP
```

| Kebutuhan | Tempat dan batas tanggung jawab |
| --- | --- |
| Endpoint domain baru | `app/modules/<module>/router.py`, didaftarkan di app factory |
| Request/response HTTP | `app/modules/<module>/schemas.py` |
| Aturan bisnis, eligibility, aging | `service.py` modul pemilik |
| SELECT/INSERT/UPDATE/DELETE | `repository.py` modul pemilik |
| Koneksi/session database | `app/db/connection.py` |
| Perubahan schema/backfill | `migrations/versions/` |
| Data contoh | `app/seed.py` atau helper seed terkait; bukan migration |
| SDK/provider eksternal | `app/integrations/<provider>/` |
| MCP server/tool/resource/prompt | `app/mcp/`; memanggil service domain, bukan SQL langsung |
| Client ke server MCP eksternal | `app/integrations/mcp/`; terpisah dari MCP server sendiri |
| Konfigurasi | `app/config.py` dan `.env.example` |
| Test existing bootstrap | Pertahankan `tests/test_api.py` dan `tests/test_store.py` sebagai suite PostgreSQL yang sudah dimigrasikan |

## Batas modul dan tanggung jawab

- Alur dependency: `router -> service -> repository -> db`. Schema digunakan
  pada batas input/output. `main.py` hanya merangkai aplikasi, bukan tempat
  menambah query atau workflow bisnis baru.
- Router menangani HTTP, parsing parameter, injection, dan mapping error.
  Service tidak bergantung pada `Request`, `Response`, atau `HTTPException`;
  error domain dipetakan ke status HTTP pada boundary.
- Repository menangani persistence, bukan keputusan boleh/tidaknya melakukan
  collection. Jangan menyimpan aturan bisnis dalam SQL helper atau route.
- Shared/db tidak mengimpor modul domain. Modul tidak mengimpor router atau
  repository internal modul lain; gunakan operasi service yang diekspor secara
  sengaja. Orkestrasi beberapa domain dapat memakai `app/workflows/`.
- Hindari circular import. Jangan membuat generic repository/base service atau
  interface tambahan tanpa kebutuhan konkret. Satu modul kecil tidak harus
  mempunyai semua file contoh di atas.
- Tentukan satu pemilik transaksi untuk satu use case. Semua write yang harus
  atomik memakai connection/session yang sama; jangan commit sebagian di setiap
  repository bila use case membutuhkan transaksi bersama.
- SQL accounts berada di repository dan aging di service. Pertahankan satu
  implementasi aktif dan perbarui seed, tests, serta consumer ketika berubah.
- Adapter MCP mengikuti `mcp tool/resource -> service -> repository -> db`.
  Modul domain tidak mengimpor SDK, session, atau transport MCP.

## Python, API, dan data

- Nama modul/fungsi/variabel `snake_case`, class `PascalCase`, konstanta
  `UPPER_SNAKE_CASE`. Gunakan type hint pada fungsi baru dan batas publik.
  Ikuti Ruff dan batas panjang baris 100 pada `pyproject.toml`.
- `psycopg` sinkron adalah blocking I/O; gunakan handler sinkron `def` untuk jalur ini.
  Jangan menambahkan blocking database/network call langsung ke event loop
  `async def`. Pakai async hanya dengan dependency async atau offloading yang benar.
- Gunakan parameterized query, constraint database, dan validasi Pydantic.
  Jangan interpolasi input ke SQL, mempercayai keputusan frontend, atau
  mengembalikan row/ORM object tanpa response schema yang eksplisit.
- Endpoint domain berada di `/api/v1`; `/health` tetap endpoint operasional.
  Pertahankan HTTP 404 untuk resource tidak ditemukan dan 422 untuk input tidak
  valid sesuai kontrak saat ini. Jangan membocorkan exception/SQL ke client.
- List accounts mempertahankan `{items, total, limit, offset}`, limit 1–100,
  offset nonnegatif, serta urutan `due_date` lalu `id`. Search/filter dilakukan
  sebelum pagination. Total dashboard meliputi seluruh portfolio.
- Pydantic dan OpenAPI FastAPI adalah sumber kontrak. Perubahan field, enum,
  status, atau error harus memperbarui tests, README, serta types/client frontend
  (saat ini `src/lib/api.ts`). Jangan membuat spec manual yang dibiarkan berbeda.
- Nominal adalah integer IDR nonnegatif sesuai domain. Due date adalah tanggal
  kalender; aging menggunakan `Asia/Jakarta`. Event timestamp baru harus timezone
  aware, disimpan sebagai UTC dan dikonversi eksplisit saat ditampilkan.
- Clock dapat diinjeksi untuk menguji batas tanggal/jam. Jangan bergantung pada
  tanggal mesin nyata untuk hasil test yang deterministik.
- Konfigurasi lewat Settings; jangan membaca env/hardcode credential di banyak
  modul. Jangan commit database, `.env`, token, atau data peminjam nyata.
- Demo memakai data sintetis dan channel simulasi. Jika fitur collection/AI
  ditambahkan, backend wajib menegakkan gate deterministik sebelum generation
  dan saat dispatch; fail closed ketika pemeriksaan wajib gagal. Panggilan
  provider dipisah dalam adapter dengan timeout dan penanganan failure.
- Jangan menjalankan retry otomatis pada write/pengiriman tanpa strategi
  idempotency. Audit yang append-only tidak boleh diubah oleh CRUD biasa.

## MCP — Model Context Protocol

### Scope dan arsitektur

Server MCP sekarang di `app/mcp/`, menggunakan SDK Python Streamable HTTP dan
principal bearer ber-expiry. Panduan operasional aktual ada di
`docs/integrations/hermes-mcp.md`. REST dan MCP memakai public facade
`app/modules/collection/service.py`; facade tetap meneruskan operasi ke domain pemilik.

- **MCP server** menyediakan kemampuan backend ke agent/client melalui
  `app/mcp/`. **MCP client** mengonsumsi server eksternal melalui
  `app/integrations/mcp/`. Jangan mencampur kedua lifecycle dan credential.
- Gunakan SDK MCP Python yang terpelihara dengan versi di manifest/lockfile.
  Jangan membuat implementasi JSON-RPC/handshake sendiri jika SDK sudah menangani.
- REST dan MCP adalah dua adapter menuju service yang sama. Tool tidak memanggil
  router FastAPI, tidak melakukan HTTP ke backend sendiri, dan tidak mengakses
  repository/PostgreSQL langsung. Jika use case belum tersedia, buat service domain
  dahulu, lalu hubungkan adapter yang diperlukan.
- `app/mcp/server.py` hanya merangkai server, dependency, dan registrasi. Pisahkan
  tool per domain, misalnya `tools/accounts.py`, `tools/compliance.py`, dan
  `tools/conversations.py`; hindari satu file berisi semua tool dan aturan bisnis.
- Lifecycle koneksi dan session ditangani saat startup/shutdown. Jangan membuka
  server/network connection saat import modul. Tetapkan timeout, batas concurrency,
  dan cleanup saat cancellation; blocking psycopg tetap harus di-offload dengan benar.

### Tools, resources, dan prompts

| Capability | Gunakan untuk | Penempatan contoh |
| --- | --- | --- |
| Tool | Operasi eksplisit: pencarian, evaluasi aturan, atau write yang diizinkan | `app/mcp/tools/accounts.py` |
| Resource | Konteks read-only dengan URI stabil dan MIME type yang jelas | `app/mcp/resources/policies.py` |
| Prompt | Template instruksi/percakapan yang dipilih client | `app/mcp/prompts/review_account.py` |
| Schema | Validasi input/output MCP dan mapping ke/dari service | `app/mcp/schemas/accounts.py` |

- Resource read tidak boleh mengirim pesan, mengubah status kasus, atau menulis
  data domain. Prompt tidak menjadi pemilik policy, credential, atau mekanisme
  otorisasi. Eligibility dan keputusan dispatch tetap di service backend.
- Beri tool nama `snake_case` berbentuk aksi dan objek, misalnya `get_account`,
  `list_accounts`, `evaluate_contact_policy`, atau `simulate_dispatch`.
  Gunakan nama simulasi yang eksplisit bila belum mendukung pengiriman nyata.
- Deskripsi tool menjelaskan tujuan, kapan digunakan, parameter wajib, satuan,
  efek samping, serta batasannya. Hindari tool generik seperti `execute_sql`,
  `run_command`, atau `call_any_url` yang membuka akses melampaui use case.
- Definisikan input tervalidasi: tipe, enum, panjang string, batas pagination,
  serta ID wajib. Tolak field tak dikenal bila kontrak tidak membutuhkannya.
  Tenant, identitas caller, role, dan persetujuan bukan klaim tepercaya dari
  argument model; turunkan atau verifikasi dari context server yang sah.
- Gunakan structured output dengan schema eksplisit, selaras dengan
  `outputSchema`/`structuredContent` SDK dan versi protokol yang dipakai.
  Return hanya data yang diperlukan; list harus dibatasi dan punya pagination,
  resource besar harus dipotong/dipaginasi sesuai kontrak. Jangan mengirim seluruh
  database/riwayat percakapan sebagai satu hasil tool.
- Model domain dapat digunakan ulang, tetapi DTO MCP milik adapter. Jika mapping
  diperlukan, buat eksplisit. OpenAPI REST tidak otomatis menjadi kontrak MCP;
  schema yang diumumkan saat discovery harus cocok dengan payload runtime.
- Isi annotation seperti `readOnlyHint`, `destructiveHint`, dan `idempotentHint`
  sesuai perilaku aktual SDK. Annotation membantu client dan **bukan** pengganti
  validasi, otorisasi, atau idempotency yang ditegakkan server.
- Error domain dalam eksekusi tool dilaporkan sebagai tool error (`isError`
  sesuai SDK) dengan kode stabil, pesan aman, serta petunjuk retry bila relevan.
  Error protokol tetap mengikuti SDK/MCP. Jangan mengembalikan HTTPException,
  stack trace, SQL, credential, atau status sukses untuk operasi yang gagal.
- Perubahan nama tool, argument wajib, enum, atau output adalah perubahan kontrak:
  update tests, dokumentasi, dan consumer agent. Pertahankan kompatibilitas atau
  sediakan masa transisi yang jelas sebelum menghapus capability lama.

### Transport, otorisasi, dan efek samping

- Pilih transport berdasarkan consumer: `stdio` untuk proses lokal; Streamable
  HTTP untuk akses remote. Jangan menyalakan keduanya tanpa kebutuhan. Dokumentasi
  harus menyebut transport, versi protokol/SDK, cara start, konfigurasi, dan contoh
  koneksi yang benar-benar diuji; jangan menambahkan command fiktif ke README.
- Pada `stdio`, stdout khusus pesan protokol; log ke stderr. Pada HTTP, mount
  path MCP yang jelas (misalnya `/mcp`) terpisah dari route REST `/api/v1` dan
  kelola lifespan/session manager sesuai SDK. Validasi Origin untuk mencegah
  DNS rebinding; CORS sendiri bukan autentikasi.
- Demo lokal tanpa autentikasi harus eksplisit, memakai data sintetis dan binding
  lokal. Remote/shared wajib mengautentikasi caller dan memeriksa otorisasi tiap
  tool/resource, termasuk ownership/tenant pada setiap ID. Session ID MCP bukan
  bukti identitas. Jangan menganggap client/model tepercaya karena sudah terkoneksi.
- Jika memakai bearer/OAuth, verifikasi token untuk resource MCP ini, termasuk
  issuer, audience, expiry, dan scope yang sesuai. Jangan meneruskan token caller
  mentah ke provider lain atau memakai credential server untuk melewati scope caller.
- Tool read-only dan write mempunyai izin terpisah; pembatasan discovery saja
  tidak cukup. Periksa izin saat eksekusi, termasuk pembacaan audit/resource sensitif.
- Tool write memakai transaksi dan mekanisme idempotency service yang sama dengan
  REST. Retry harus memakai key yang sama untuk operasi logis yang sama dan
  tidak boleh menghasilkan write/pengiriman ganda.
- Bila suatu aksi membutuhkan persetujuan manusia menurut workflow, backend
  memverifikasi record/token persetujuan yang terikat aksi, target, dan masa
  berlaku. Boolean `approved=true` dari model tidak membuktikan persetujuan.
- Tool collection tetap melewati eligibility, waktu kontak, verified recipient,
  guardrail, dan dispatch gate. Dilarang menyediakan jalur tool yang melompati
  kontrol tersebut; demo tetap menggunakan channel simulasi.
- Catat caller, nama tool, target ID, correlation ID, hasil, dan durasi secara
  aman. Prompt/resource/tool result merupakan data yang dapat mengandung prompt
  injection; isinya tidak dapat mengubah izin atau menonaktifkan policy backend.

### Backend sebagai MCP client

- Daftar server, URL/command, transport, tool yang diizinkan, dan credential
  dikonfigurasi backend. Jangan menerima URL server, command executable, atau
  credential arbitrary dari frontend/model; validasi tujuan untuk mencegah SSRF
  dan eksekusi proses tanpa batas. Konfigurasi lokal admin tetap harus eksplisit.
- Batasi tool berdasarkan kebutuhan use case, ukuran hasil, timeout, retry,
  dan concurrency. Perlakukan output server eksternal sebagai input tak tepercaya;
  validasi sebelum dipakai oleh service atau model.
- Tool eksternal yang melakukan write tetap tunduk pada policy dan idempotency
  aplikasi. Putusnya koneksi tidak membuktikan aksi belum terjadi; jangan retry
  write secara buta. Tutup session/subprocess saat selesai atau shutdown.
- SDK MCP hanya berada pada adapter. Service bergantung pada interface operasi
  domain yang diperlukan; jangan menyebarkan objek client/protocol ke semua modul.

### Menambah dan menguji capability MCP

1. Pilih tool/resource/prompt yang sesuai dan service domain pemilik. Tentukan
   schema, izin, efek samping, error, serta batas hasil sebelum registrasi.
2. Tambahkan adapter per domain dan registrasikan secara eksplisit di server.
   Konfigurasi baru masuk Settings, `.env.example`, dan README tanpa secret.
3. Tambahkan tests di `tests/mcp/` menggunakan client/transport SDK: initialize,
   discovery capability yang didukung, eksekusi/read/get sesuai jenis capability,
   validasi schema, hasil, dan error. Tidak cukup hanya memanggil fungsi Python.
4. Uji unauthorized/cross-tenant access, input invalid, limit hasil, timeout,
   cancellation, serta failure dependency. Write juga diuji untuk retry duplikat,
   rollback, dan penolakan saat gate/approval gagal. Resource/prompt tidak boleh
   diam-diam melakukan write domain.
5. Gunakan DB sementara dan server MCP uji deterministik. Verifikasi REST dan
   MCP tetap mengikuti aturan bisnis yang sama. Jika kedua transport didukung,
   uji keduanya; cek stdout stdio bersih dan isolasi session HTTP.
6. Test MCP masuk suite pytest/CI setelah implementasi tersedia. Laporkan
   transport/capability yang diuji; unit test adapter tidak membuktikan handshake,
   koneksi ke server eksternal, atau integrasi agent secara penuh.

## Migration database

### Kondisi sekarang

Alembic tersedia dan menjadi satu-satunya pemilik DDL. Baseline
`migrations/versions/0001_accounts.py` membuat tabel accounts PostgreSQL dengan
PK, UNIQUE loan reference, CHECK nominal nonnegatif, tipe BIGINT IDR, DATE,
dan index `(due_date, id)`. Revision `0002_collection_state` menambah case, policy, session, run/job/event,
knowledge snapshot, message/approval, review, receipt, dan hash-chain audit tanpa mengubah
field portfolio. Data demo tidak dimasukkan migration; `app.seed` memprovisikan
recipient sintetis serta policy fixture secara eksplisit.

Koneksi runtime memakai psycopg melalui Settings
`DATABASE_URL`. Startup dan seed tidak membuat atau memperbarui schema.
`/health` memeriksa liveness, sedangkan `/ready` memeriksa koneksi dan revision
schema. Perbarui `REQUIRED_REVISION` di `app/db/connection.py` saat head berubah.

Jalankan migration eksplisit sebelum API dan seed. Compose memakai service
`migrate` one-shot setelah PostgreSQL sehat dan sebelum backend dimulai.
`uv run alembic upgrade head` tersedia untuk host. SQLAlchemy hanya dipakai
infrastruktur migration. Revision ditulis eksplisit karena tidak ada metadata
ORM runtime untuk `--autogenerate`.

File SQLite lama tidak dihapus atau dipakai sebagai database runtime.
`uv run python -m app.db.import_sqlite /absolute/path/to/snapshot.db` membaca
snapshot legacy secara read-only dan mengimpor ke PostgreSQL yang sudah dimigrasi.
Importer memvalidasi data, idempotent untuk row identik, serta rollback seluruh
transaksi bila ID/reference bentrok. Siapkan snapshot konsisten memakai backup
API SQLite atau hentikan penulis, lalu verifikasi jumlah/field penting pada
tujuan. Jangan menjalankan `stamp head` terhadap schema yang belum sesuai.

### Aturan setiap migration

- Semua migration ada di `migrations/versions/`, bukan disisipkan dalam router,
  service, seed, atau file SQL tanpa riwayat. Beri nama deskriptif, misalnya
  `<revision>_add_account_contact_status.py`, dengan parent revision yang benar.
- Revision yang sudah dibagikan/diterapkan tidak diedit atau dihapus; koreksi
  dengan revision baru. Usahakan satu head; bila branch paralel membuat beberapa
  head, selesaikan dengan merge revision setelah dependency diverifikasi.
- Gunakan pola expand/backfill/contract: tambah kolom kompatibel, isi data lama,
  pindahkan consumer, baru hapus kolom lama pada perubahan terpisah. Tetapkan
  default/nullability untuk row existing dan pertimbangkan durasi lock/backfill.
- Tinjau lock, durasi backfill, index, dan constraint PostgreSQL saat migration
  menyentuh tabel besar. Jangan mengasumsikan operasi seperti CREATE INDEX
  CONCURRENTLY dapat berjalan dalam transaksi biasa.
- Migration data hanya untuk transformasi data aplikasi yang diperlukan versi
  schema. Demo seed tetap eksplisit dan idempotent; jangan memasukkan borrower
  demo ke migration produksi atau menjalankan seed otomatis saat startup.
- Uji database kosong -> head, salinan database versi lama -> head dengan data
  tetap utuh, dan aplikasi terhadap schema baru. Verifikasi jumlah row, constraint,
  serta field penting, bukan hanya exit code migration.
- Tulis `downgrade` bila aman. Jika perubahan kehilangan data/tidak dapat dibalik,
  nyatakan jelas dalam revision dan README, beserta recovery/forward fix. Jangan
  menjanjikan downgrade dapat memulihkan data yang sudah dihapus.
- Sebelum menerapkan pada database berisi data, siapkan backup konsisten dan
  rencana restore. Untuk SQLite aktif gunakan backup API SQLite atau hentikan
  penulis dengan prosedur konsisten; jangan sekadar menyalin file `.db` aktif
  tanpa memperhitungkan WAL. Verifikasi restore pada salinan sementara.
- Operasi destruktif pada data shared/nyata memerlukan otorisasi eksplisit untuk
  target dan dampaknya. Persiapan revision dan pengujian pada DB sementara tetap
  dapat dilakukan. Jangan pernah menghapus DB lokal sebagai pengganti migration.

Command berikut tersedia dari root repo. Konfigurasi harus mengarah ke
database yang dimaksud:

```sh
uv run alembic revision -m "add account contact status"  # Lalu isi/review operasi
uv run alembic heads
uv run alembic current
uv run alembic upgrade head
```

Jalankan downgrade hanya pada target yang tepat setelah memeriksa reversibility
dan backup. Deploy menjalankan revision yang sudah di-commit, bukan membuat
revision baru di environment tujuan.

## Menambah modul atau endpoint

1. Tentukan domain pemilik dan kontrak request/response. Periksa implementasi
   existing agar tidak membuat modul kedua untuk domain yang sama.
2. Tambahkan file yang diperlukan di `app/modules/<module>/`. Pisahkan router,
   aturan bisnis, dan SQL sesuai batas di atas; registrasikan router di app factory.
3. Jika schema berubah, sertakan migration beserta jalur database existing,
   bukan hanya mengganti schema bootstrap.
4. Tambahkan test untuk aturan bisnis, validasi/error HTTP, dan persistence yang
   berubah. Write baru juga harus menguji transaksi/constraint dan idempotency
   jika relevan. Sesuaikan CORS method secara spesifik bila write diperkenalkan.
5. Sinkronkan kontrak frontend, README, `.env.example`, serta fixture/seed bila
   perlu. Jangan mengaktifkan kontak nyata sebagai bagian dari demo.

## Verifikasi dan dependency

Jalankan dari root backend:

```sh
uv sync --locked
uv run ruff check .
docker compose --profile test up -d --wait postgres-test
TEST_DATABASE_URL=postgresql://collectflow_test:local-test-password@127.0.0.1:5433/collectflow_test \
  uv run pytest
```

Tests memakai PostgreSQL nyata pada database khusus berakhiran `_test`, dengan
schema acak terisolasi per test, migration nyata, data sintetis, serta clock
aturan aging deterministik. Jangan arahkan tests ke DB developer atau shared.
CI menyediakan service PostgreSQL sendiri. Bila port test diganti, sesuaikan
URL test. Hentikan service test setelah dipakai dengan
`docker compose --profile test stop postgres-test`.

Perubahan dokumentasi saja cukup diverifikasi terhadap file dan konfigurasi
aktual. Laporkan keterbatasan Docker/DB bila checks tidak dapat dijalankan.

Gunakan uv; update manifest dan `uv.lock` bersama saat dependency berubah.
Jangan commit `.venv`, cache, file database, atau backup. Sebelum menyatakan
selesai, laporkan checks yang benar-benar dijalankan dan keterbatasan yang masih
ada; test unit tidak membuktikan integrasi eksternal atau migration nyata.
