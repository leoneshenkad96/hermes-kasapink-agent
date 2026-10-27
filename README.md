# Hermes Bossun — Personal Operations Assistant

Scaffold lokal untuk Hermes Agent di Windows. Tujuannya:

- mengambil snapshot read-only dari ERP Henka dan router MikroTik;
- membandingkan snapshot sekarang dengan baseline lokal;
- mengirim hanya perubahan/error yang relevan ke Hermes untuk diringkas ke Telegram atau Discord;
- menerima perintah interaktif, tetapi tetap meminta approval sebelum perubahan konfigurasi/data.

## Status saat ini

Yang sudah dibuat:

- `monitor.py`: collector dan baseline/diff engine, default dry-run; live mode login ERP memakai session cookie lalu membaca `/api/healthz` dan `/api/erp/state`;
- `AGENTS.md`: kebijakan operasi aman untuk sesi Hermes;
- `.env.example`: daftar variabel yang perlu diisi, tanpa secret nyata;
- `hermes-cron-prompt.md`: prompt cron 30-menit yang siap ditinjau;
- `config-snippet.yaml`: snippet konfigurasi toolset least-privilege.
- Core Gateway: tunnel WireGuard Hermes aktif pada `10.10.10.4/32`; API RouterOS read-only dibatasi ke alamat VPN tersebut.
- WireGuard Manager dan tunnel `hermes-laptop` berjalan sebagai `LocalSystem`/Automatic; task `Hermes WireGuard At Startup` menyalakan ulang tunnel dan retry saat boot belum siap.
- `routeros_probe.py`: probe API RouterOS read-only melalui tunnel, tanpa menampilkan kredensial.
- `discord_bot.py`: Discord command layer (`/status`, `/check`, `/diff`, `/command`, `/help`), chat natural, audit log lokal, alert monitoring tiap 30 menit, dan heartbeat idle tiap 3 jam tanpa perubahan (bisa diatur lewat `HERMES_IDLE_ALERT_MINUTES`).
- `access_policy.py`: akses bersama untuk fitur Hermes umum; status/diff/audit yang mencakup MikroTik dibatasi ke pemilik bot.
- Hak akses dipisah: allowlist boleh chat/baca; MikroTik hanya owner; tulis ERP dan aksi sistem laptop hanya ID di `DISCORD_ERP_OPERATOR_IDS` / `DISCORD_LOCAL_ACTION_OPERATOR_IDS` (default owner saja).
- `erp_auto_policy.py`: kebijakan auto-entry ERP bernilai rendah; hanya entri bahan baru yang lengkap dan unik, belanja/penjualan hari ini yang cocok katalog, semuanya di bawah batas Rp100.000. Produk/harga, resep, opname, tanggal lama, data ambigu, dan transaksi lebih besar tetap memerlukan approval.
- `repo_context.py`: pencarian read-only pada snapshot lokal repo ERP Henka untuk memberi konteks jawaban berbasis source/documentation.
- `approval.py`: proposal perubahan ERP satu kali pakai, TTL 10 menit, audit status, dan executor operator yang dibatasi allowlist.
- `/approval-status`: status proposal privat milik peminta; hasil `UNKNOWN` dikunci dari pengulangan dan harus direkonsiliasi read-only. Rollback ERP tidak otomatis karena endpoint kompensasi berbeda per transaksi.
- `history_store.py`: riwayat snapshot SQLite lokal dengan retention yang dapat diatur.
- `anomaly_rules.py`: rule engine deterministik untuk stok, biaya ERP, ketersediaan router, dan perubahan snapshot router.
- `correlation.py`: korelasi perubahan ERP dan jaringan pada siklus yang sama.
- `form_parser.py`: parser konservatif untuk template bahan/produk; field kosong tidak ditebak.
- `engineering_tasks.py`: handoff queue lokal untuk task yang akan dikerjakan Codex/ChatGPT.
- `change_gate.py`: approval gate merge/deploy tanpa menjalankan Git atau deployment secara diam-diam.
- `audit_summary.py`: ringkasan jumlah snapshot dan anomaly.
- `audit_dashboard.py`: generator dashboard HTML audit lokal.
- `local_runner.py`: lapisan akses laptop dengan diagnostik allowlist, timeout, redaksi rahasia, kill switch, serta kontrol terbatas ChatGPT/Codex Desktop.
- `local_gate.py`: approval satu kali untuk aksi lokal yang dibatasi; mendukung start/restart ChatGPT, restart Hermes, dan jadwal restart laptop 20 detik.
- `devops_adapters.py`: registry 15 domain DevOps, probe tool lokal read-only, readiness GitHub/Vercel/Neon tanpa menampilkan credential, termasuk validasi login CLI yang tersimpan di keyring, serta rencana workflow adapter/incident/programmer/DevOps.
- `external_adapters.py`: adapter read-only nyata untuk metadata repo/PR GitHub, project/deployment Vercel, dan project/branch Neon; mutasi tidak tersedia di adapter ini.
- `external_changes.py`: write adapter allowlist dengan proposal kadaluarsa, approval satu kali, audit, redaksi output, dan executor terbatas untuk GitHub, Git, Vercel preview/production, serta Neon branch.
- `memory_store.py`: persistent memory lokal SQLite dengan FTS5, scope user/channel/project, redaksi credential, short-term context 10–20 pesan, dan auto-summary percakapan.
- `identity.py`: pengenalan user Discord berbasis user ID/display name, registry user lokal, `/whoami`, dan `/users` owner-only.
- `business_advisor.py`: adapter ERP read-only untuk analisis harga/HPP/margin dan laporan finance/F&B; tidak memiliki executor write.
- `knowledge_base.py`: runbook terkurasi untuk Python, PowerShell/Windows, Linux/TCP-IP, RouterOS, Docker/Compose, Kubernetes/proxy/TLS, database, backup/DR, CI/CD/Git, dan observability/incident response.
- `knowledge_base.py`: juga memuat pengetahuan F&B (operasi dapur, keamanan pangan, FIFO/FEFO, costing, menu engineering, inventory) dan parfum (notes, accord, keluarga aroma, rekomendasi, layering, formulasi/QC/safety).
- `official_sources.py` + `data/knowledge/official_sources.json`: Official Knowledge Source Layer untuk citation sumber WHO/FAO/BPOM/IFRA serta dokumentasi resmi Docker, Kubernetes, Python, PowerShell, Git, GitHub, Vercel, Neon, PostgreSQL, MySQL, MikroTik, dan OpenTelemetry; lengkap dengan versioning manifest, tanggal verifikasi, jadwal review, dan pemisahan antara panduan umum versus status regulasi terkini.
- `curated_knowledge.py` + `data/knowledge/user_curated_sources.json`: peta belajar dari materi yang diberikan owner, mencakup DevOps, network, MikroTik, server/database, IT support, programming, F&B, dan parfum; setiap sumber diberi tier primer, sekunder, atau komunitas.

Yang belum dilakukan dengan sengaja:

- tidak menggunakan Hermes cron; loop monitor dijalankan di dalam bot Discord yang diluncurkan Windows Task Scheduler saat boot;
- tidak mengubah konfigurasi Hermes global selain parameter koneksi lokal yang diperlukan;
- collector live dikonfigurasi memakai `.env` lokal; pemeriksaan read-only terakhir menunjukkan ERP Henka, Core Gateway, LAN Controller, Groq, dan Google AI Studio tersambung;
- scheduler berjalan sebagai Windows Scheduled Task `Hermes Kasapink Discord Bot`;
- launcher bot melakukan retry 30 detik bila Windows sudah boot tetapi DNS/internet belum siap;
- perubahan ERP hanya bisa melalui `/propose` lalu `/approve ID`; executor mengizinkan bahan, produk, pembelian, penjualan, stok opname, dan penggantian resep pada endpoint allowlist. DELETE, manajemen user, serta konfigurasi MikroTik tetap dikunci;
- history menyimpan snapshot lokal selama retention yang dikonfigurasi; anomaly report menyertakan severity dan alasan before/after tanpa mengandalkan tebakan AI;
- daily digest memuat maksimal lima anomaly tertinggi dari 24 jam terakhir; rule router juga memberi alert saat CPU/RAM melewati ambang dan saat ERP/router berubah dari OK;
- GET monitoring mencoba ulang kegagalan jaringan/HTTP sementara secara terbatas (maksimal empat percobaan); login POST dan ERP write tidak di-retry otomatis;
- task engineering tersimpan di `data/engineering/tasks/` agar bisa dibuka dari workspace oleh Codex/ChatGPT;
- merge/deploy memiliki change approval terpisah; eksekusi tetap dilakukan setelah review diff, checks, dan rollback;
- snapshot repo ERP disalin ke `data/kasapink-repo/` untuk konteks lokal; file rahasia/biner tidak dipakai sebagai konteks;
- tidak melakukan deployment atau perubahan konfigurasi/data.
- akses laptop tidak menyediakan shell bebas, pembacaan password/cookie, atau kontrol aplikasi arbitrer; tindakan lokal harus masuk allowlist dan melewati approval.

## Prasyarat koneksi

Scaffold ini membutuhkan detail yang belum tersedia di workspace:

1. URL endpoint health/read-only ERP Henka dan cara autentikasinya;
2. URL MikroTik REST API atau alternatif SSH/API yang diizinkan; Core Gateway sudah memiliki jalur API RouterOS read-only melalui WireGuard;
3. channel Telegram atau Discord tujuan;
4. model/provider pilihan untuk Hermes.

Untuk free-tier, gunakan Groq sebagai model cepat untuk klasifikasi/ringkasan dan Google AI Studio/Gemini sebagai fallback atau audit yang membutuhkan konteks lebih panjang. Kuota aktual harus dilihat di dashboard akun karena berubah per model/proyek.

## Uji lokal yang aman

```powershell
python .\monitor.py --dry-run
```

Mode live baru akan aktif bila dipanggil eksplisit:

```powershell
python .\monitor.py --live
```

Perintah live collector hanya melakukan GET/read. Executor mutasi terpisah memakai akun `hermes-operator` dan hanya menerima proposal yang lolos allowlist serta approval eksplisit.

## Perintah Discord

Gunakan channel command dengan slash command:

```text
/status
/devops-status
/external-status
/external-propose service:github action:issue_create payload:{"title":"...","body":"..."}
/external-approve change_id:EXT-XXXXXXXX
/external-deny change_id:EXT-XXXXXXXX
/devops-plan stage:devops request:siapkan pipeline preview yang aman
/remember content:"User lebih suka laporan ringkas" scope:user
/remember content:"ERP Kasapink memakai project ini" scope:project project_key:kasapink-erp
/memory query:ERP project_key:kasapink-erp
/forget memory_id:MEM-XXXXXXXXXX
/whoami
/users
/business question:"Jual nasi bakar Rp15.000 masih untung nggak?"
/business-report start_date:2026-10-19 end_date:2026-10-25
/fnb question:"Cara mengurangi food waste di dapur?"
/parfum question:"Notes apa yang cocok untuk parfum siang di cuaca panas?"
/sources topic:"F&B|food safety|parfum|IFRA|BPOM"
/knowledge-sources topic:"Docker|MikroTik|Python|F&B|parfum"
/check target:all
/check target:core
/check target:lan
/check target:erp
/diff
/command text:cek Core Gateway
/propose method:POST endpoint:/api/erp/ingredients body:{"name":"Tepung","category":"Bahan","stockType":"count","unit":"kg","minStock":1}
/approve approval_id:HMR-XXXXXXXX
/deny approval_id:HMR-XXXXXXXX
/approval-status approval_id:HMR-XXXXXXXX
/eng-task title:... description:... evidence:...
/eng-list
/audit
/audit-dashboard
/change-propose target:staging action:merge reference:branch/... summary:...
/change-approve approval_id:CHG-XXXXXXXX
/laptop-check target:system
/laptop-check target:disk
/laptop-check target:processes
/laptop-check target:network
/laptop-check target:wireguard
/laptop-check target:hermes
/laptop-check target:chatgpt
/laptop-propose action:start_chatgpt
/laptop-propose action:restart_chatgpt
/laptop-propose action:restart_hermes
/laptop-propose action:restart_laptop
/approved approval_id:LAP-XXXXXXXX
/laptop-approve approval_id:LAP-XXXXXXXX
```

Bot hanya menerima command channel dari user yang diizinkan. Proposal belum mengubah ERP; perubahan baru dikirim setelah `/approve ID`. ID hanya dapat dipakai sekali oleh pembuat proposal dan kedaluwarsa setelah 10 menit.

### Persistent memory

- Short-term context menyimpan maksimal 20 pesan terakhir per user/channel di RAM untuk percakapan aktif.
- `/remember` menyimpan fakta eksplisit ke `data/memory.db`; scope yang tersedia adalah `user`, `channel`, dan `project`.
- `/memory` mencari memory yang boleh diakses user tersebut dan menampilkan ringkasan percakapan lokal.
- `/forget` melakukan soft-delete terhadap memory milik user; memory tidak dapat dihapus user lain.
- Auto-summary berjalan setiap 20 turn percakapan natural dan disimpan secara lokal. Jika provider AI tidak tersedia, Hermes memakai fallback ringkasan deterministik.
- Redaksi otomatis menyamarkan Bearer/GitHub/Slack token, password/secret/API key, private key, dan URL PostgreSQL/MySQL sebelum disimpan atau diringkas.
- Isi `data/memory.db` bersifat sensitif: backup, retention, permission folder, dan penghapusan permanen perlu dikelola sebagai tahap hardening berikutnya.

### Identitas user Discord

- Hermes otomatis mengenali user yang diizinkan dari Discord user ID, display name, dan username; ID tidak ditampilkan dalam jawaban biasa.
- Registry user disimpan lokal di tabel `user_profiles` pada `data/memory.db`, termasuk waktu terakhir command dan jumlah command.
- `/whoami` menjawab siapa yang sedang berbicara dengan Hermes.
- `/users` hanya bisa dibuka owner dan menampilkan user yang sudah command ke Hermes.
- Akses tetap mengikuti `DISCORD_ALLOWED_USER_ID` dan `DISCORD_ADDITIONAL_USER_IDS`; pengenalan nama tidak otomatis membuka akses baru.
- Jika ingin label tetap seperti `Hensa`, `Pinky`, atau `Nita`, isi `HERMES_USER_NAMES` dengan JSON user ID-to-name setelah ID masing-masing diketahui.

Perintah bahasa natural sebagai pesan biasa memerlukan `Message Content Intent` pada Discord Developer Portal. Tanpa itu, gunakan `/command`, yang sudah aktif tanpa privileged intent.

Untuk akses laptop, pesan seperti `cek kesehatan laptop`, `cek ChatGPT`, `cek wireguard`, `cek proses`, `cek jaringan laptop`, `cek disk laptop`, dan `cek Hermes di laptop` dipetakan ke diagnostik yang tetap. Hasilnya read-only dan tidak menampilkan nilai secret. Jika perlu menyalakan atau memulihkan ChatGPT/Codex, restart bot, atau restart laptop, tinjau ID approval lalu gunakan `/approved approval_id:...`. ChatGPT dijalankan melalui task `ChatGPT At User Logon`, sehingga tetap otomatis dibuka setelah user login Windows. Restart laptop dijadwalkan 20 detik setelah approval.

Restart laptop dijadwalkan 20 detik setelah approval; Hermes tidak menerima command shutdown mentah atau parameter dari chat. Kill switch lokal: buat file `data/LOCAL_ACTIONS_DISABLED` atau set `HERMES_LOCAL_ACTIONS_ENABLED=false` di `.env` untuk menonaktifkan semua lapisan aksi lokal. Diagnostik juga akan berhenti saat kill switch aktif.

Pesan template yang diisi dengan format `Field: nilai` dapat diproses untuk bahan, produk, pembelian, penjualan, stok opname, dan resep. Untuk transaksi yang merujuk data lama, Hermes mencocokkan nama secara persis terhadap katalog ERP terbaru; nama yang tidak ditemukan atau ambigu harus diganti dengan ID. Hermes membuat approval proposal dan tidak menebak field kosong. Jika koneksi terputus saat mengirim perubahan, hasil ditandai `UNKNOWN`; jangan kirim ulang sebelum memeriksa ERP secara read-only karena server mungkin sudah menerima perubahan.

Rekap otomatis ERP/MikroTik dijadwalkan setiap hari pukul **06.00 WIB** ke `DISCORD_ALERT_CHANNEL` (channel alert), tanpa data saham. Isinya status ERP/Core Gateway/LAN Controller, ringkasan omzet/HPP/laba kotor/pembelian hari ERP, jumlah stok di/bawah minimum, serta ringkasan resource router yang aman untuk kanal. Status delivery disimpan lokal agar reconnect tidak mengirim ulang rekap pada hari yang sama.

Untuk data historis saham IDX, buat API key gratis Api.co.id lalu set `API_CO_ID_KEY` pada `.env` lokal. Gunakan `/saham ASII` atau `cek saham ASII`; Hermes juga memahami `rata-rata saham ASII setahun` serta perbandingan tanggal ISO (`2025-01-01`), tanggal Indonesia (`10 September 2026`), dan `DD/MM/YYYY`. Rata-rata memakai harga penutupan setiap sesi bursa pada periode yang tersedia; perbandingan memakai sesi bursa terakhir yang tersedia sampai tanggal tujuan. Free tier yang diiklankan provider memberi 500 hit per bulan. Rekap khusus `DISCORD_STOCK_CHANNEL` berisi sampel acak 5 ticker dan berjalan setelah sesi I (Senin–Kamis 12.05, Jumat 11.35 WIB) serta setelah penutupan sesi II (setiap hari bursa 16.20 WIB). Data sesi I bersifat sementara; laporan kedua memakai data setelah penutupan. Sampel saham diacak dari daftar IHSG yang di-cache mingguan; `HERMES_STOCK_WATCHLIST` bisa diisi untuk ticker tetap. Top 10 gainers/losers pasar penuh memerlukan endpoint Movers yang tercantum di paket Starter, bukan Free. Data historis/EOD, bukan real-time, dan bukan rekomendasi beli/jual personal.

Untuk riset produk e-commerce, set `BRAVE_SEARCH_API_KEY` di `.env` lokal. Hermes melakukan pencarian read-only yang dibatasi ke hasil Shopee, Tokopedia, Blibli, dan Lazada Indonesia, lalu meminta Groq/Gemini membandingkan snippet dan menyertakan tautan listing. Hasil indeks tidak menjamin harga/stok terbaru; verifikasi di halaman toko. Brave Search API mencantumkan harga $5 per 1.000 request dengan $5 kredit gratis bulanan pada halaman harga saat fitur ini dibangun; penggunaan setelah kredit habis dapat dikenai biaya. Fitur ini tidak checkout, membeli, menghubungi seller, atau mengubah keranjang. Tanpa API key, Hermes memberi tahu bahwa pencarian belum aktif. Trigger contoh: `Carikan tablet buat gambar, bandingkan harga dan speknya, lalu rekomendasikan yang paling cocok`.

Untuk engineering, Hermes tidak login ke akun ChatGPT Plus. Hermes membuat task lokal; Codex/ChatGPT membaca task dari workspace atau repo terhubung, membuat diff/branch, lalu lu meninjau dan menyetujui merge/deploy.

## Tahap berikutnya setelah detail akses diberikan

1. Isi `.env` dari `.env.example` menggunakan secret manager atau file lokal yang permission-nya ketat.
2. Jalankan collector live sekali untuk membuat baseline awal, lalu tinjau hasilnya.
3. Uji satu kali melalui `hermes cron run`/job sementara.
4. Setelah hasil benar, minta persetujuan untuk membuat cron `every 30m` dan memilih Telegram/Discord.
5. Uji perintah interaktif read-only seperti `status`, `diff`, dan `audit menu`.

