import hashlib
import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "qc_prepress.db")


def hash_password(raw_password: str) -> str:
    return hashlib.sha256(f"qcprepress_salt:{raw_password}".encode("utf-8")).hexdigest()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='job_checklist_forms'"
    ).fetchone()
    if row is None:
        _init_schema_on_conn(conn)
    else:
        # Ensure the 4 required accounts (Dio, Dina, Naufal, Halim) & password_hash column exist
        cols = [c["name"] for c in conn.execute("PRAGMA table_info(users)").fetchall()]
        dio_row = conn.execute("SELECT id FROM users WHERE username = 'dio'").fetchone() if "password_hash" in cols else None
        if "password_hash" not in cols or dio_row is None:
            _init_schema_on_conn(conn)
    return conn


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Standar Checklist Prepress Industri Percetakan & Packaging
# Setiap item memiliki `auto_rule` jika dapat diperiksa otomatis oleh Prepress File Inspector.
DEFAULT_CHECKLIST_ITEMS: List[Dict[str, Any]] = [
    # 1. DOKUMEN & DIMENSI (DOCUMENT & DIMENSIONS)
    {
        "code": "DOC-01",
        "category": "1. Dokumen & Dimensi",
        "stage_target": "AW",
        "title": "Ukuran Trim / Cut Size Sesuai Spesifikasi Job",
        "description": "Dimensi bersih (TrimBox / MediaBox dikurangi bleed) sesuai ukuran spesifikasi produk (toleransi ±0.5 mm).",
        "is_auto": True,
        "auto_rule": "trim_size_match",
        "mandatory": True,
    },
    {
        "code": "DOC-02",
        "category": "1. Dokumen & Dimensi",
        "stage_target": "ONEUP",
        "title": "Area Bleed Mencukupi (Min. sesuai spesifikasi, std ≥ 2.0 - 3.0 mm)",
        "description": "Terdapat kelebihan area cetak (bleed) di seluruh sisi luar garis potong untuk mencegah putih kertas saat pemotongan/pond.",
        "is_auto": True,
        "auto_rule": "bleed_sufficient",
        "mandatory": True,
    },
    {
        "code": "DOC-03",
        "category": "1. Dokumen & Dimensi",
        "stage_target": "AW",
        "title": "Orientasi & Jumlah Halaman Sesuai",
        "description": "Orientasi (Portrait/Landscape) dan jumlah halaman file sesuai spesifikasi produk.",
        "is_auto": True,
        "auto_rule": "page_orientation_valid",
        "mandatory": True,
    },
    {
        "code": "DOC-04",
        "category": "1. Dokumen & Dimensi",
        "stage_target": "AW",
        "title": "Kode Item, Nomor Revisi & Tanggal Artwork Tercantum Benar",
        "description": "Legend / informasi kode item dan nomor revisi pada luar area cetak sudah sesuai dengan SPK / Job Order.",
        "is_auto": False,
        "auto_rule": None,
        "mandatory": True,
    },

    # 2. WARNA & SEPARASI (COLOR & SEPARATION)
    {
        "code": "CLR-01",
        "category": "2. Warna & Separasi",
        "stage_target": "AW",
        "title": "Mode Warna Dokumen & Gambar Wajib CMYK / Spot (Bebas RGB)",
        "description": "Tidak boleh ada objek gambar atau elemen vektor yang masih menggunakan ruang warna RGB atau Lab yang belum dikonversi ke CMYK.",
        "is_auto": True,
        "auto_rule": "no_rgb_colorspace",
        "mandatory": True,
    },
    {
        "code": "CLR-02",
        "category": "2. Warna & Separasi",
        "stage_target": "AW",
        "title": "Kesesuaian Jumlah & Nama Spot Color / Pantone",
        "description": "Channel warna khusus (Separation / Pantone / Diecut / Varnish / White Ink) sesuai dengan spesifikasi jumlah warna cetak.",
        "is_auto": True,
        "auto_rule": "spot_color_check",
        "mandatory": True,
    },
    {
        "code": "CLR-03",
        "category": "2. Warna & Separasi",
        "stage_target": "ONEUP",
        "title": "Setting Overprint Black (Teks Hitam 100% K) & Knock-out White",
        "description": "Teks hitam kecil menggunakan 100% K Overprint (bukan 4-color rich black) dan elemen putih tidak di-overprint tanpa sengaja.",
        "is_auto": True,
        "auto_rule": "overprint_check",
        "mandatory": True,
    },
    {
        "code": "CLR-04",
        "category": "2. Warna & Separasi",
        "stage_target": "ONEUP",
        "title": "Trapping Antar Warna & Batas Total Ink Coverage (TAC ≤ 300%)",
        "description": "Sudah diberikan trapping (0.1 - 0.2 mm) pada pertemuan warna kontras dan kepekatan tinta total tidak melebihi batas kertas.",
        "is_auto": False,
        "auto_rule": None,
        "mandatory": True,
    },

    # 3. TIPOGRAFI & TEKS (FONTS & TYPOGRAPHY)
    {
        "code": "FNT-01",
        "category": "3. Tipografi & Teks",
        "stage_target": "AW",
        "title": "Seluruh Font Sudah Di-Convert to Curves / Outlined atau Embedded 100%",
        "description": "Tidak ada font yang missing atau belum di-embed/di-convert ke kurva agar huruf tidak berubah saat masuk RIP CTP.",
        "is_auto": True,
        "auto_rule": "fonts_safe",
        "mandatory": True,
    },
    {
        "code": "FNT-02",
        "category": "3. Tipografi & Teks",
        "stage_target": "AW",
        "title": "Ukuran Font Minimum Aman Cetak (≥ 5 pt Positif / ≥ 6 pt Negatif)",
        "description": "Ukuran huruf terkecil masih terbaca jelas setelah dicetak, terutama teks negatif/putih di atas background gelap.",
        "is_auto": True,
        "auto_rule": "min_font_size_check",
        "mandatory": False,
    },
    {
        "code": "FNT-03",
        "category": "3. Tipografi & Teks",
        "stage_target": "AW",
        "title": "Kesesuaian Teks, Komposisi, Netto, BPOM/Halal & Alamat vs File Asli",
        "description": "Tidak ada teks hilang, terpotong (overflow), atau salah ketik dibanding File Asli dari customer/design.",
        "is_auto": False,
        "auto_rule": None,
        "mandatory": True,
    },

    # 4. GAMBAR & RESOLUSI (IMAGES & RESOLUTION)
    {
        "code": "IMG-01",
        "category": "4. Gambar & Resolusi",
        "stage_target": "AW",
        "title": "Resolusi Gambar Raster Memenuhi Standar Cetak (≥ 300 DPI Efektif)",
        "description": "Seluruh gambar bitmap memiliki resolusi efektif minimal 300 DPI (toleransi warning 250 DPI) agar hasil cetak tajam.",
        "is_auto": True,
        "auto_rule": "image_dpi_min",
        "mandatory": True,
    },
    {
        "code": "IMG-02",
        "category": "4. Gambar & Resolusi",
        "stage_target": "AW",
        "title": "Gambar Ter-embed Utuh (Tidak Ada Missing Link / Low-Res Proxy)",
        "description": "Semua gambar tertanam di dalam dokumen akhir dan tidak ada artefak kompresi JPEG berlebih atau objek pecah.",
        "is_auto": True,
        "auto_rule": "images_valid",
        "mandatory": True,
    },

    # 5. ELEMEN TEKNIS & PACKAGING (TECHNICAL & PACKAGING)
    {
        "code": "PKG-01",
        "category": "5. Elemen Teknis & Packaging",
        "stage_target": "ONEUP",
        "title": "Garis Pisau (Die-line / Cut & Crease) Menggunakan Spot Color & Overprint",
        "description": "Garis pisau/pond dan lipatan berada pada layer/channel Spot khusus (mis. DIELINE/CUT) dengan status overprint agar tidak memotong gambar.",
        "is_auto": True,
        "auto_rule": "dieline_spot_check",
        "mandatory": True,
    },
    {
        "code": "PKG-02",
        "category": "5. Elemen Teknis & Packaging",
        "stage_target": "AW",
        "title": "Barcode (EAN/UPC/QR) 100% Black (K), Quiet Zone Aman & Terbaca Scanner",
        "description": "Barcode dicetak 1 warna (100% K) di atas dasar putih, memiliki margin kiri-kanan (quiet zone) cukup, dan orientasi searah jalannya kertas bila memungkinkan.",
        "is_auto": False,
        "auto_rule": None,
        "mandatory": True,
    },
    {
        "code": "PKG-03",
        "category": "5. Elemen Teknis & Packaging",
        "stage_target": "ONEUP",
        "title": "Area Lem (Glue Flap) Bebas Tinta & Bebas Varnish / Laminasi",
        "description": "Area lidah lem (glue area) dikosongkan dari tinta/varnish atau diberi knurling agar lem menempel sempurna saat proses finishing.",
        "is_auto": False,
        "auto_rule": None,
        "mandatory": False,
    },
    {
        "code": "PKG-04",
        "category": "5. Elemen Teknis & Packaging",
        "stage_target": "ONEUP",
        "title": "Posisi & Ukuran Eye Mark / Sensor Mark Sesuai Mesin Kemasan",
        "description": "Eye mark untuk sensor mesin filling/cutting memiliki ukuran, warna kontras, dan jalur bebas gangguan grafis.",
        "is_auto": False,
        "auto_rule": None,
        "mandatory": False,
    },

    # 6. VERIFIKASI ANTAR-TAHAP (ORIGINAL vs AW vs ONEUP vs LAYOUT)
    {
        "code": "CMP-01",
        "category": "6. Perbandingan Antar-Tahap (Visual & Pixel Diff)",
        "stage_target": "AW",
        "title": "Perbandingan File Asli vs AW: Konten & Grafis Identik (Tanpa Pergeseran)",
        "description": "Hasil pemeriksaan Visual Comparator antara File Asli (Customer) dan file AW (Prepress) menunjukkan tidak ada elemen hilang, berubah warna, atau bergeser.",
        "is_auto": True,
        "auto_rule": "diff_original_vs_aw",
        "mandatory": True,
    },
    {
        "code": "CMP-02",
        "category": "6. Perbandingan Antar-Tahap (Visual & Pixel Diff)",
        "stage_target": "ONEUP",
        "title": "Perbandingan AW vs OneUp: Bleed, Trapping & Posisi Die-line Presisi",
        "description": "File OneUp sesuai dengan AW yang disetujui dengan tambahan bleed dan kontur pisau yang akurat.",
        "is_auto": True,
        "auto_rule": "diff_aw_vs_oneup",
        "mandatory": True,
    },
    {
        "code": "CMP-03",
        "category": "6. Perbandingan Antar-Tahap (Visual & Pixel Diff)",
        "stage_target": "LAYOUT",
        "title": "Verifikasi OneUp vs Layout (Imposisi): Jumlah Up (N-Up) & Orientasi Benar",
        "description": "Seluruh slot pada Layout/Montase menggunakan file OneUp revisi terbaru dengan jumlah up, posisi head-to-head/step-repeat yang akurat.",
        "is_auto": True,
        "auto_rule": "layout_imposition_check",
        "mandatory": True,
    },
    {
        "code": "CMP-04",
        "category": "6. Perbandingan Antar-Tahap (Visual & Pixel Diff)",
        "stage_target": "LAYOUT",
        "title": "Kelengkapan Layout Cetak: Register Mark, Crop Mark, Color Bar, Gripper & Arah Serat",
        "description": "Ukuran plano/plate, jarak gripper (jepitan mesin), color bar CMYK+Spot, tanda potong/lipat, dan arah serat kertas (grain direction) sudah benar.",
        "is_auto": False,
        "auto_rule": None,
        "mandatory": True,
    },
]

DEFAULT_USERS = [
    {
        "id": 1,
        "username": "dio",
        "password_plain": "dio123",
        "full_name": "Dio",
        "nik": "PRP-001",
        "role": "SUPERVISOR",
        "role_label": "Kepala Prepress",
        "department": "Divisi Prepress & Design",
    },
    {
        "id": 2,
        "username": "dina",
        "password_plain": "dina123",
        "full_name": "Dina",
        "nik": "PRP-002",
        "role": "QC",
        "role_label": "QC Prepress",
        "department": "Divisi Prepress & Design",
    },
    {
        "id": 3,
        "username": "naufal",
        "password_plain": "naufal123",
        "full_name": "Naufal",
        "nik": "PRP-003",
        "role": "OPERATOR",
        "role_label": "Operator Prepress",
        "department": "Divisi Prepress & Design",
    },
    {
        "id": 4,
        "username": "halim",
        "password_plain": "halim123",
        "full_name": "Halim",
        "nik": "PRP-004",
        "role": "OPERATOR",
        "role_label": "Operator Prepress",
        "department": "Divisi Prepress & Design",
    },
]


def _init_schema_on_conn(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    # Users table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL DEFAULT '',
            full_name TEXT NOT NULL,
            nik TEXT NOT NULL,
            role TEXT NOT NULL,
            role_label TEXT NOT NULL,
            department TEXT NOT NULL
        )
        """
    )
    cols = [c["name"] for c in cur.execute("PRAGMA table_info(users)").fetchall()]
    if "password_hash" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")

    # Jobs (Prepress Inspection Orders)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_number TEXT UNIQUE NOT NULL,
            product_name TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            item_code TEXT NOT NULL,
            package_type TEXT NOT NULL DEFAULT 'Folding Carton / Box',
            print_method TEXT NOT NULL DEFAULT 'Cetak Offset',
            revision_no INTEGER NOT NULL DEFAULT 1,
            target_width_mm REAL NOT NULL DEFAULT 120.0,
            target_height_mm REAL NOT NULL DEFAULT 180.0,
            required_bleed_mm REAL NOT NULL DEFAULT 3.0,
            min_dpi INTEGER NOT NULL DEFAULT 300,
            color_spec TEXT NOT NULL DEFAULT 'CMYK + 1 Spot (PANTONE 185 C) + DIELINE',
            expected_nup INTEGER NOT NULL DEFAULT 4,
            layout_width_mm REAL NOT NULL DEFAULT 290.0,
            layout_height_mm REAL NOT NULL DEFAULT 410.0,
            status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            notes TEXT DEFAULT ''
        )
        """
    )

    # Job Files across the 4 Prepress Stages: ORIGINAL, AW, ONEUP, LAYOUT
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS job_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            stage TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            filename TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            preview_path TEXT NOT NULL,
            file_format TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL,
            uploaded_by TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            inspection_json TEXT NOT NULL,
            auto_status TEXT NOT NULL DEFAULT 'PASS',
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """
    )

    # Checklist Items per Job
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS checklist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            category TEXT NOT NULL,
            stage_target TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            is_auto INTEGER NOT NULL DEFAULT 0,
            auto_rule TEXT,
            mandatory INTEGER NOT NULL DEFAULT 1,
            auto_status TEXT DEFAULT 'PENDING',
            auto_value TEXT DEFAULT '',
            auto_detail TEXT DEFAULT '',
            manual_status TEXT DEFAULT 'PENDING',
            operator_note TEXT DEFAULT '',
            checked_by TEXT DEFAULT '',
            checked_role TEXT DEFAULT '',
            checked_at TEXT DEFAULT '',
            override_reason TEXT DEFAULT '',
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """
    )

    # Visual Annotations / Defect Pins on Artwork Canvas
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            pin_number INTEGER NOT NULL,
            stage TEXT NOT NULL,
            x_pct REAL NOT NULL,
            y_pct REAL NOT NULL,
            w_pct REAL NOT NULL DEFAULT 0.0,
            h_pct REAL NOT NULL DEFAULT 0.0,
            severity TEXT NOT NULL DEFAULT 'MAJOR',
            category TEXT NOT NULL DEFAULT 'Warna / Spot',
            title TEXT NOT NULL,
            comment TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            checklist_code TEXT DEFAULT '',
            created_by TEXT NOT NULL,
            created_role TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_by TEXT DEFAULT '',
            resolved_note TEXT DEFAULT '',
            resolved_at TEXT DEFAULT '',
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """
    )

    # Cross-Stage Visual Comparisons (e.g. ORIGINAL vs AW, AW vs ONEUP, ONEUP vs LAYOUT)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS comparisons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            stage_a TEXT NOT NULL,
            stage_b TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'PIXEL_DIFF',
            similarity_pct REAL NOT NULL DEFAULT 100.0,
            diff_Pixel_count INTEGER NOT NULL DEFAULT 0,
            diff_regions_json TEXT NOT NULL DEFAULT '[]',
            diff_image_path TEXT NOT NULL DEFAULT '',
            matched_nup_count INTEGER NOT NULL DEFAULT 0,
            summary TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """
    )

    # Multi-tier Approval Sign-offs (OPERATOR -> QC -> SUPERVISOR)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            role_stage TEXT NOT NULL,
            decision TEXT NOT NULL,
            approver_name TEXT NOT NULL,
            approver_nik TEXT NOT NULL,
            approver_role_label TEXT NOT NULL,
            notes TEXT DEFAULT '',
            signature_hash TEXT NOT NULL,
            signed_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """
    )

    # Complete Audit Trail Logs
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            actor_name TEXT NOT NULL,
            actor_role TEXT NOT NULL,
            action_type TEXT NOT NULL,
            target_stage TEXT DEFAULT '',
            summary TEXT NOT NULL,
            details_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )

    # Custom Checklist Templates table (so users can customize standard rules)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_checklist_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            category TEXT NOT NULL,
            stage_target TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            is_auto INTEGER NOT NULL DEFAULT 0,
            auto_rule TEXT,
            mandatory INTEGER NOT NULL DEFAULT 1
        )
        """
    )

    # 3 Official Division: Design Checklist Forms per Job (AWAL, ONEUP, AKHIR)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS job_checklist_forms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            form_type TEXT NOT NULL,
            form_data_json TEXT NOT NULL DEFAULT '{}',
            desainer_name TEXT DEFAULT '',
            desainer_nik TEXT DEFAULT '',
            desainer_role TEXT DEFAULT '',
            desainer_signed_at TEXT DEFAULT '',
            reviewer_name TEXT DEFAULT '',
            reviewer_nik TEXT DEFAULT '',
            reviewer_role TEXT DEFAULT '',
            reviewer_signed_at TEXT DEFAULT '',
            updated_by TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            UNIQUE(job_id, form_type),
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """
    )

    # Ensure only the 4 official users (Dio, Dina, Naufal, Halim) exist in users table
    cur.execute("DELETE FROM users WHERE username NOT IN ('dio', 'dina', 'naufal', 'halim')")
    for u in DEFAULT_USERS:
        cur.execute(
            """
            INSERT OR REPLACE INTO users (id, username, password_hash, full_name, nik, role, role_label, department)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                u["id"],
                u["username"],
                hash_password(u["password_plain"]),
                u["full_name"],
                u["nik"],
                u["role"],
                u["role_label"],
                u["department"],
            ),
        )

    # Seed checklist templates if empty
    cur.execute("SELECT COUNT(*) as cnt FROM custom_checklist_templates")
    if cur.fetchone()["cnt"] == 0:
        for item in DEFAULT_CHECKLIST_ITEMS:
            cur.execute(
                """
                INSERT INTO custom_checklist_templates
                (code, category, stage_target, title, description, is_auto, auto_rule, mandatory)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["code"],
                    item["category"],
                    item["stage_target"],
                    item["title"],
                    item["description"],
                    1 if item["is_auto"] else 0,
                    item["auto_rule"],
                    1 if item["mandatory"] else 0,
                ),
            )

    conn.commit()


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _init_schema_on_conn(conn)
    conn.close()


def log_audit(
    conn: sqlite3.Connection,
    job_id: Optional[int],
    actor_name: str,
    actor_role: str,
    action_type: str,
    summary: str,
    target_stage: str = "",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_logs (job_id, actor_name, actor_role, action_type, target_stage, summary, details_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            actor_name,
            actor_role,
            action_type,
            target_stage,
            summary,
            json.dumps(details or {}, ensure_ascii=False),
            now_iso(),
        ),
    )


def populate_job_checklist(conn: sqlite3.Connection, job_id: int) -> None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM custom_checklist_templates ORDER BY id ASC")
    templates = cur.fetchall()
    for tpl in templates:
        cur.execute(
            """
            INSERT INTO checklist_items (
                job_id, code, category, stage_target, title, description,
                is_auto, auto_rule, mandatory, auto_status, manual_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 'PENDING')
            """,
            (
                job_id,
                tpl["code"],
                tpl["category"],
                tpl["stage_target"],
                tpl["title"],
                tpl["description"],
                tpl["is_auto"],
                tpl["auto_rule"],
                tpl["mandatory"],
            ),
        )
