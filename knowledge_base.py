"""Compact technical runbooks selected by topic for Hermes conversations."""

from __future__ import annotations

import re


CARDS = {
    "programming": {
        "keywords": ("python", "javascript", "typescript", "coding", "program", "bug", "exception", "traceback", "git", "api", "database"),
        "text": """PROGRAMMING RUNBOOK
- Diagnose from the smallest reproducible case: input, expected result, actual result, traceback, environment, and recent change.
- Separate syntax, type/data, dependency, configuration, network, concurrency, and logic failures.
- Prefer a minimal patch, a regression test, and a rollback path. Never claim code was changed or tested without evidence.
- For Git: inspect status/diff, preserve unrelated user changes, make focused commits, and never reset or delete without explicit approval.""",
    },
    "it_support": {
        "keywords": ("windows", "laptop", "app", "application", "startup", "task scheduler", "powershell", "terminal", "ram", "cpu", "disk", "support"),
        "text": """IT SUPPORT RUNBOOK
- Triage in order: user impact, scope, recent change, process/service state, logs, CPU/RAM/disk, network, then recovery.
- Distinguish app failure, user-session failure, machine failure, and upstream network failure.
- Read-only checks first; restart only the named service/app with approval and record before/after evidence.
- Never expose credentials, tokens, full environment files, MAC addresses, or unnecessary PII in Discord.""",
    },
    "server": {
        "keywords": ("server", "service", "daemon", "port", "log", "http", "https", "database", "linux", "windows server", "uptime", "incident"),
        "text": """SERVER DIAGNOSTIC RUNBOOK
- Establish health: process/service, listening port, HTTP response, dependency reachability, disk, memory, CPU, and recent logs.
- Correlate timestamps before changing anything. A timeout can be DNS, routing, firewall, TLS, upstream, or application latency.
- Use bounded commands and timeouts; capture status, likely cause, evidence, action, and rollback.
- Recovery order: retry transient dependency, restart the affected service, validate health, then escalate. Avoid blind repeated restarts.""",
    },
    "networking": {
        "keywords": ("network", "jaringan", "mikrotik", "router", "dns", "dhcp", "nat", "route", "routing", "firewall", "wireguard", "latency", "packet loss", "isp", "gateway", "tcp", "udp"),
        "text": """NETWORKING RUNBOOK
- Check layers: link/interface, IP/DHCP, DNS, default route, gateway reachability, TCP port, TLS/HTTP, then application auth.
- Separate local connectivity, tunnel/WireGuard, ISP/upstream, router policy, and remote service failure.
- For MikroTik: read resource, interface, route, connection, and log state first; configuration changes require explicit approval and rollback.
- Report latency/loss with timestamp and target. Do not infer outage from one failed probe.""",
    },
    "devops": {
        "keywords": ("devops", "deploy", "deployment", "release", "ci", "cd", "pipeline", "docker", "container", "kubernetes", "k8s", "rollback", "monitoring", "observability", "backup", "secret", "infrastructure"),
        "text": """DEVOPS RUNBOOK
- Plan every change: objective, scope, precheck, exact action, health check, rollback, owner, and evidence.
- Prefer immutable/versioned artifacts, environment separation, least privilege, secret redaction, health probes, and idempotent scripts.
- Deploy flow: validate → test → stage → observe → promote; rollback on defined failure thresholds.
- Never treat a shell command, deployment, firewall change, secret rotation, or production mutation as read-only. Ask for approval immediately before execution.""",
    },
    "python_debugging": {
        "keywords": ("python", "traceback", "exception", "pytest", "venv", "dependency", "asyncio", "debugging"),
        "text": """PYTHON DEBUGGING RUNBOOK
- Capture Python version, interpreter path, package lock, command, input, traceback, and the smallest reproducible case.
- Classify the failure as syntax/import, type/data, dependency, configuration, async/concurrency, I/O, or logic before patching.
- Prefer a focused fix plus regression test; use bounded timeouts and never execute untrusted files merely to inspect them.""",
    },
    "powershell_windows": {
        "keywords": ("powershell", "windows internals", "event log", "service", "scheduled task", "registry", "wmi", "win32"),
        "text": """POWERSHELL AND WINDOWS RUNBOOK
- Read service/task/process/event-log state first; record name, status, start mode, exit code, timestamp, and dependencies.
- Use NoProfile, non-interactive, bounded commands and explicit allowlists. Avoid arbitrary script text from chat.
- Changes to services, registry, firewall, users, startup, or scheduled tasks require a named target, approval, backup, and rollback.""",
    },
    "linux_tcpip": {
        "keywords": ("linux", "systemd", "journalctl", "ssh", "tcp", "ip address", "subnet", "cidr", "routing", "iptables", "nftables"),
        "text": """LINUX AND TCP/IP RUNBOOK
- Linux triage: systemd unit, journal window, process, listening socket, disk/inode, memory, load, DNS, route, and dependency health.
- For subnetting: normalize CIDR, calculate network/broadcast/usable range, then verify route and ACL direction; do not infer reachability from address math.
- SSH is a transport, not permission to mutate. Use read-only commands, host-key validation, least privilege, and explicit approval for changes.""",
    },
    "routeros": {
        "keywords": ("mikrotik", "routeros", "router", "firewall", "nat", "dhcp", "wireguard"),
        "text": """MIKROTIK ROUTEROS RUNBOOK
- Read resources, interfaces, addresses, routes, DNS, DHCP, firewall counters, WireGuard peers, connections, and logs before forming a hypothesis.
- Separate link, IP, route, DNS, policy, tunnel, and application failures; include timestamp and probe target.
- Export/backup and rollback plan are mandatory before any RouterOS configuration change; current Hermes keeps this path read-only.""",
    },
    "containers": {
        "keywords": ("docker", "compose", "container", "image", "volume", "registry"),
        "text": """DOCKER AND COMPOSE RUNBOOK
- Inspect client/server version, context, compose config, container state, health, logs, ports, mounts, networks, and image digest.
- Treat compose up, pull, prune, volume removal, registry push, and host socket access as mutating or high-risk actions.
- Prefer pinned image tags/digests, non-root containers, resource limits, secrets outside images, healthchecks, and reversible rollout.""",
    },
    "kubernetes_proxy_tls": {
        "keywords": ("kubernetes", "k8s", "kubectl", "pod", "deployment", "ingress", "reverse proxy", "nginx", "caddy", "tls", "certificate", "cert-manager"),
        "text": """KUBERNETES, PROXY, AND TLS RUNBOOK
- Kubernetes diagnosis: context/namespace, nodes, workload rollout, pod events/logs, service endpoints, ingress, DNS, and resource pressure.
- Reverse proxy diagnosis: listener, upstream resolution, connect/read timeout, headers, websocket, access/error logs, and health endpoint.
- TLS diagnosis: certificate chain, hostname/SAN, expiry, key match, protocol/cipher, SNI, and renewal controller. Never disable verification as a fix.""",
    },
    "databases": {
        "keywords": ("postgresql", "postgres", "mysql", "database", "sql", "connection pool", "migration", "query"),
        "text": """DATABASE RUNBOOK
- Check endpoint/DNS/TLS/auth separately, then pool saturation, active sessions, locks, slow queries, replication, disk, and recent migration.
- Read-only inspection comes before SQL writes. Never print DATABASE_URL, passwords, tokens, or full rows into Discord.
- Migrations require backup/restore proof, transaction strategy, lock/timeout plan, compatibility check, and rollback or forward-fix.""",
    },
    "backup_dr": {
        "keywords": ("backup", "restore", "disaster recovery", "rpo", "rto", "snapshot", "retention", "restore test"),
        "text": """BACKUP AND DISASTER RECOVERY RUNBOOK
- Define asset, owner, RPO, RTO, retention, encryption, off-site copy, and failure scenario before declaring backup healthy.
- Verify freshness, size/change rate, integrity, access control, and a restore test; a successful backup job alone is not recovery evidence.
- Restore to an isolated target first. Production restore, deletion, retention changes, and key rotation require approval.""",
    },
    "cicd_git": {
        "keywords": ("ci/cd", "ci cd", "pipeline", "github actions", "git", "branch", "pull request", "commit", "deploy", "vercel"),
        "text": """CI/CD AND GIT RUNBOOK
- Git flow: inspect status/diff/log, preserve unrelated work, branch from known revision, test, review, then commit with evidence.
- CI flow: lint/test/security scan/build artifact, preview/stage, deployment checks, observe, then promote; pin action/tool versions.
- GitHub/Vercel access is least-privilege and read-only by default. Production deploy, merge, rollback, and secret changes require approval.""",
    },
    "observability_incident": {
        "keywords": ("observability", "metrics", "logs", "traces", "alert", "incident", "on-call", "latency", "sli", "slo", "postmortem"),
        "text": """OBSERVABILITY AND INCIDENT RUNBOOK
- Build a timeline from logs, metrics, traces, deploys, config changes, dependency health, and user impact; use UTC/WIB timestamps explicitly.
- Distinguish symptom, trigger, contributing factor, and root cause. Record confidence and missing evidence instead of guessing.
- Mitigate safely, verify SLI recovery, preserve evidence, communicate status, and create a blameless follow-up with prevention actions.""",
    },
    "fnb_operations": {
        "keywords": ("fnb", "f&b", "food and beverage", "makanan", "dapur", "menu", "sop dapur", "produksi makanan", "portion", "porsi", "food waste"),
        "text": """F&B OPERATIONS KNOWLEDGE
- Pisahkan fakta ERP, asumsi operasional, dan rekomendasi. Untuk Kasapink, F&B adalah lini aktif; parfum tidak boleh dicampur ke angka F&B.
- Alur operasi: forecast permintaan → rencana produksi → prep/batch → penjualan → pemakaian stok → waste → review margin.
- SOP dapur perlu mencakup kebersihan tangan/peralatan, pemisahan bahan mentah dan matang, kontrol suhu, label tanggal, porsi standar, dan pencatatan waste.
- FIFO berarti bahan yang lebih dulu masuk diprioritaskan; FEFO berarti yang lebih cepat kedaluwarsa diprioritaskan. FEFO membutuhkan data expiry/lot yang nyata.
- Rekomendasi produksi harus mempertimbangkan histori penjualan, hari/jam, kapasitas dapur, stok, shelf life, dan ketidakpastian; jangan menyamakan forecast dengan kepastian.""",
    },
    "fnb_food_safety": {
        "keywords": ("food safety", "keamanan pangan", "higiene", "sanitasi", "suhu", "kontaminasi", "expired", "kadaluarsa", "kedaluwarsa", "shelf life"),
        "text": """F&B FOOD SAFETY KNOWLEDGE
- Analisis keamanan pangan harus membedakan bahaya biologis, kimia, fisik, alergi, dan risiko kontaminasi silang.
- Periksa penerimaan bahan, kondisi kemasan, tanggal, penyimpanan, pemisahan mentah/matang, kebersihan, waktu-suhu, dan traceability batch.
- Jangan memberi angka suhu/waktu sebagai aturan universal tanpa konteks bahan, proses, peralatan, dan sumber resmi yang berlaku.
- Jika ada indikasi makanan rusak atau kontaminasi, prioritasnya hentikan penyajian batch terkait, pisahkan, dokumentasikan, dan minta penilaian penanggung jawab; jangan menutupi atau menganggap aman.""",
    },
    "fnb_costing_menu": {
        "keywords": ("hpp makanan", "hpp menu", "food cost", "gross margin", "gross profit", "laba kotor", "margin menu", "bep", "break even", "contribution margin", "menu engineering", "harga jual makanan"),
        "text": """F&B COSTING AND MENU ANALYSIS
- HPP per porsi harus berasal dari resep, conversion factor/satuan, average cost atau costing snapshot yang valid; jika data tidak lengkap, tandai tidak dapat dihitung.
- Gross profit = harga jual − HPP. Gross margin % = gross profit ÷ harga jual × 100. Markup % = gross profit ÷ HPP × 100. Jangan menyebutnya laba bersih.
- Food cost % biasanya COGS bahan/preparation ÷ revenue; jelaskan basis periode dan apakah memakai costing snapshot transaksi.
- BEP membutuhkan biaya tetap/operasional dan contribution margin, bukan hanya harga dan HPP.
- Menu engineering menggabungkan popularitas/volume dan margin: stars, plowhorses, puzzles, dogs. Jangan mengklasifikasikan menu tanpa periode penjualan yang cukup.
- Simulasi kenaikan bahan adalah estimasi; pisahkan dari angka aktual ERP dan tunjukkan asumsi.""",
    },
    "fnb_inventory_analytics": {
        "keywords": ("stok makanan", "inventory makanan", "bahan baku", "stok menipis", "stok habis", "stok negatif", "waste", "stock opname", "reorder", "belanja bahan", "supplier", "safety stock"),
        "text": """F&B INVENTORY ANALYTICS
- Bedakan stok sistem, stok fisik, stok prep, stok produk jadi, pembelian, pemakaian resep, penjualan, adjustment, dan waste.
- Low-stock bukan otomatis harus membeli: periksa pemakaian, forecast, open purchase, shelf life, supplier lead time, dan minimum order.
- Reorder point membutuhkan demand rate × lead time + safety stock. Jika lead time atau demand bersih belum tersedia, berikan daftar data yang kurang.
- Stok negatif adalah sinyal yang perlu ditelusuri, bukan langsung bukti kehilangan: cek tanggal, transaksi, resep, unit conversion, batch, dan opname.
- Waste rate dan variance harus dilaporkan dengan periode, satuan, nilai biaya, alasan, dan confidence; jangan menuduh fraud dari satu anomali.""",
    },
    "perfume_notes": {
        "keywords": ("parfum", "perfume", "fragrance", "notes parfum", "top note", "middle note", "heart note", "base note", "accord", "aroma", "wangi", "citrus", "floral", "woody", "gourmand", "musk", "amber"),
        "text": """PERFUME NOTES AND FRAGRANCE KNOWLEDGE
- Top notes memberi kesan awal dan biasanya lebih mudah menguap; middle/heart notes membentuk karakter utama; base notes memberi fondasi dan ketahanan. Batas waktunya bergantung formula, kulit, cuaca, dan konsentrasi.
- Keluarga aroma umum: citrus, aromatic/green, floral, fruity, woody, amber/oriental, gourmand, leather, chypre, dan musk. Sebuah parfum dapat menggabungkan beberapa keluarga.
- Accord adalah kesan aroma gabungan, bukan satu bahan tunggal. Deskripsi notes pemasaran tidak selalu sama dengan formula aktual.
- Untuk memilih parfum, tanyakan preferensi intensitas, kesan, cuaca, acara, gender expression yang diinginkan, aroma yang disukai/dihindari, dan budget.
- Rekomendasi notes adalah opini berbasis preferensi, bukan jaminan wangi atau ketahanan. Kulit, suhu, kelembapan, aplikasi, dan batch dapat mengubah hasil.""",
    },
    "perfume_recommendation": {
        "keywords": ("cocoknya parfum", "rekomendasi parfum", "parfum untuk", "wangi untuk", "layering", "blind buy", "longevity", "sillage", "projection", "daily", "kantor", "kencan", "malam", "cuaca panas"),
        "text": """PERFUME RECOMMENDATION WORKFLOW
- Mulai dari use case: harian/kantor, panas/outdoor, formal, kencan, malam, ibadah, atau acara khusus; lalu cocokkan intensitas dan keluarga aroma.
- Untuk cuaca panas, sering kali citrus, fresh, green, aquatic, atau aromatic terasa lebih mudah dipakai; untuk malam/dingin, woody, amber, spicy, gourmand, atau musk bisa memberi kesan lebih kaya. Ini kecenderungan, bukan aturan mutlak.
- Bedakan longevity, projection, dan sillage. Parfum yang tahan lama belum tentu menyebar kuat atau cocok untuk ruang tertutup.
- Layering harus dimulai dari kombinasi sederhana dan diuji pada blotter/skin; jangan mencampur bahan konsentrat mentah tanpa pengetahuan kompatibilitas dan keselamatan.
- Jangan mengklaim parfum asli, aman, cocok kulit, atau memiliki notes tertentu tanpa sumber/produk yang jelas. Sarankan patch test dan hentikan pemakaian bila terjadi iritasi.""",
    },
    "perfume_formulation_safety": {
        "keywords": ("formulasi parfum", "fragrance oil", "essential oil", "alkohol parfum", "solvent", "fixative", "konsentrasi parfum", "edp", "edt", "edc", "batch parfum", "qc parfum", "regulasi parfum", "bpom", "kosmetik"),
        "text": """PERFUME FORMULATION AND SAFETY
- EDP/EDT/EDC adalah istilah konsentrasi yang umum dipakai industri, tetapi rentang konsentrasi dan performa tidak universal; jangan menjanjikan persentase atau ketahanan tanpa formula dan bahan nyata.
- Bedakan fragrance concentrate, pelarut, fixative, stabilitas, kompatibilitas kemasan, maceration, filtrasi, batch record, dan QC.
- Essential oil/fragrance material dapat menyebabkan iritasi, sensitisasi, fototoksisitas, atau bahaya mudah terbakar. Data SDS/IFRA dan penilaian keamanan diperlukan.
- Jangan memberi instruksi produksi komersial atau klaim regulasi Indonesia hanya dari pengetahuan umum. Untuk penjualan, cek persyaratan BPOM, label, notifikasi kosmetik, klaim, dan dokumentasi resmi yang berlaku.
- Uji stabilitas, patch/skin safety, batch traceability, dan quality release harus ditangani prosedur yang bertanggung jawab; Hermes hanya memberi kerangka pertanyaan dan analisis risiko, bukan persetujuan keamanan.""",
    },
}


def retrieve(query: str, limit: int = 2) -> str:
    value = query.lower()
    scored = []
    for name, card in CARDS.items():
        score = sum(1 for keyword in card["keywords"] if re.search(r"\b" + re.escape(keyword) + r"\b", value))
        if score:
            scored.append((score, name, card["text"]))
    if not scored:
        return ""
    scored.sort(reverse=True)
    selected = scored[:limit]
    return "\n\n".join(f"[{name}]\n{text}" for _, name, text in selected)
