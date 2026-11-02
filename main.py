import hashlib
import json
import os
import shutil
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.checklist_forms import (
    FORM_META,
    FORM_TYPES,
    ensure_job_checklist_forms,
    render_printable_checklist_html,
)
from backend.comparator import compare_stage_files
from backend.database import (
    BASE_DIR,
    get_db,
    hash_password,
    init_db,
    log_audit,
    now_iso,
    populate_job_checklist,
)
from backend.inspector import evaluate_job_checklist, inspect_artwork_file
from backend.report_generator import generate_qc_report_pdf
from backend.sample_generator import generate_stage_pdfs_for_job
from backend.spk_scanner import scan_spk_file

UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
STATIC_DIR = os.path.join(BASE_DIR, "static")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)

app = FastAPI(
    title="QC Prepress Studio - Sistem Pemeriksaan Artwork & Checklist Otomatis",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateJobRequest(BaseModel):
    job_number: str
    product_name: str
    customer_name: str
    item_code: str
    package_type: str = "Folding Carton / Box"
    print_method: str = "Cetak Offset"
    revision_no: int = 1
    target_width_mm: float = 120.0
    target_height_mm: float = 180.0
    required_bleed_mm: float = 3.0
    min_dpi: int = 300
    color_spec: str = "CMYK + 1 Spot (PANTONE 185 C) + DIELINE"
    expected_nup: int = 4
    layout_width_mm: float = 290.0
    layout_height_mm: float = 410.0
    notes: str = ""
    created_by: str = "Naufal"
    created_role: str = "Operator Prepress"
    generate_demo_files: bool = False
    demo_has_defect: bool = False
    checklist_overrides: Optional[Dict[str, Any]] = None


class UpdateJobRequest(BaseModel):
    job_number: str
    product_name: str
    customer_name: str
    item_code: str
    package_type: str = "Folding Carton / Box"
    print_method: str = "Cetak Offset"
    revision_no: int = 1
    target_width_mm: float = 120.0
    target_height_mm: float = 180.0
    required_bleed_mm: float = 3.0
    min_dpi: int = 300
    color_spec: str = "CMYK + 1 Spot (PANTONE 185 C) + DIELINE"
    expected_nup: int = 4
    layout_width_mm: float = 290.0
    layout_height_mm: float = 410.0
    notes: str = ""
    actor_name: str = "Budi Santoso"
    actor_role: str = "Operator Prepress"


class ChecklistUpdateRequest(BaseModel):
    manual_status: str
    operator_note: str = ""
    override_reason: str = ""
    actor_name: str = "Budi Santoso"
    actor_role: str = "Operator Prepress"


class CompareStagesRequest(BaseModel):
    stage_a: str
    stage_b: str
    actor_name: str = "Budi Santoso"
    actor_role: str = "Operator Prepress"


class CreateAnnotationRequest(BaseModel):
    stage: str
    x_pct: float
    y_pct: float
    w_pct: float = 6.0
    h_pct: float = 6.0
    severity: str = "MAJOR"
    category: str = "Teks / Typo"
    title: str
    comment: str
    checklist_code: str = ""
    actor_name: str = "Andi Wijaya"
    actor_role: str = "QC Prepress Inspector"


class ResolveAnnotationRequest(BaseModel):
    status: str
    resolved_note: str = ""
    actor_name: str = "Budi Santoso"
    actor_role: str = "Operator Prepress"


class SubmitApprovalRequest(BaseModel):
    role_stage: str  # OPERATOR, QC, SUPERVISOR
    decision: str  # APPROVED, APPROVED_WITH_NOTE, REJECTED
    approver_name: str
    approver_nik: str
    approver_role_label: str
    notes: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateTemplateItemRequest(BaseModel):
    code: str
    category: str
    stage_target: str = "AW"
    title: str
    description: str
    mandatory: bool = True


class SaveChecklistFormRequest(BaseModel):
    form_data: Dict[str, Any]
    actor_name: str = "Naufal"
    actor_role: str = "Operator Prepress"


class SignChecklistFormRequest(BaseModel):
    slot: str  # "DESAINER" or "REVIEWER"
    action: str = "sign"  # "sign" or "unsign"
    actor_name: str = "Dio"
    actor_nik: str = "PRP-001"
    actor_role: str = "Kepala Prepress"


def _process_and_register_file(
    conn,
    job_id: int,
    stage: str,
    src_file_path: str,
    original_filename: str,
    uploaded_by: str,
    uploaded_role: str,
) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job = dict(cur.fetchone())

    cur.execute(
        "SELECT COALESCE(MAX(version), 0) as max_v FROM job_files WHERE job_id = ? AND stage = ?",
        (job_id, stage),
    )
    next_version = int(cur.fetchone()["max_v"]) + 1

    job_dir = os.path.join(UPLOADS_DIR, f"job_{job_id}")
    os.makedirs(job_dir, exist_ok=True)

    ext = os.path.splitext(original_filename)[1].lower()
    safe_base = f"{stage}_v{next_version}{ext}"
    stored_path = os.path.join(job_dir, safe_base)
    if os.path.abspath(src_file_path) != os.path.abspath(stored_path):
        shutil.copy2(src_file_path, stored_path)

    preview_filename = f"preview_{stage}_v{next_version}.png"
    preview_path = os.path.join(job_dir, preview_filename)

    inspection = inspect_artwork_file(stored_path, preview_path, job_spec=job)

    # Determine overall file auto_status
    auto_status = "PASS"
    if inspection.get("has_rgb") or not inspection.get("all_fonts_safe"):
        auto_status = "FAIL"
    elif inspection.get("min_dpi") and inspection["min_dpi"] < job["min_dpi"]:
        auto_status = "FAIL" if inspection["min_dpi"] < 220 else "WARN"
    elif stage == "ONEUP" and inspection.get("detected_bleed_mm", 0) < job["required_bleed_mm"]:
        auto_status = "WARN"

    file_size = os.path.getsize(stored_path)
    rel_stored = f"/uploads/job_{job_id}/{safe_base}"
    rel_preview = f"/uploads/job_{job_id}/{preview_filename}"

    cur.execute(
        """
        INSERT INTO job_files (
            job_id, stage, version, filename, stored_path, preview_path,
            file_format, file_size_bytes, uploaded_by, uploaded_at,
            inspection_json, auto_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            stage,
            next_version,
            original_filename,
            rel_stored,
            rel_preview,
            ext.lstrip(".").upper(),
            file_size,
            uploaded_by,
            now_iso(),
            json.dumps(inspection, ensure_ascii=False),
            auto_status,
        ),
    )
    file_id = cur.lastrowid

    evaluate_job_checklist(conn, job_id)
    ensure_job_checklist_forms(conn, job_id, force_refresh_auto=True)

    log_audit(
        conn,
        job_id,
        uploaded_by,
        uploaded_role,
        "UPLOAD_AND_INSPECT",
        f"Unggah & Preflight Otomatis file [{stage} v{next_version}] '{original_filename}' -> Status: {auto_status}",
        target_stage=stage,
        details={
            "filename": original_filename,
            "version": next_version,
            "trim_box_mm": inspection.get("trim_box_mm"),
            "detected_bleed_mm": inspection.get("detected_bleed_mm"),
            "color_spaces": inspection.get("color_spaces"),
            "spot_colors": inspection.get("spot_colors"),
            "min_dpi": inspection.get("min_dpi"),
            "auto_status": auto_status,
        },
    )
    conn.commit()
    return {"file_id": file_id, "stage": stage, "version": next_version, "inspection": inspection}


def _run_stage_comparison_internal(
    conn,
    job_id: int,
    stage_a: str,
    stage_b: str,
    actor_name: str,
    actor_role: str,
) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM job_files WHERE job_id = ? AND stage = ? ORDER BY version DESC, id DESC LIMIT 1",
        (job_id, stage_a),
    )
    row_a = cur.fetchone()
    cur.execute(
        "SELECT * FROM job_files WHERE job_id = ? AND stage = ? ORDER BY version DESC, id DESC LIMIT 1",
        (job_id, stage_b),
    )
    row_b = cur.fetchone()
    if not row_a or not row_b:
        raise ValueError(f"File untuk tahap {stage_a} dan {stage_b} harus diunggah terlebih dahulu.")

    abs_prev_a = os.path.join(BASE_DIR, row_a["preview_path"].lstrip("/").replace("/", os.sep))
    abs_prev_b = os.path.join(BASE_DIR, row_b["preview_path"].lstrip("/").replace("/", os.sep))
    insp_a = json.loads(row_a["inspection_json"])
    insp_b = json.loads(row_b["inspection_json"])

    job_dir = os.path.join(UPLOADS_DIR, f"job_{job_id}")
    diff_filename = f"diff_{stage_a}_vs_{stage_b}.png"
    abs_diff_path = os.path.join(job_dir, diff_filename)
    rel_diff_path = f"/uploads/job_{job_id}/{diff_filename}"

    res = compare_stage_files(
        abs_prev_a,
        abs_prev_b,
        stage_a,
        stage_b,
        insp_a,
        insp_b,
        abs_diff_path,
    )

    cur.execute(
        """
        INSERT INTO comparisons (
            job_id, stage_a, stage_b, mode, similarity_pct, diff_pixel_count,
            diff_regions_json, diff_image_path, matched_nup_count, summary,
            created_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            stage_a,
            stage_b,
            res["mode"],
            res["similarity_pct"],
            res["diff_pixel_count"],
            json.dumps(res["regions"], ensure_ascii=False),
            rel_diff_path,
            res["matched_nup_count"],
            res["summary"],
            actor_name,
            now_iso(),
        ),
    )
    comp_id = cur.lastrowid

    evaluate_job_checklist(conn, job_id)
    ensure_job_checklist_forms(conn, job_id, force_refresh_auto=True)

    log_audit(
        conn,
        job_id,
        actor_name,
        actor_role,
        "VISUAL_COMPARE",
        f"Perbandingan Visual {stage_a} vs {stage_b}: {res['summary']}",
        target_stage=f"{stage_a}->{stage_b}",
        details={
            "similarity_pct": res["similarity_pct"],
            "region_count": len(res["regions"]),
            "matched_nup_count": res["matched_nup_count"],
        },
    )
    conn.commit()
    return {
        "id": comp_id,
        "stage_a": stage_a,
        "stage_b": stage_b,
        "preview_a": row_a["preview_path"],
        "preview_b": row_b["preview_path"],
        "diff_image_path": rel_diff_path,
        **res,
    }


def seed_demo_jobs_if_empty(force_if_zero_jobs: bool = False) -> None:
    """Seeds 2 realistic Prepress Inspection Jobs on first startup so the app is ready for immediate demo."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM jobs")
    jobs_cnt = cur.fetchone()["cnt"]
    cur.execute("SELECT COUNT(*) as cnt FROM audit_logs")
    logs_cnt = cur.fetchone()["cnt"]
    if jobs_cnt > 0 or (logs_cnt > 0 and not force_if_zero_jobs):
        conn.close()
        return

    # DEMO JOB 1: Job with deliberate Prepress Defects detected in AW (RGB image, 180 DPI, Netto typo & badge shift)
    cur.execute(
        """
        INSERT INTO jobs (
            job_number, product_name, customer_name, item_code, package_type, print_method,
            revision_no, target_width_mm, target_height_mm, required_bleed_mm, min_dpi,
            color_spec, expected_nup, layout_width_mm, layout_height_mm, status,
            created_by, created_at, updated_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "JOB-2026-1001",
            "Folding Box Nusantara Roast Arabica 250g",
            "PT Nusantara Aroma Cemerlang",
            "PKG-NR-250G-R02",
            "Folding Carton / Box",
            "Cetak Offset",
            2,
            120.0,
            180.0,
            3.0,
            300,
            "CMYK + PANTONE 185 C + DIELINE_CUT",
            4,
            290.0,
            410.0,
            "REVISION_REQUIRED",
            "Naufal",
            now_iso(),
            now_iso(),
            "Pemeriksaan file AW menunjukkan deviasi teks Netto (200g vs 250g), pergeseran badge 100% Asli, serta gambar produk masih RGB 180 DPI.",
        ),
    )
    job1_id = cur.lastrowid
    populate_job_checklist(conn, job1_id)
    conn.commit()

    tmp_dir_1 = os.path.join(UPLOADS_DIR, "seed_tmp_1")
    paths_1 = generate_stage_pdfs_for_job(
        tmp_dir_1, "PKG-NR-250G-R02", 120.0, 180.0, 3.0, has_aw_defect=True
    )
    for st in ("ORIGINAL", "AW", "ONEUP", "LAYOUT"):
        _process_and_register_file(
            conn,
            job1_id,
            st,
            paths_1[st],
            os.path.basename(paths_1[st]),
            "Naufal",
            "Operator Prepress",
        )
    shutil.rmtree(tmp_dir_1, ignore_errors=True)

    # Run automated comparisons for Job 1
    _run_stage_comparison_internal(conn, job1_id, "ORIGINAL", "AW", "Dina", "QC Prepress")
    _run_stage_comparison_internal(conn, job1_id, "ONEUP", "LAYOUT", "Dina", "QC Prepress")

    # Add realistic Visual Defect Pins on Job 1 (AW stage)
    pins_job1 = [
        (
            1,
            "AW",
            82.0,
            11.2,
            16.0,
            11.5,
            "CRITICAL",
            "Elemen Bergeser / Berubah",
            "Badge '100% ASLI' Bergeser 4.5mm & Teks Berubah Jadi '99% ASLI'",
            "Terdeteksi pada Visual Comparator (Pixel Diff #1): posisi lingkaran badge bergeser ke kanan 4.5 mm dan angka berubah dibanding File Asli customer.",
            "CMP-01",
            "Dina",
            "QC Prepress",
            "OPEN",
        ),
        (
            2,
            "AW",
            45.0,
            72.0,
            68.0,
            6.5,
            "CRITICAL",
            "Teks / Typo",
            "Salah Ketik Berat Bersih / Netto: Tertulis 200g (Seharusnya 250g)",
            "Pada File Asli tertulis 'BERAT BERSIH / NETTO: 250g (10 Sachet @ 25g)', tetapi di file AW berubah menjadi '200g (10 Sachet @ 20g)'. Harap segera dikoreksi!",
            "FNT-03",
            "Dina",
            "QC Prepress",
            "OPEN",
        ),
        (
            3,
            "AW",
            50.0,
            41.5,
            50.0,
            25.0,
            "MAJOR",
            "Resolusi & Warna",
            "Ilustrasi Tengah Masih Mode RGB & Resolusi 180 DPI (< 300 DPI)",
            "Hasil baca parameter otomatis mendeteksi XObject gambar utama masih DeviceRGB dengan resolusi efektif 180 DPI. Wajib dikonversi ke CMYK 300 DPI.",
            "CLR-01",
            "Naufal",
            "Operator Prepress",
            "OPEN",
        ),
    ]
    for p in pins_job1:
        cur.execute(
            """
            INSERT INTO annotations (
                job_id, pin_number, stage, x_pct, y_pct, w_pct, h_pct,
                severity, category, title, comment, checklist_code,
                created_by, created_role, created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job1_id, *p[:11], p[11], p[12], now_iso(), p[13]),
        )

    # DEMO JOB 2: Clean Master Prepress Job that passed all checks & has full Multi-Role Approval
    cur.execute(
        """
        INSERT INTO jobs (
            job_number, product_name, customer_name, item_code, package_type, print_method,
            revision_no, target_width_mm, target_height_mm, required_bleed_mm, min_dpi,
            color_spec, expected_nup, layout_width_mm, layout_height_mm, status,
            created_by, created_at, updated_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "JOB-2026-1002",
            "Master Box Nusantara Roast Gold Edition (Final Cetak)",
            "PT Nusantara Aroma Cemerlang",
            "PKG-NR-GOLD-R03",
            "Folding Carton / Box",
            "Cetak Offset",
            3,
            120.0,
            180.0,
            3.0,
            300,
            "CMYK + PANTONE 185 C + DIELINE_CUT",
            4,
            290.0,
            410.0,
            "APPROVED",
            "Halim",
            now_iso(),
            now_iso(),
            "Seluruh parameter otomatis (CMYK, 300 DPI, Bleed 3mm, Spot Dieline, 4-Up Imposition) dan perbandingan File Asli vs AW 100% sesuai. Siap naik cetak CTP.",
        ),
    )
    job2_id = cur.lastrowid
    populate_job_checklist(conn, job2_id)
    conn.commit()

    tmp_dir_2 = os.path.join(UPLOADS_DIR, "seed_tmp_2")
    paths_2 = generate_stage_pdfs_for_job(
        tmp_dir_2, "PKG-NR-GOLD-R03", 120.0, 180.0, 3.0, has_aw_defect=False
    )
    for st in ("ORIGINAL", "AW", "ONEUP", "LAYOUT"):
        _process_and_register_file(
            conn,
            job2_id,
            st,
            paths_2[st],
            os.path.basename(paths_2[st]),
            "Halim",
            "Operator Prepress",
        )
    shutil.rmtree(tmp_dir_2, ignore_errors=True)

    _run_stage_comparison_internal(conn, job2_id, "ORIGINAL", "AW", "Halim", "Operator Prepress")
    _run_stage_comparison_internal(conn, job2_id, "AW", "ONEUP", "Halim", "Operator Prepress")
    _run_stage_comparison_internal(conn, job2_id, "ONEUP", "LAYOUT", "Dina", "QC Prepress")

    # Mark remaining manual items as PASS on Job 2
    cur.execute(
        """
        UPDATE checklist_items
        SET manual_status = 'PASS',
            checked_by = 'Dina',
            checked_role = 'QC Prepress',
            checked_at = ?,
            operator_note = 'Diverifikasi sesuai standar cetak & SPK.'
        WHERE job_id = ?
        """,
        (now_iso(), job2_id),
    )

    # Add 3-tier approvals on Job 2
    approvals_job2 = [
        ("OPERATOR", "APPROVED", "Halim", "PRP-004", "Operator Prepress", "File AW, OneUp (Bleed 3mm), dan Layout 4-Up sudah diperiksa lengkap."),
        ("QC", "APPROVED", "Dina", "PRP-002", "QC Prepress", "Hasil Pixel Diff File Asli vs AW 100% identik, separasi warna & barcode lolos QC."),
        ("SUPERVISOR", "APPROVED", "Dio", "PRP-001", "Kepala Prepress", "ACC Naik Cetak / Output CTP Plate."),
    ]
    for r_stage, dec, a_name, a_nik, a_label, a_note in approvals_job2:
        sig_hash = hashlib.sha256(f"{job2_id}:{r_stage}:{a_nik}:{now_iso()}".encode()).hexdigest()[:24].upper()
        cur.execute(
            """
            INSERT INTO approvals (
                job_id, role_stage, decision, approver_name, approver_nik,
                approver_role_label, notes, signature_hash, signed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job2_id, r_stage, dec, a_name, a_nik, a_label, a_note, sig_hash, now_iso()),
        )

    conn.commit()
    conn.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    seed_demo_jobs_if_empty(force_if_zero_jobs=True)
    _migrate_legacy_actor_names()


def _migrate_legacy_actor_names() -> None:
    """Ensures any existing demo records use the 4 official accounts: Dio, Dina, Naufal, Halim."""
    conn = get_db()
    cur = conn.cursor()
    replacements = [
        ("Budi Santoso", "Naufal"),
        ("Siti Rahmawati", "Halim"),
        ("Andi Wijaya", "Dina"),
        ("Maya Kusuma", "Dina"),
        ("Ir. Hendra Gunawan", "Dio"),
    ]
    for old_name, new_name in replacements:
        cur.execute("UPDATE jobs SET created_by = ? WHERE created_by = ?", (new_name, old_name))
        cur.execute("UPDATE job_files SET uploaded_by = ? WHERE uploaded_by = ?", (new_name, old_name))
        cur.execute("UPDATE checklist_items SET checked_by = ? WHERE checked_by = ?", (new_name, old_name))
        cur.execute("UPDATE annotations SET created_by = ? WHERE created_by = ?", (new_name, old_name))
        cur.execute("UPDATE annotations SET resolved_by = ? WHERE resolved_by = ?", (new_name, old_name))
        cur.execute("UPDATE comparisons SET created_by = ? WHERE created_by = ?", (new_name, old_name))
        cur.execute("UPDATE approvals SET approver_name = ? WHERE approver_name = ?", (new_name, old_name))
        cur.execute("UPDATE audit_logs SET actor_name = ? WHERE actor_name = ?", (new_name, old_name))

    cur.execute("UPDATE approvals SET approver_nik = 'PRP-001', approver_role_label = 'Kepala Prepress' WHERE role_stage = 'SUPERVISOR'")
    cur.execute("UPDATE approvals SET approver_nik = 'PRP-002', approver_role_label = 'QC Prepress' WHERE role_stage = 'QC'")
    cur.execute("UPDATE approvals SET approver_nik = 'PRP-004', approver_role_label = 'Operator Prepress' WHERE role_stage = 'OPERATOR' AND approver_name = 'Halim'")
    cur.execute("UPDATE approvals SET approver_nik = 'PRP-003', approver_role_label = 'Operator Prepress' WHERE role_stage = 'OPERATOR' AND approver_name = 'Naufal'")
    cur.execute("UPDATE jobs SET print_method = 'Cetak Digital' WHERE LOWER(print_method) LIKE '%digital%' AND print_method != 'Cetak Digital'")
    cur.execute("UPDATE jobs SET print_method = 'Cetak Offset' WHERE print_method NOT IN ('Cetak Offset', 'Cetak Digital')")
    conn.commit()
    conn.close()


@app.post("/api/auth/login")
def login_user(req: LoginRequest) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    uname = req.username.strip().lower()
    cur.execute(
        "SELECT id, username, password_hash, full_name, nik, role, role_label, department FROM users WHERE LOWER(username) = ? OR LOWER(full_name) = ?",
        (uname, uname),
    )
    row = cur.fetchone()
    if not row or row["password_hash"] != hash_password(req.password.strip()):
        conn.close()
        raise HTTPException(status_code=401, detail="Username atau Password salah.")

    user_dict = {
        "id": row["id"],
        "username": row["username"],
        "full_name": row["full_name"],
        "nik": row["nik"],
        "role": row["role"],
        "role_label": row["role_label"],
        "department": row["department"],
    }
    token = hashlib.sha256(f"session:{row['username']}:{row['id']}:{now_iso()}".encode()).hexdigest()

    log_audit(
        conn,
        None,
        row["full_name"],
        row["role_label"],
        "USER_LOGIN",
        f"Login berhasil: {row['full_name']} ({row['role_label']})",
    )
    conn.commit()
    conn.close()
    return {"token": token, "user": user_dict}


@app.get("/api/users")
def list_users() -> List[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, username, full_name, nik, role, role_label, department FROM users ORDER BY id ASC"
    )
    users = [dict(r) for r in cur.fetchall()]
    conn.close()
    return users


@app.get("/api/dashboard")
def get_dashboard_summary() -> Dict[str, Any]:
    seed_demo_jobs_if_empty()
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as cnt FROM jobs")
    total_jobs = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(*) as cnt FROM jobs WHERE status = 'APPROVED'")
    approved_jobs = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(*) as cnt FROM jobs WHERE status IN ('REVISION_REQUIRED', 'REJECTED')")
    revision_jobs = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(*) as cnt FROM checklist_items WHERE is_auto = 1 AND auto_status != 'PENDING'")
    auto_checks_run = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(*) as cnt FROM annotations WHERE status = 'OPEN'")
    open_pins = cur.fetchone()["cnt"]

    cur.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 15")
    recent_logs = [dict(r) for r in cur.fetchall()]

    conn.close()
    return {
        "total_jobs": total_jobs,
        "approved_jobs": approved_jobs,
        "revision_jobs": revision_jobs,
        "in_progress_jobs": max(0, total_jobs - approved_jobs - revision_jobs),
        "auto_checks_run": auto_checks_run,
        "open_pins": open_pins,
        "recent_logs": recent_logs,
    }


@app.get("/api/jobs")
def list_jobs() -> List[Dict[str, Any]]:
    seed_demo_jobs_if_empty()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs ORDER BY id DESC")
    jobs = [dict(r) for r in cur.fetchall()]

    for j in jobs:
        jid = j["id"]
        cur.execute("SELECT DISTINCT stage FROM job_files WHERE job_id = ?", (jid,))
        j["uploaded_stages"] = [r["stage"] for r in cur.fetchall()]

        cur.execute("SELECT manual_status, auto_status FROM checklist_items WHERE job_id = ?", (jid,))
        c_rows = cur.fetchall()
        total_c = len(c_rows)
        pass_c = sum(1 for r in c_rows if r["manual_status"] == "PASS")
        fail_c = sum(1 for r in c_rows if r["manual_status"] == "FAIL" or r["auto_status"] == "FAIL")
        j["checklist_total"] = total_c
        j["checklist_passed"] = pass_c
        j["checklist_failed"] = fail_c
        j["checklist_progress_pct"] = round((pass_c / max(total_c, 1)) * 100.0, 1)

        cur.execute("SELECT COUNT(*) as cnt FROM annotations WHERE job_id = ? AND status = 'OPEN'", (jid,))
        j["open_annotations"] = cur.fetchone()["cnt"]

        cur.execute("SELECT role_stage, decision, approver_name FROM approvals WHERE job_id = ?", (jid,))
        j["approvals_summary"] = {r["role_stage"]: {"decision": r["decision"], "by": r["approver_name"]} for r in cur.fetchall()}

        forms_map = ensure_job_checklist_forms(conn, jid)
        signed_forms = sum(
            1 for f in forms_map.values() if f.get("desainer_name") or f.get("reviewer_name")
        )
        j["checklist_forms_signed"] = signed_forms
        j["checklist_forms_total"] = 3

    conn.close()
    return jobs


@app.post("/api/spk/scan")
async def scan_spk_endpoint(
    file: UploadFile = File(...),
    actor_name: str = Form("Naufal"),
    actor_role: str = Form("Operator Prepress"),
) -> Dict[str, Any]:
    spk_dir = os.path.join(UPLOADS_DIR, "spk_scans")
    os.makedirs(spk_dir, exist_ok=True)
    ext = os.path.splitext(file.filename or "spk.png")[1].lower() or ".png"
    safe_name = f"spk_{int(os.times().elapsed * 1000) if hasattr(os.times(), 'elapsed') else 'scan'}{ext}"
    saved_path = os.path.join(spk_dir, safe_name)

    try:
        content = await file.read()
        with open(saved_path, "wb") as f_out:
            f_out.write(content)

        parsed = scan_spk_file(saved_path)
        parsed["spk_image_url"] = f"/uploads/spk_scans/{safe_name}"

        conn = get_db()
        log_audit(
            conn,
            None,
            actor_name,
            actor_role,
            "SCAN_SPK",
            f"Scan Otomatis Kertas SPK ({file.filename}) -> Job: {parsed.get('job_number')} ({parsed.get('product_name')})",
        )
        conn.commit()
        conn.close()
        return parsed
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Gagal membaca gambar SPK: {e}")


@app.post("/api/spk/scan-sample")
def scan_sample_spk_endpoint(
    actor_name: str = "Naufal",
    actor_role: str = "Operator Prepress",
) -> Dict[str, Any]:
    sample_path = os.path.join(ASSETS_DIR, "sample_spk_wh_mo_05661.png")
    if not os.path.exists(sample_path):
        raise HTTPException(status_code=404, detail="File contoh SPK belum tersedia di folder assets.")
    parsed = scan_spk_file(sample_path)
    parsed["spk_image_url"] = "/assets/sample_spk_wh_mo_05661.png"
    return parsed


@app.post("/api/jobs")
def create_job(req: CreateJobRequest) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO jobs (
                job_number, product_name, customer_name, item_code, package_type, print_method,
                revision_no, target_width_mm, target_height_mm, required_bleed_mm, min_dpi,
                color_spec, expected_nup, layout_width_mm, layout_height_mm, status,
                created_by, created_at, updated_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'IN_PROGRESS', ?, ?, ?, ?)
            """,
            (
                req.job_number.strip(),
                req.product_name.strip(),
                req.customer_name.strip(),
                req.item_code.strip(),
                req.package_type,
                req.print_method,
                req.revision_no,
                req.target_width_mm,
                req.target_height_mm,
                req.required_bleed_mm,
                req.min_dpi,
                req.color_spec,
                req.expected_nup,
                req.layout_width_mm,
                req.layout_height_mm,
                req.created_by,
                now_iso(),
                now_iso(),
                req.notes,
            ),
        )
        job_id = cur.lastrowid
        populate_job_checklist(conn, job_id)
        log_audit(
            conn,
            job_id,
            req.created_by,
            req.created_role,
            "CREATE_JOB",
            f"Membuat Job Pemeriksaan Prepress baru: {req.job_number} - {req.product_name}",
        )
        conn.commit()

        if req.generate_demo_files:
            tmp_dir = os.path.join(UPLOADS_DIR, f"tmp_gen_{job_id}")
            paths = generate_stage_pdfs_for_job(
                tmp_dir,
                req.item_code.strip() or f"JOB-{job_id}",
                req.target_width_mm,
                req.target_height_mm,
                req.required_bleed_mm,
                has_aw_defect=req.demo_has_defect,
            )
            for st in ("ORIGINAL", "AW", "ONEUP", "LAYOUT"):
                _process_and_register_file(
                    conn,
                    job_id,
                    st,
                    paths[st],
                    os.path.basename(paths[st]),
                    req.created_by,
                    req.created_role,
                )
            shutil.rmtree(tmp_dir, ignore_errors=True)
            _run_stage_comparison_internal(conn, job_id, "ORIGINAL", "AW", req.created_by, req.created_role)
            _run_stage_comparison_internal(conn, job_id, "ONEUP", "LAYOUT", req.created_by, req.created_role)

        # Initialize the 3 official checklist forms and apply any SPK overrides
        forms_map = ensure_job_checklist_forms(conn, job_id)
        if req.checklist_overrides:
            for ftype in FORM_TYPES:
                f_obj = forms_map.get(ftype)
                if f_obj:
                    merged_data = f_obj["form_data"]
                    merged_data.update(req.checklist_overrides)
                    conn.execute(
                        """
                        UPDATE job_checklist_forms
                        SET form_data_json = ?, updated_by = ?, updated_at = ?
                        WHERE job_id = ? AND form_type = ?
                        """,
                        (
                            json.dumps(merged_data, ensure_ascii=False),
                            f"{req.created_by} (Auto-Fill SPK)",
                            now_iso(),
                            job_id,
                            ftype,
                        ),
                    )
            conn.commit()

        return {"id": job_id, "job_number": req.job_number}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@app.put("/api/jobs/{job_id}")
@app.post("/api/jobs/{job_id}/update")
def update_job(job_id: int, req: UpdateJobRequest) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    existing = cur.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    try:
        cur.execute(
            """
            UPDATE jobs
            SET job_number = ?,
                product_name = ?,
                customer_name = ?,
                item_code = ?,
                package_type = ?,
                print_method = ?,
                revision_no = ?,
                target_width_mm = ?,
                target_height_mm = ?,
                required_bleed_mm = ?,
                min_dpi = ?,
                color_spec = ?,
                expected_nup = ?,
                layout_width_mm = ?,
                layout_height_mm = ?,
                notes = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                req.job_number.strip(),
                req.product_name.strip(),
                req.customer_name.strip(),
                req.item_code.strip(),
                req.package_type,
                req.print_method,
                req.revision_no,
                req.target_width_mm,
                req.target_height_mm,
                req.required_bleed_mm,
                req.min_dpi,
                req.color_spec.strip(),
                req.expected_nup,
                req.layout_width_mm,
                req.layout_height_mm,
                req.notes.strip(),
                now_iso(),
                job_id,
            ),
        )
        evaluate_job_checklist(conn, job_id)
        ensure_job_checklist_forms(conn, job_id, force_refresh_auto=True)
        log_audit(
            conn,
            job_id,
            req.actor_name,
            req.actor_role,
            "EDIT_JOB",
            f"Memperbarui spesifikasi Job: {req.job_number} - {req.product_name} (R0{req.revision_no})",
            details={
                "target_size_mm": f"{req.target_width_mm}x{req.target_height_mm}",
                "required_bleed_mm": req.required_bleed_mm,
                "min_dpi": req.min_dpi,
                "expected_nup": req.expected_nup,
            },
        )
        conn.commit()
        return {"status": "updated", "id": job_id, "job_number": req.job_number}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@app.delete("/api/jobs/{job_id}")
@app.post("/api/jobs/{job_id}/delete")
def delete_job(
    job_id: int,
    actor_name: str = "Budi Santoso",
    actor_role: str = "Operator Prepress",
) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    existing = cur.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    job_num = existing["job_number"]
    prod_name = existing["product_name"]

    cur.execute("DELETE FROM audit_logs WHERE job_id = ?", (job_id,))
    cur.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    log_audit(
        conn,
        None,
        actor_name,
        actor_role,
        "DELETE_JOB",
        f"Menghapus Job Prepress: {job_num} - {prod_name}",
        details={"deleted_job_id": job_id, "job_number": job_num},
    )
    conn.commit()
    conn.close()

    job_dir = os.path.join(UPLOADS_DIR, f"job_{job_id}")
    if os.path.exists(job_dir):
        shutil.rmtree(job_dir, ignore_errors=True)

    return {"status": "deleted", "id": job_id, "job_number": job_num}


@app.get("/api/jobs/{job_id}")
def get_job_detail(job_id: int) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    job = dict(row)

    # Files per stage (latest + history)
    cur.execute(
        "SELECT * FROM job_files WHERE job_id = ? ORDER BY version DESC, id DESC",
        (job_id,),
    )
    all_files = []
    stage_latest: Dict[str, Dict[str, Any]] = {}
    for r in cur.fetchall():
        d = dict(r)
        d["inspection"] = json.loads(d["inspection_json"])
        all_files.append(d)
        if d["stage"] not in stage_latest:
            stage_latest[d["stage"]] = d

    cur.execute("SELECT * FROM checklist_items WHERE job_id = ? ORDER BY id ASC", (job_id,))
    checklist = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM annotations WHERE job_id = ? ORDER BY pin_number ASC, id ASC", (job_id,))
    annotations = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM comparisons WHERE job_id = ? ORDER BY id DESC", (job_id,))
    comparisons = []
    for r in cur.fetchall():
        c = dict(r)
        c["regions"] = json.loads(c["diff_regions_json"] or "[]")
        comparisons.append(c)

    cur.execute("SELECT * FROM approvals WHERE job_id = ? ORDER BY id ASC", (job_id,))
    approvals = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM audit_logs WHERE job_id = ? ORDER BY id DESC", (job_id,))
    audit_logs = []
    for r in cur.fetchall():
        lg = dict(r)
        lg["details"] = json.loads(lg["details_json"] or "{}")
        audit_logs.append(lg)

    checklist_forms = ensure_job_checklist_forms(conn, job_id)

    conn.close()
    return {
        "job": job,
        "stage_files": stage_latest,
        "file_history": all_files,
        "checklist": checklist,
        "checklist_forms": checklist_forms,
        "annotations": annotations,
        "comparisons": comparisons,
        "approvals": approvals,
        "audit_logs": audit_logs,
    }


@app.get("/api/jobs/{job_id}/checklist-forms")
def get_job_checklist_forms(job_id: int) -> Dict[str, Any]:
    conn = get_db()
    try:
        forms = ensure_job_checklist_forms(conn, job_id)
        if not forms:
            raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
        return forms
    finally:
        conn.close()


@app.put("/api/jobs/{job_id}/checklist-forms/{form_type}")
@app.post("/api/jobs/{job_id}/checklist-forms/{form_type}")
def save_job_checklist_form(
    job_id: int, form_type: str, req: SaveChecklistFormRequest
) -> Dict[str, Any]:
    ftype = form_type.strip().upper()
    if ftype not in FORM_TYPES:
        raise HTTPException(status_code=400, detail="form_type harus AWAL, ONEUP, atau AKHIR")

    conn = get_db()
    try:
        forms = ensure_job_checklist_forms(conn, job_id)
        if not forms:
            raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

        current = forms[ftype]["form_data"]
        current.update(req.form_data or {})

        conn.execute(
            """
            UPDATE job_checklist_forms
            SET form_data_json = ?, updated_by = ?, updated_at = ?
            WHERE job_id = ? AND form_type = ?
            """,
            (
                json.dumps(current, ensure_ascii=False),
                req.actor_name,
                now_iso(),
                job_id,
                ftype,
            ),
        )
        log_audit(
            conn,
            job_id,
            req.actor_name,
            req.actor_role,
            "SAVE_CHECKLIST_FORM",
            f"Menyimpan perubahan pada {FORM_META[ftype]['title']} ({ftype})",
            target_stage=ftype,
        )
        conn.commit()
        updated_forms = ensure_job_checklist_forms(conn, job_id)
        return updated_forms[ftype]
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/checklist-forms/{form_type}/autofill")
def autofill_job_checklist_form(
    job_id: int,
    form_type: str,
    actor_name: str = "Naufal",
    actor_role: str = "Operator Prepress",
) -> Dict[str, Any]:
    ftype = form_type.strip().upper()
    if ftype not in FORM_TYPES:
        raise HTTPException(status_code=400, detail="form_type harus AWAL, ONEUP, atau AKHIR")

    conn = get_db()
    try:
        forms = ensure_job_checklist_forms(conn, job_id, force_refresh_auto=True)
        if not forms:
            raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
        log_audit(
            conn,
            job_id,
            actor_name,
            actor_role,
            "AUTOFILL_CHECKLIST_FORM",
            f"Sinkronisasi Auto-Fill dari parameter file pada {FORM_META[ftype]['title']}",
            target_stage=ftype,
        )
        conn.commit()
        return forms[ftype]
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/checklist-forms/{form_type}/sign")
def sign_job_checklist_form(
    job_id: int, form_type: str, req: SignChecklistFormRequest
) -> Dict[str, Any]:
    ftype = form_type.strip().upper()
    if ftype not in FORM_TYPES:
        raise HTTPException(status_code=400, detail="form_type harus AWAL, ONEUP, atau AKHIR")

    slot = req.slot.strip().upper()
    if slot not in ("DESAINER", "REVIEWER"):
        raise HTTPException(status_code=400, detail="slot harus DESAINER atau REVIEWER")

    conn = get_db()
    try:
        forms = ensure_job_checklist_forms(conn, job_id)
        if not forms:
            raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

        is_unsign = req.action.strip().lower() == "unsign"
        name_val = "" if is_unsign else req.actor_name.strip()
        nik_val = "" if is_unsign else req.actor_nik.strip()
        role_val = "" if is_unsign else req.actor_role.strip()
        ts_val = "" if is_unsign else now_iso()

        if slot == "DESAINER":
            conn.execute(
                """
                UPDATE job_checklist_forms
                SET desainer_name = ?, desainer_nik = ?, desainer_role = ?, desainer_signed_at = ?,
                    updated_by = ?, updated_at = ?
                WHERE job_id = ? AND form_type = ?
                """,
                (name_val, nik_val, role_val, ts_val, req.actor_name, now_iso(), job_id, ftype),
            )
        else:
            conn.execute(
                """
                UPDATE job_checklist_forms
                SET reviewer_name = ?, reviewer_nik = ?, reviewer_role = ?, reviewer_signed_at = ?,
                    updated_by = ?, updated_at = ?
                WHERE job_id = ? AND form_type = ?
                """,
                (name_val, nik_val, role_val, ts_val, req.actor_name, now_iso(), job_id, ftype),
            )

        action_label = "Membatalkan tanda tangan" if is_unsign else "Menandatangani"
        log_audit(
            conn,
            job_id,
            req.actor_name,
            req.actor_role,
            "SIGN_CHECKLIST_FORM",
            f"{action_label} kolom {slot} pada {FORM_META[ftype]['title']}",
            target_stage=ftype,
            details={"form_type": ftype, "slot": slot, "action": req.action},
        )
        conn.commit()
        updated_forms = ensure_job_checklist_forms(conn, job_id)
        return updated_forms[ftype]
    finally:
        conn.close()


@app.get("/api/jobs/{job_id}/checklist-forms/{form_type}/print", response_class=HTMLResponse)
def print_job_checklist_form(job_id: int, form_type: str) -> HTMLResponse:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM jobs WHERE id = ?", (job_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
        html_doc = render_printable_checklist_html(conn, job_id, form_type)
        return HTMLResponse(content=html_doc, status_code=200)
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/upload")
async def upload_stage_file(
    job_id: int,
    stage: str = Form(...),
    uploaded_by: str = Form("Budi Santoso"),
    uploaded_role: str = Form("Operator Prepress"),
    file: UploadFile = File(...),
) -> Dict[str, Any]:
    stage_up = stage.strip().upper()
    if stage_up not in ("ORIGINAL", "AW", "ONEUP", "LAYOUT"):
        raise HTTPException(status_code=400, detail="Stage harus salah satu dari: ORIGINAL, AW, ONEUP, LAYOUT")

    conn = get_db()
    tmp_path = os.path.join(UPLOADS_DIR, f"upload_tmp_{job_id}_{file.filename}")
    try:
        with open(tmp_path, "wb") as f_out:
            content = await file.read()
            f_out.write(content)

        res = _process_and_register_file(
            conn,
            job_id,
            stage_up,
            tmp_path,
            file.filename or f"{stage_up}.pdf",
            uploaded_by,
            uploaded_role,
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Gagal memproses file: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        conn.close()


@app.post("/api/jobs/{job_id}/compare")
def compare_job_stages(job_id: int, req: CompareStagesRequest) -> Dict[str, Any]:
    conn = get_db()
    try:
        return _run_stage_comparison_internal(
            conn,
            job_id,
            req.stage_a.upper(),
            req.stage_b.upper(),
            req.actor_name,
            req.actor_role,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@app.put("/api/jobs/{job_id}/checklist/{item_id}")
def update_checklist_item(
    job_id: int, item_id: int, req: ChecklistUpdateRequest
) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM checklist_items WHERE id = ? AND job_id = ?", (item_id, job_id))
    item = cur.fetchone()
    if not item:
        conn.close()
        raise HTTPException(status_code=404, detail="Item checklist tidak ditemukan.")

    # Guard against accidental human error: if auto_status is FAIL and user sets PASS without override_reason/note
    if (
        item["is_auto"]
        and item["auto_status"] == "FAIL"
        and req.manual_status == "PASS"
        and not (req.override_reason.strip() or req.operator_note.strip())
    ):
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="Peringatan Human Error Prevention: Parameter otomatis mendeteksi FAIL pada item ini. Anda wajib mengisi alasan/catatan override jika ingin meloloskan item ini.",
        )

    cur.execute(
        """
        UPDATE checklist_items
        SET manual_status = ?,
            operator_note = ?,
            override_reason = ?,
            checked_by = ?,
            checked_role = ?,
            checked_at = ?
        WHERE id = ? AND job_id = ?
        """,
        (
            req.manual_status,
            req.operator_note,
            req.override_reason,
            req.actor_name,
            req.actor_role,
            now_iso(),
            item_id,
            job_id,
        ),
    )

    log_audit(
        conn,
        job_id,
        req.actor_name,
        req.actor_role,
        "CHECKLIST_UPDATE",
        f"Update Checklist [{item['code']}] {item['title']} -> {req.manual_status}",
        target_stage=item["stage_target"],
        details={
            "code": item["code"],
            "auto_status": item["auto_status"],
            "manual_status": req.manual_status,
            "note": req.operator_note,
        },
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "item_id": item_id, "manual_status": req.manual_status}


@app.post("/api/jobs/{job_id}/checklist/verify-all-pending")
def verify_all_pending_checklist(
    job_id: int,
    actor_name: str = Form("Budi Santoso"),
    actor_role: str = Form("Operator Prepress"),
) -> Dict[str, Any]:
    """Marks all PENDING or WARN items (that are not auto-FAIL) as PASS with operator attribution."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE checklist_items
        SET manual_status = 'PASS',
            checked_by = ?,
            checked_role = ?,
            checked_at = ?,
            operator_note = CASE WHEN operator_note = '' THEN 'Diverifikasi sesuai standar.' ELSE operator_note END
        WHERE job_id = ? AND auto_status != 'FAIL' AND manual_status != 'FAIL'
        """,
        (actor_name, actor_role, now_iso(), job_id),
    )
    count = cur.rowcount
    log_audit(
        conn,
        job_id,
        actor_name,
        actor_role,
        "CHECKLIST_BATCH_VERIFY",
        f"Verifikasi masal {count} item checklist yang telah memenuhi syarat.",
    )
    conn.commit()
    conn.close()
    return {"verified_count": count}


@app.post("/api/jobs/{job_id}/annotations")
def create_annotation(job_id: int, req: CreateAnnotationRequest) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(MAX(pin_number), 0) as max_p FROM annotations WHERE job_id = ?",
        (job_id,),
    )
    next_pin = int(cur.fetchone()["max_p"]) + 1

    cur.execute(
        """
        INSERT INTO annotations (
            job_id, pin_number, stage, x_pct, y_pct, w_pct, h_pct,
            severity, category, title, comment, status, checklist_code,
            created_by, created_role, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)
        """,
        (
            job_id,
            next_pin,
            req.stage.upper(),
            round(req.x_pct, 2),
            round(req.y_pct, 2),
            round(req.w_pct, 2),
            round(req.h_pct, 2),
            req.severity.upper(),
            req.category,
            req.title.strip(),
            req.comment.strip(),
            req.checklist_code.strip(),
            req.actor_name,
            req.actor_role,
            now_iso(),
        ),
    )
    ann_id = cur.lastrowid

    log_audit(
        conn,
        job_id,
        req.actor_name,
        req.actor_role,
        "ADD_ANNOTATION_PIN",
        f"Menambahkan Pin Masalah #{next_pin} pada [{req.stage}] ({req.severity}): {req.title}",
        target_stage=req.stage.upper(),
        details={
            "pin_number": next_pin,
            "coordinates_pct": {"x": req.x_pct, "y": req.y_pct},
            "severity": req.severity,
            "category": req.category,
        },
    )
    conn.commit()
    conn.close()
    return {"id": ann_id, "pin_number": next_pin}


@app.put("/api/jobs/{job_id}/annotations/{ann_id}")
def update_annotation_status(
    job_id: int, ann_id: int, req: ResolveAnnotationRequest
) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM annotations WHERE id = ? AND job_id = ?", (ann_id, job_id))
    ann = cur.fetchone()
    if not ann:
        conn.close()
        raise HTTPException(status_code=404, detail="Pin anotasi tidak ditemukan.")

    cur.execute(
        """
        UPDATE annotations
        SET status = ?, resolved_by = ?, resolved_note = ?, resolved_at = ?
        WHERE id = ? AND job_id = ?
        """,
        (req.status.upper(), req.actor_name, req.resolved_note, now_iso(), ann_id, job_id),
    )

    log_audit(
        conn,
        job_id,
        req.actor_name,
        req.actor_role,
        "UPDATE_ANNOTATION_PIN",
        f"Update Status Pin #{ann['pin_number']} ({ann['title']}) -> {req.status.upper()}",
        target_stage=ann["stage"],
        details={"pin_number": ann["pin_number"], "status": req.status, "note": req.resolved_note},
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.delete("/api/jobs/{job_id}/annotations/{ann_id}")
def delete_annotation(
    job_id: int,
    ann_id: int,
    actor_name: str = "Andi Wijaya",
    actor_role: str = "QC Prepress Inspector",
) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM annotations WHERE id = ? AND job_id = ?", (ann_id, job_id))
    ann = cur.fetchone()
    if ann:
        cur.execute("DELETE FROM annotations WHERE id = ?", (ann_id,))
        log_audit(
            conn,
            job_id,
            actor_name,
            actor_role,
            "DELETE_ANNOTATION_PIN",
            f"Menghapus Pin Masalah #{ann['pin_number']}: {ann['title']}",
            target_stage=ann["stage"],
        )
        conn.commit()
    conn.close()
    return {"status": "deleted"}


@app.post("/api/jobs/{job_id}/approve")
def submit_job_approval(job_id: int, req: SubmitApprovalRequest) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job = cur.fetchone()
    if not job:
        conn.close()
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    role_stage = req.role_stage.upper()
    if role_stage not in ("OPERATOR", "QC", "SUPERVISOR"):
        conn.close()
        raise HTTPException(status_code=400, detail="Role stage tidak valid.")

    # Replace existing approval for this role_stage if re-signing
    cur.execute(
        "DELETE FROM approvals WHERE job_id = ? AND role_stage = ?",
        (job_id, role_stage),
    )

    ts = now_iso()
    sig_payload = f"{job_id}:{job['job_number']}:{role_stage}:{req.decision}:{req.approver_nik}:{ts}"
    sig_hash = hashlib.sha256(sig_payload.encode("utf-8")).hexdigest()[:24].upper()

    cur.execute(
        """
        INSERT INTO approvals (
            job_id, role_stage, decision, approver_name, approver_nik,
            approver_role_label, notes, signature_hash, signed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            role_stage,
            req.decision.upper(),
            req.approver_name,
            req.approver_nik,
            req.approver_role_label,
            req.notes,
            sig_hash,
            ts,
        ),
    )

    # Determine overall job status based on approvals
    cur.execute("SELECT role_stage, decision FROM approvals WHERE job_id = ?", (job_id,))
    app_map = {r["role_stage"]: r["decision"] for r in cur.fetchall()}

    if req.decision.upper() == "REJECTED":
        new_job_status = "REVISION_REQUIRED"
    elif (
        app_map.get("OPERATOR", "").startswith("APPROV")
        and app_map.get("QC", "").startswith("APPROV")
        and app_map.get("SUPERVISOR", "").startswith("APPROV")
    ):
        new_job_status = "APPROVED"
    elif app_map.get("OPERATOR", "").startswith("APPROV") and app_map.get("QC", "").startswith("APPROV"):
        new_job_status = "WAITING_SUPERVISOR"
    elif app_map.get("OPERATOR", "").startswith("APPROV"):
        new_job_status = "WAITING_QC"
    else:
        new_job_status = "IN_PROGRESS"

    cur.execute(
        "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
        (new_job_status, ts, job_id),
    )

    log_audit(
        conn,
        job_id,
        req.approver_name,
        req.approver_role_label,
        "APPROVAL_SIGNOFF",
        f"Otorisasi [{role_stage}] oleh {req.approver_name} ({req.approver_nik}) -> Keputusan: {req.decision.upper()} (Sign: {sig_hash[:12]})",
        details={
            "role_stage": role_stage,
            "decision": req.decision.upper(),
            "nik": req.approver_nik,
            "signature_hash": sig_hash,
            "notes": req.notes,
            "new_job_status": new_job_status,
        },
    )
    conn.commit()
    conn.close()
    return {
        "status": "ok",
        "job_status": new_job_status,
        "signature_hash": sig_hash,
        "signed_at": ts,
    }


@app.get("/api/jobs/{job_id}/report.pdf")
def export_job_report_pdf(job_id: int) -> FileResponse:
    detail = get_job_detail(job_id)
    job = detail["job"]
    files = list(detail["stage_files"].values())
    checklist = detail["checklist"]
    annotations = detail["annotations"]
    comparisons = detail["comparisons"]
    approvals = detail["approvals"]

    report_dir = os.path.join(UPLOADS_DIR, f"job_{job_id}")
    os.makedirs(report_dir, exist_ok=True)
    pdf_filename = f"Laporan_QC_Prepress_{job['job_number']}.pdf"
    pdf_path = os.path.join(report_dir, pdf_filename)

    generate_qc_report_pdf(
        job, files, checklist, annotations, comparisons, approvals, pdf_path
    )
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=pdf_filename,
    )


@app.get("/api/templates")
def get_checklist_templates() -> List[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM custom_checklist_templates ORDER BY id ASC")
    res = [dict(r) for r in cur.fetchall()]
    conn.close()
    return res


@app.post("/api/templates")
def add_checklist_template(req: CreateTemplateItemRequest) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO custom_checklist_templates
            (code, category, stage_target, title, description, is_auto, auto_rule, mandatory)
            VALUES (?, ?, ?, ?, ?, 0, NULL, ?)
            """,
            (
                req.code.strip().upper(),
                req.category.strip(),
                req.stage_target.strip().upper(),
                req.title.strip(),
                req.description.strip(),
                1 if req.mandatory else 0,
            ),
        )
        new_id = cur.lastrowid
        conn.commit()
        return {"id": new_id, "code": req.code}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@app.get("/api/audit-logs")
def list_all_audit_logs(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.*, j.job_number, j.product_name
        FROM audit_logs a
        LEFT JOIN jobs j ON a.job_id = j.id
        ORDER BY a.id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        d["details"] = json.loads(d["details_json"] or "{}")
        rows.append(d)
    conn.close()
    return rows


# Mount uploads, assets, and static frontend
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_spa() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
