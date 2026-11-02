import html
import json
import sqlite3
from typing import Any, Dict, List, Optional

from backend.database import now_iso

FORM_TYPES = ("AWAL", "ONEUP", "AKHIR")

FORM_META = {
    "AWAL": {
        "title": "CHECK LIST AWAL",
        "subtitle": "DIVISION : DESIGN",
        "stage_hint": "Tahap Awal (File Customer / Artwork)",
    },
    "ONEUP": {
        "title": "CHECK LIST ONEUP",
        "subtitle": "DIVISION : DESIGN",
        "stage_hint": "Tahap Master 1-Up & Pisau Pond",
    },
    "AKHIR": {
        "title": "CHECK LIST AKHIR",
        "subtitle": "DIVISION : DESIGN",
        "stage_hint": "Tahap Final (File Desain, Diecut, Pre Press & Finishing)",
    },
}


def _fmt_num(val: Any) -> str:
    if val is None or val == "":
        return ""
    try:
        f = float(val)
        if abs(f - round(f)) < 0.05:
            return str(int(round(f)))
        return f"{f:.1f}"
    except Exception:
        return str(val)


def build_autofill_data(conn: sqlite3.Connection, job_id: int, form_type: str) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job_row = cur.fetchone()
    if not job_row:
        return {}
    job = dict(job_row)

    # Fetch latest file per stage
    cur.execute(
        """
        SELECT jf.*
        FROM job_files jf
        INNER JOIN (
            SELECT stage, MAX(version) AS max_ver
            FROM job_files
            WHERE job_id = ?
            GROUP BY stage
        ) latest ON jf.stage = latest.stage AND jf.version = latest.max_ver
        WHERE jf.job_id = ?
        """,
        (job_id, job_id),
    )
    stage_files: Dict[str, Dict[str, Any]] = {}
    for r in cur.fetchall():
        d = dict(r)
        try:
            d["inspection"] = json.loads(d.get("inspection_json") or "{}")
        except Exception:
            d["inspection"] = {}
        stage_files[d["stage"]] = d

    # Choose primary inspection source based on form_type
    orig_file = stage_files.get("ORIGINAL")
    aw_file = stage_files.get("AW")
    oneup_file = stage_files.get("ONEUP")
    layout_file = stage_files.get("LAYOUT")

    if form_type == "AWAL":
        primary_file = aw_file or orig_file or oneup_file or layout_file
        preview_source = aw_file or orig_file
    elif form_type == "ONEUP":
        primary_file = oneup_file or aw_file or orig_file or layout_file
        preview_source = oneup_file or aw_file
    else:  # AKHIR
        primary_file = oneup_file or layout_file or aw_file or orig_file
        preview_source = layout_file or oneup_file or aw_file

    customer_source_file = orig_file or aw_file or primary_file
    insp = (primary_file or {}).get("inspection", {})
    oneup_insp = (oneup_file or primary_file or {}).get("inspection", {})
    layout_insp = (layout_file or {}).get("inspection", {})

    # Check N-Up from comparisons if available
    cur.execute(
        "SELECT matched_nup_count FROM comparisons WHERE job_id = ? AND (stage_a = 'LAYOUT' OR stage_b = 'LAYOUT') ORDER BY id DESC LIMIT 1",
        (job_id,),
    )
    comp_row = cur.fetchone()
    matched_nup = comp_row["matched_nup_count"] if comp_row and comp_row["matched_nup_count"] else job.get("expected_nup", 4)

    # Determine customer file format flags
    cust_filename = (customer_source_file or {}).get("filename", "").lower()
    cust_fmt_family = ((customer_source_file or {}).get("inspection", {}).get("format_family") or "").lower()
    fmt_corel = cust_filename.endswith(".cdr") or "corel" in cust_fmt_family
    fmt_psd = cust_filename.endswith(".psd") or "photoshop" in cust_fmt_family
    fmt_pdf = cust_filename.endswith(".pdf") or "pdf" in cust_fmt_family
    fmt_ai = cust_filename.endswith((".ai", ".eps")) or "illustrator" in cust_fmt_family
    fmt_jpeg = cust_filename.endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff"))

    # Color space flags
    color_spaces = [str(c).upper() for c in insp.get("color_spaces", [])]
    has_rgb = bool(insp.get("has_rgb", False) or "RGB" in color_spaces)
    has_cmyk = bool("CMYK" in color_spaces or not has_rgb)
    spot_colors = [str(s) for s in insp.get("spot_colors", [])]
    non_dieline_spots = [
        s for s in spot_colors
        if not any(k in s.upper() for k in ("DIELINE", "CUT", "CREASE", "PISAU", "RIT", "POND"))
    ]
    has_pantone = bool(len(non_dieline_spots) > 0 or "PANTONE" in (job.get("color_spec") or "").upper())

    # Bleed, DPI, Fonts, Overprint
    detected_bleed = oneup_insp.get("detected_bleed_mm") if form_type in ("ONEUP", "AKHIR") else insp.get("detected_bleed_mm")
    if detected_bleed is None or float(detected_bleed or 0) <= 0:
        detected_bleed = insp.get("detected_bleed_mm") or job.get("required_bleed_mm") or 3.0

    min_dpi = insp.get("min_dpi") or job.get("min_dpi") or 300
    font_count = int(insp.get("font_count", 0) or 0)
    has_overprint = bool(insp.get("has_overprint", False) or oneup_insp.get("has_overprint", False))
    has_dieline = bool(oneup_insp.get("has_dieline_spot", False) or insp.get("has_dieline_spot", False))

    # Preview image URL
    preview_url = ""
    if preview_source and preview_source.get("preview_path"):
        rel_prev = str(preview_source["preview_path"]).replace("\\", "/")
        if "uploads/" in rel_prev:
            preview_url = "/" + rel_prev[rel_prev.index("uploads/"):]
        else:
            preview_url = f"/uploads/{rel_prev.lstrip('/')}"

    # Nama File per form
    if form_type == "AWAL":
        nama_file = (aw_file or orig_file or {}).get("filename") or f"{job['item_code']} - {job['product_name']}"
    elif form_type == "ONEUP":
        nama_file = (oneup_file or aw_file or {}).get("filename") or f"{job['item_code']} - {job['product_name']}"
    else:
        nama_file = (layout_file or oneup_file or aw_file or {}).get("filename") or f"{job['item_code']} - {job['product_name']}"

    # Warna Cetak & Pantone string
    warna_cetak_str = "CMYK" if has_cmyk and not has_rgb else ("RGB + CMYK" if has_rgb else "CMYK")
    if non_dieline_spots:
        pantone_str = ", ".join(non_dieline_spots)
    elif "PANTONE" in (job.get("color_spec") or "").upper():
        pantone_str = job.get("color_spec", "")
    else:
        pantone_str = "-"

    # Dimensions
    target_w = _fmt_num(job.get("target_width_mm", 120))
    target_h = _fmt_num(job.get("target_height_mm", 180))
    oneup_w = _fmt_num(oneup_insp.get("trim_width_mm") or job.get("target_width_mm", 120))
    oneup_h = _fmt_num(oneup_insp.get("trim_height_mm") or job.get("target_height_mm", 180))
    layout_w = _fmt_num(layout_insp.get("trim_width_mm") or job.get("layout_width_mm", 290))
    layout_h = _fmt_num(layout_insp.get("trim_height_mm") or job.get("layout_height_mm", 410))

    data: Dict[str, Any] = {
        # Top info
        "customer": job.get("customer_name", ""),
        "nama_file": nama_file,

        # Row 1: Format file Customer
        "fmt_corel": fmt_corel,
        "fmt_psd": fmt_psd,
        "fmt_pdf": fmt_pdf,
        "fmt_ai": fmt_ai,
        "fmt_jpeg": fmt_jpeg,
        "fmt_lainnya": False,
        "fmt_lainnya_text": "",

        # Row 2: Format Warna dlm File
        "warna_rgb": has_rgb,
        "warna_cmyk": has_cmyk,
        "warna_pantone": has_pantone,

        # Row 3: Acuan Desain
        "acuan_file_customer": bool(orig_file is not None),
        "acuan_desain_prima": bool(orig_file is None),
        "acuan_existing_customer": False,
        "acuan_file_lama": False,

        # Row 4: Format Gambar dlm File
        "img_rgb": has_rgb,
        "img_cmyk": has_cmyk and not has_rgb,
        "img_pantone": has_pantone,

        # Row 5: Shadow
        "shadow_sudah_expand": True,
        "shadow_belum_expand": False,
        "shadow_100_black": True,
        "shadow_4_warna": False,

        # Row 6: Gradasi
        "gradasi_sesuai": not has_rgb,
        "gradasi_perlu_dirubah": has_rgb,

        # Row 7: Transparasi
        "transparasi_pct": "0",

        # Row 8: Acuan Warna
        "acuan_warna_contoh_customer": True,
        "acuan_warna_standart_mesin": True,
        "acuan_warna_pantone_tc": has_pantone,
        "acuan_warna_acc_foto": False,
        "acuan_warna_acc_datang": False,
        "acuan_warna_print_digital": False,
        "acuan_warna_color_range": False,
        "acuan_warna_cetakan_lama": False,
        "acuan_warna_lainnya": False,
        "acuan_warna_lainnya_text": "",

        # Row 9: Bleed/Sisiran
        "bleed_mm": _fmt_num(detected_bleed),
        "jarak_konten_mm": "3",
        "cek_typo_gpt": not has_rgb,

        # Row 10: Kualitas Gambar
        "dpi_value": _fmt_num(min_dpi),
        "img_berupa_link": False,
        "img_embedded_link": True,

        # Row 11: Outline Gambar/Teks
        "outline_pt": _fmt_num(insp.get("min_font_size_pt") or 0.5),
        "outline_expand_line": True,
        "outline_edited_line": False,

        # Row 12: Komposisi Hitam
        "hitam_4_warna": False,
        "hitam_100_black": True,
        "hitam_rich_cyan30": False,

        # Row 13: Black Overprint
        "overprint_fill": has_overprint,
        "overprint_trapp": has_overprint,

        # Row 14: Format Font & QR / Barcode
        "font_editable": font_count > 0,
        "font_convert": font_count == 0,
        "barcode_jpeg_png": False,
        "barcode_vector": True,

        # Row 15: Warna Cetak
        "warna_cetak": warna_cetak_str,
        "kode_pantone_tc": pantone_str,

        # Preview & Catatan
        "preview_image_url": preview_url,
        "show_preview_image": bool(preview_url),
        "preview_notes": "",
        "catatan_notes": job.get("notes", "") if form_type == "AWAL" else "",
    }

    if form_type == "AKHIR":
        page_count = int(layout_insp.get("page_count") or insp.get("page_count") or 1)
        data.update(
            {
                # SECTION 2: DIECUT
                "diecut_src_pacdora": False,
                "diecut_src_dummy_polos": True,
                "diecut_src_contoh_customer": True,
                "diecut_src_file_lama": False,

                "diecut_val_dummy_polos": True,
                "diecut_val_dummy_digital": True,
                "diecut_val_existing_customer": False,
                "diecut_val_pisau_lama": False,

                "ukuran_terbentang_mm": f"{oneup_w} x {oneup_h}",
                "ukuran_dimensi_jadi_mm": f"{target_w} x {target_h}",

                "lidah_kanan": True,
                "lidah_kiri": False,
                "panjang_lidah_lem_mm": "15",

                "film_mirror": False,
                "film_terbaca": True,

                "pisau_rit": has_dieline,
                "pisau_pisau": has_dieline,
                "pisau_perporasi": False,
                "pisau_perporasi_text": "",
                "pisau_overprint_diecut": has_dieline and has_overprint,
                "pisau_mata_ayam": False,
                "mata_ayam_mm": "",
                "mata_ayam_pcs": "",

                # SECTION 3: PRE PRESS
                "uk_potong_kertas_mm": f"{layout_w} x {layout_h}",
                "jumlah_up_druk": str(matched_nup),
                "jarak_up_mm": "6",

                "muka_1": page_count == 1,
                "muka_2": page_count > 1,

                "layout_balik_kertas": False,
                "layout_lain_plat": True,
                "layout_lain_unleg": False,

                "nomer_diecut_logo_pp": True,

                "reg_cetak_kress": True,
                "reg_signature": True,
                "reg_panah_unleg": True,
                "reg_palete_warna_bg": True,

                "metode_manual": False,
                "metode_prinnect": True,

                "mesin_pm": False,
                "mesin_sm": False,
                "mesin_cd": True,
                "mesin_gto": False,
                "mesin_sord": False,
                "mesin_sors": False,
                "mesin_toko": False,

                "unleg_mesin_text": "12 mm",
                "unleg_dari_kress": True,
                "unleg_dari_blok": False,

                # SECTION 4: FINSHING
                "spotuv_ketebalan": "0.5 pt",
                "spotuv_jumlah_up": str(matched_nup),

                "hotprint_kode": "-",
                "hotprint_ukuran_klise": "-",
                "hotprint_ketebalan": "-",
                "hotprint_jumlah_up": "",

                "emboss_check": False,
                "deboss_check": False,
                "emboss_ukuran_klise": "-",
                "emboss_ketebalan": "-",
                "emboss_jumlah_up": "",

                "lam_doff": False,
                "lam_glossy": True,
                "batas_lam_glossy_mm": "3",
            }
        )

    return data


def ensure_job_checklist_forms(
    conn: sqlite3.Connection,
    job_id: int,
    force_refresh_auto: bool = False,
) -> Dict[str, Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job_row = cur.fetchone()
    if not job_row:
        return {}
    job = dict(job_row)

    result: Dict[str, Dict[str, Any]] = {}
    for ftype in FORM_TYPES:
        cur.execute(
            "SELECT * FROM job_checklist_forms WHERE job_id = ? AND form_type = ?",
            (job_id, ftype),
        )
        existing = cur.fetchone()
        auto_data = build_autofill_data(conn, job_id, ftype)

        if existing is None:
            # Default signatures for clean approved demo job (JOB-2026-1002)
            desainer_name = ""
            desainer_nik = ""
            desainer_role = ""
            desainer_signed_at = ""
            reviewer_name = ""
            reviewer_nik = ""
            reviewer_role = ""
            reviewer_signed_at = ""

            if job.get("status") == "APPROVED":
                desainer_name = "Halim" if ftype != "ONEUP" else "Naufal"
                desainer_nik = "PRP-004" if desainer_name == "Halim" else "PRP-003"
                desainer_role = "Operator Prepress"
                desainer_signed_at = now_iso()
                reviewer_name = "Dina" if ftype != "AKHIR" else "Dio"
                reviewer_nik = "PRP-002" if reviewer_name == "Dina" else "PRP-001"
                reviewer_role = "QC Prepress" if reviewer_name == "Dina" else "Kepala Prepress"
                reviewer_signed_at = now_iso()
            elif job.get("job_number") == "JOB-2026-1001" and ftype == "AWAL":
                desainer_name = "Naufal"
                desainer_nik = "PRP-003"
                desainer_role = "Operator Prepress"
                desainer_signed_at = now_iso()

            cur.execute(
                """
                INSERT INTO job_checklist_forms (
                    job_id, form_type, form_data_json,
                    desainer_name, desainer_nik, desainer_role, desainer_signed_at,
                    reviewer_name, reviewer_nik, reviewer_role, reviewer_signed_at,
                    updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    ftype,
                    json.dumps(auto_data, ensure_ascii=False),
                    desainer_name,
                    desainer_nik,
                    desainer_role,
                    desainer_signed_at,
                    reviewer_name,
                    reviewer_nik,
                    reviewer_role,
                    reviewer_signed_at,
                    "Sistem Auto-Fill",
                    now_iso(),
                ),
            )
            conn.commit()
            cur.execute(
                "SELECT * FROM job_checklist_forms WHERE job_id = ? AND form_type = ?",
                (job_id, ftype),
            )
            existing = cur.fetchone()
        elif force_refresh_auto:
            try:
                saved_data = json.loads(existing["form_data_json"] or "{}")
            except Exception:
                saved_data = {}
            # Preserve manual notes while updating file-driven parameters
            keep_preview_notes = saved_data.get("preview_notes", "")
            keep_catatan_notes = saved_data.get("catatan_notes", "")
            saved_data.update(auto_data)
            if keep_preview_notes:
                saved_data["preview_notes"] = keep_preview_notes
            if keep_catatan_notes:
                saved_data["catatan_notes"] = keep_catatan_notes
            cur.execute(
                """
                UPDATE job_checklist_forms
                SET form_data_json = ?, updated_by = 'Sistem Auto-Fill', updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(saved_data, ensure_ascii=False), now_iso(), existing["id"]),
            )
            conn.commit()
            cur.execute("SELECT * FROM job_checklist_forms WHERE id = ?", (existing["id"],))
            existing = cur.fetchone()

        row_dict = dict(existing)
        try:
            form_data = json.loads(row_dict.pop("form_data_json") or "{}")
        except Exception:
            form_data = {}
        # Ensure any newly added keys from auto_data exist
        for k, v in auto_data.items():
            if k not in form_data:
                form_data[k] = v
        # Keep preview_image_url up-to-date with latest uploaded stage file
        if auto_data.get("preview_image_url"):
            form_data["preview_image_url"] = auto_data["preview_image_url"]

        row_dict["form_data"] = form_data
        row_dict["meta"] = FORM_META.get(ftype, {})
        row_dict["is_complete"] = bool(row_dict.get("desainer_name") and row_dict.get("reviewer_name"))
        result[ftype] = row_dict

    return result


def _chk(data: Dict[str, Any], key: str) -> str:
    return "checked" if data.get(key) else ""


def _val(data: Dict[str, Any], key: str) -> str:
    return html.escape(str(data.get(key, "") or ""))


def _render_common_design_rows_html(d: Dict[str, Any], include_format_customer: bool = True, compact: bool = False) -> str:
    w_rgb = "105px" if compact else "110px"
    w_cmyk = "100px"
    w_grad = "155px" if compact else "165px"
    w_trans = "90px" if compact else "100px"
    w_bleed = "40px" if compact else "45px"
    w_out = "50px" if compact else "55px"
    w_blk1 = "80px" if compact else "85px"
    w_blk2 = "85px" if compact else "90px"
    w_fnt2 = "75px" if compact else "80px"
    w_wc = "120px" if compact else "130px"
    w_ptc = "220px" if compact else "240px"
    sec_end_cls = ' class="section-end"' if compact else ""

    row1 = ""
    if include_format_customer:
        side_td = '<td rowspan="15" class="side-cat"><div class="vertical-text">FILE DESAIN</div></td>' if compact else ""
        row1 = f"""
        <tr>
            {side_td}
            <td class="label-col">Format file Customer{" :" if compact else ""}</td>
            <td class="colon-col">{"" if compact else ":"}</td>
            <td>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "fmt_corel")}> Corel</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "fmt_psd")}> PSD</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "fmt_pdf")}> PDF</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "fmt_ai")}> Illustrator</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "fmt_jpeg")}> JPEG</label>
                <label class="checkbox-group" style="margin-right: 0;"><input type="checkbox" {_chk(d, "fmt_lainnya")}> Lainnya<input type="text" class="dotted-input" style="width: 75px;" value="{_val(d, "fmt_lainnya_text")}"></label>
            </td>
        </tr>
        """

    return f"""
        {row1}
        <tr>
            <td class="label-col">Format Warna dlm File{" :" if not include_format_customer or compact else ""}</td>
            <td class="colon-col">{"" if not include_format_customer or compact else ":"}</td>
            <td>
                <label class="checkbox-group" style="width: {w_rgb};"><input type="checkbox" {_chk(d, "warna_rgb")}> RGB</label>
                <label class="checkbox-group" style="width: {w_cmyk};"><input type="checkbox" {_chk(d, "warna_cmyk")}> CMYK</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "warna_pantone")}> Pantone</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Acuan Desain{" :" if not include_format_customer or compact else ""}</td>
            <td class="colon-col">{"" if not include_format_customer or compact else ":"}</td>
            <td>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "acuan_file_customer")}> File Customer</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "acuan_desain_prima")}> Desain Prima</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "acuan_existing_customer")}> Existing/offset Customer</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "acuan_file_lama")}> File Lama</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Format Gambar dlm File{" :" if not include_format_customer or compact else ""}</td>
            <td class="colon-col">{"" if not include_format_customer or compact else ":"}</td>
            <td>
                <label class="checkbox-group" style="width: {w_rgb};"><input type="checkbox" {_chk(d, "img_rgb")}> RGB</label>
                <label class="checkbox-group" style="width: {w_cmyk};"><input type="checkbox" {_chk(d, "img_cmyk")}> CMYK</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "img_pantone")}> Pantone</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Shadow</td>
            <td class="colon-col">:</td>
            <td>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "shadow_sudah_expand")}> Sudah di Expand</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "shadow_belum_expand")}> Belum Di Expand</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "shadow_100_black")}> 100% Black</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "shadow_4_warna")}> 4 Warna</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Gradasi</td>
            <td class="colon-col">:</td>
            <td>
                <label class="checkbox-group" style="width: {w_grad};"><input type="checkbox" {_chk(d, "gradasi_sesuai")}> Sesuai File</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "gradasi_perlu_dirubah")}> Perlu dirubah</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Transparasi</td>
            <td class="colon-col">:</td>
            <td>
                <input type="text" class="dotted-input" style="width: {w_trans};" value="{_val(d, "transparasi_pct")}"> <b>%</b>
            </td>
        </tr>
        <tr>
            <td class="label-col">Acuan Warna</td>
            <td class="colon-col">:</td>
            <td>
                <div style="margin-bottom: 3px;">
                    <label class="checkbox-group" style="width: {w_rgb};"><input type="checkbox" {_chk(d, "acuan_warna_contoh_customer")}> Contoh Customer</label>
                    <label class="checkbox-group" style="width: {w_cmyk};"><input type="checkbox" {_chk(d, "acuan_warna_standart_mesin")}> Standart Mesin</label>
                    <label class="checkbox-group" style="width: 85px;"><input type="checkbox" {_chk(d, "acuan_warna_pantone_tc")}> Pantone/TC</label>
                    <label class="checkbox-group" style="width: 78px;"><input type="checkbox" {_chk(d, "acuan_warna_acc_foto")}> ACC Foto</label>
                    <label class="checkbox-group" style="margin-right: 0;"><input type="checkbox" {_chk(d, "acuan_warna_acc_datang")}> ACC Datang</label>
                </div>
                <div>
                    <label class="checkbox-group" style="width: {w_rgb};"><input type="checkbox" {_chk(d, "acuan_warna_print_digital")}> Print Digital</label>
                    <label class="checkbox-group" style="width: {w_cmyk};"><input type="checkbox" {_chk(d, "acuan_warna_color_range")}> Color Range</label>
                    <label class="checkbox-group" style="width: 85px;"><input type="checkbox" {_chk(d, "acuan_warna_cetakan_lama")}> Cetakan Lama</label>
                    <label class="checkbox-group" style="margin-right: 0;"><input type="checkbox" {_chk(d, "acuan_warna_lainnya")}> Lainnya<input type="text" class="dotted-input" style="width: 90px;" value="{_val(d, "acuan_warna_lainnya_text")}"></label>
                </div>
            </td>
        </tr>
        <tr>
            <td class="label-col">Bleed/Sisiran</td>
            <td class="colon-col">:</td>
            <td>
                <input type="text" class="dotted-input" style="width: {w_bleed};" value="{_val(d, "bleed_mm")}">mm
                <span style="margin-left: 8px;">Jarak Konten dgn Potong/ Lipat : <input type="text" class="dotted-input" style="width: {w_bleed};" value="{_val(d, "jarak_konten_mm")}">mm</span>
                <span class="inline-bold" style="margin-left: 35px;">Cek Typo GPT</span>
                <label class="checkbox-group" style="margin-left: 4px;"><input type="checkbox" style="margin:0;" {_chk(d, "cek_typo_gpt")}></label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Kualitas Gambar</td>
            <td class="colon-col">:</td>
            <td>
                <input type="text" class="dotted-input" style="width: {w_bleed};" value="{_val(d, "dpi_value")}"> dpi
                <span style="margin-left: 35px;">Format Gambar &nbsp;: </span>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "img_berupa_link")}> Berupa Link</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "img_embedded_link")}> Embedded Link</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Outline Gambar/Teks</td>
            <td class="colon-col">:</td>
            <td>
                <input type="text" class="dotted-input" style="width: {w_out};" value="{_val(d, "outline_pt")}">pt
                <span style="margin-left: 30px;"></span>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "outline_expand_line")}> Expand Line</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "outline_edited_line")}> Edited Line</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Komposisi Hitam :</td>
            <td class="colon-col"></td>
            <td>
                <label class="checkbox-group" style="width: {w_blk1};"><input type="checkbox" {_chk(d, "hitam_4_warna")}> 4 WARNA</label>
                <label class="checkbox-group" style="width: {w_blk2};"><input type="checkbox" {_chk(d, "hitam_100_black")}> 100 % Black</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "hitam_rich_cyan30")}> 100 % Black + Cyan 30 %</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Black Overprint :</td>
            <td class="colon-col"></td>
            <td>
                <label class="checkbox-group" style="width: {w_blk1};"><input type="checkbox" {_chk(d, "overprint_fill")}> Overprint Fill</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "overprint_trapp")}> Overlap Outline (Trapp)</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Format Font :</td>
            <td class="colon-col"></td>
            <td>
                <label class="checkbox-group" style="width: {w_blk1};"><input type="checkbox" {_chk(d, "font_editable")}> Editable</label>
                <label class="checkbox-group" style="width: {w_fnt2};"><input type="checkbox" {_chk(d, "font_convert")}> Convert</label>
                <span class="inline-bold" style="margin-right: 5px;">QR / Barcode :</span>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "barcode_jpeg_png")}> Jpeg / Png</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "barcode_vector")}> Vector</label>
            </td>
        </tr>
        <tr{sec_end_cls}>
            <td class="label-col">Warna Cetak</td>
            <td class="colon-col">:</td>
            <td>
                <input type="text" class="dotted-input" style="width: {w_wc};" value="{_val(d, "warna_cetak")}">
                <span style="margin-left: 15px;">Kode Pantone/TC<input type="text" class="dotted-input" style="width: {w_ptc};" value="{_val(d, "kode_pantone_tc")}"></span>
            </td>
        </tr>
    """


def render_printable_checklist_html(conn: sqlite3.Connection, job_id: int, form_type: str) -> str:
    ftype = form_type.upper()
    if ftype not in FORM_TYPES:
        ftype = "AWAL"

    forms = ensure_job_checklist_forms(conn, job_id)
    form_obj = forms.get(ftype, {})
    d = form_obj.get("form_data", {})

    desainer_name = form_obj.get("desainer_name") or ""
    desainer_signed_at = form_obj.get("desainer_signed_at") or ""
    reviewer_name = form_obj.get("reviewer_name") or ""
    reviewer_signed_at = form_obj.get("reviewer_signed_at") or ""

    desainer_line = f"( <b>{html.escape(desainer_name)}</b> )" if desainer_name else "( ____________________ )"
    reviewer_line = f"( <b>{html.escape(reviewer_name)}</b> )" if reviewer_name else "( ____________________ )"
    desainer_sub = f"<div style='font-size:9.5px;color:#333;font-weight:normal;'>{html.escape(desainer_signed_at)}</div>" if desainer_signed_at else ""
    reviewer_sub = f"<div style='font-size:9.5px;color:#333;font-weight:normal;'>{html.escape(reviewer_signed_at)}</div>" if reviewer_signed_at else ""

    prev_img_html = ""
    if d.get("preview_image_url") and d.get("show_preview_image", True):
        prev_img_html = f'<div style="text-align:center;margin:4px 0 8px;"><img src="{html.escape(str(d["preview_image_url"]))}" style="max-height:210px;max-width:95%;object-fit:contain;border:1px solid #ccc;"></div>'

    if ftype == "AWAL":
        body_rows = _render_common_design_rows_html(d, include_format_customer=True, compact=False) + f"""
        <tr>
            <td colspan="3" class="preview-box">
                <div style="font-weight: bold; margin-bottom: 5px;">PREVIEW</div>
                {prev_img_html}
                <textarea class="textarea-notes" style="height: {75 if prev_img_html else 230}px;">{_val(d, "preview_notes")}</textarea>
                <div class="catatan-section">
                    <div style="font-weight: bold; margin-bottom: 5px;">CATATAN</div>
                    <textarea class="textarea-notes" style="height: 60px;">{_val(d, "catatan_notes")}</textarea>
                </div>
            </td>
        </tr>
        """
        header_title = "CHECK LIST AWAL<br>DIVISION : DESIGN"
    elif ftype == "ONEUP":
        body_rows = f"""
        <tr>
            <td colspan="3" class="preview-box">
                <div style="font-weight: bold; margin-bottom: 5px;">PREVIEW</div>
                {prev_img_html}
                <textarea class="textarea-notes" style="height: {120 if prev_img_html else 375}px;">{_val(d, "preview_notes")}</textarea>
            </td>
        </tr>
        """ + _render_common_design_rows_html(d, include_format_customer=False, compact=False)
        header_title = "CHECK LIST ONEUP<br>DIVISION : DESIGN"
    else:  # AKHIR
        body_rows = _render_common_design_rows_html(d, include_format_customer=True, compact=True) + f"""
        <!-- SECTION 2: DIECUT -->
        <tr>
            <td rowspan="6" class="side-cat"><div class="vertical-text">DIECUT</div></td>
            <td class="label-col">Source Diecut :</td>
            <td class="colon-col"></td>
            <td>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "diecut_src_pacdora")}> Pacdora</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "diecut_src_dummy_polos")}> Develop Dummy Polos</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "diecut_src_contoh_customer")}> Contoh Customer</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "diecut_src_file_lama")}> File Lama</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Validasi Diecut/Pisau :</td>
            <td class="colon-col"></td>
            <td>
                <label class="checkbox-multiline"><input type="checkbox" {_chk(d, "diecut_val_dummy_polos")}> <span>Dummy<br>Polos</span></label>
                <label class="checkbox-multiline"><input type="checkbox" {_chk(d, "diecut_val_dummy_digital")}> <span>Dummy<br>Digital</span></label>
                <label class="checkbox-multiline"><input type="checkbox" {_chk(d, "diecut_val_existing_customer")}> <span>Existing<br>Customer</span></label>
                <label class="checkbox-group" style="vertical-align: top; margin-top: 4px;"><input type="checkbox" {_chk(d, "diecut_val_pisau_lama")}> Pisau Lama</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">UkuranTerbentang/Terbuka :</td>
            <td class="colon-col"></td>
            <td>
                <input type="text" class="dotted-input" style="width: 120px;" value="{_val(d, "ukuran_terbentang_mm")}">mm
                <span class="inline-bold" style="margin-left: 25px;">Ukuran Dimensi Jadi :</span>
                <input type="text" class="dotted-input" style="width: 120px;" value="{_val(d, "ukuran_dimensi_jadi_mm")}">mm
            </td>
        </tr>
        <tr>
            <td class="label-col">Posisi Lidah :</td>
            <td class="colon-col"></td>
            <td>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "lidah_kanan")}> Kanan Desain</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "lidah_kiri")}> Kiri Desain</label>
                <span class="inline-bold" style="margin-left: 5px;">Panjang Lidah Lem:</span>
                <input type="text" class="dotted-input" style="width: 120px;" value="{_val(d, "panjang_lidah_lem_mm")}">mm
            </td>
        </tr>
        <tr>
            <td class="label-col">Setingan Film :</td>
            <td class="colon-col"></td>
            <td>
                <label class="checkbox-group" style="width: 90px;"><input type="checkbox" {_chk(d, "film_mirror")}> Mirror</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "film_terbaca")}> Terbaca (Untuk Eflute)</label>
            </td>
        </tr>
        <tr class="section-end">
            <td class="label-col">Pisau / Rit :</td>
            <td class="colon-col"></td>
            <td>
                <div style="margin-bottom: 3px;">
                    <label class="checkbox-group" style="width: 90px;"><input type="checkbox" {_chk(d, "pisau_rit")}> Rit</label>
                    <label class="checkbox-group" style="width: 75px;"><input type="checkbox" {_chk(d, "pisau_pisau")}> Pisau</label>
                    <label class="checkbox-group" style="width: 135px;"><input type="checkbox" {_chk(d, "pisau_perporasi")}> Perporasi<input type="text" class="dotted-input" style="width: 50px;" value="{_val(d, "pisau_perporasi_text")}"></label>
                    <label class="checkbox-group"><input type="checkbox" {_chk(d, "pisau_overprint_diecut")}> Overprint Diecut</label>
                </div>
                <div>
                    <label class="checkbox-group"><input type="checkbox" {_chk(d, "pisau_mata_ayam")}> Mata Ayam<input type="text" class="dotted-input" style="width: 30px;" value="{_val(d, "mata_ayam_mm")}">MM, Jumlah<input type="text" class="dotted-input" style="width: 45px;" value="{_val(d, "mata_ayam_pcs")}">pcs</label>
                </div>
            </td>
        </tr>

        <!-- SECTION 3: PRE PRESS -->
        <tr>
            <td rowspan="10" class="side-cat"><div class="vertical-text">PRE PRESS</div></td>
            <td class="label-col">Uk. Potong Kertas</td>
            <td class="colon-col">:</td>
            <td><input type="text" class="dotted-input" style="width: 120px;" value="{_val(d, "uk_potong_kertas_mm")}">mm</td>
        </tr>
        <tr>
            <td class="label-col">Jumlah Up / Druk</td>
            <td class="colon-col">:</td>
            <td><input type="text" class="dotted-input" style="width: 120px;" value="{_val(d, "jumlah_up_druk")}">up</td>
        </tr>
        <tr>
            <td class="label-col">Jarak Up</td>
            <td class="colon-col">:</td>
            <td><input type="text" class="dotted-input" style="width: 120px;" value="{_val(d, "jarak_up_mm")}">mm</td>
        </tr>
        <tr>
            <td class="label-col">Jumlah Sisi/Muka :</td>
            <td class="colon-col"></td>
            <td>
                <label class="checkbox-group" style="width: 100px;"><input type="checkbox" {_chk(d, "muka_1")}> 1 Muka</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "muka_2")}> 2 Muka</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Jenis Layout :</td>
            <td class="colon-col"></td>
            <td>
                <label class="checkbox-group" style="width: 100px;"><input type="checkbox" {_chk(d, "layout_balik_kertas")}> Balik Kertas</label>
                <label class="checkbox-group" style="width: 85px;"><input type="checkbox" {_chk(d, "layout_lain_plat")}> Lain Plat</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "layout_lain_unleg")}> Lain Unleg</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Nomer Diecut :</td>
            <td class="colon-col"></td>
            <td>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "nomer_diecut_logo_pp")}> Logo PP</label>
            </td>
        </tr>
        <tr>
            <td colspan="3">
                <span class="inline-bold">Register Cetak & KRESS (HARUS REGISTER)</span>
                <label class="checkbox-group" style="margin-left: 4px; margin-right: 15px;"><input type="checkbox" style="margin:0;" {_chk(d, "reg_cetak_kress")}></label>
                <span class="inline-bold">SIGNATURE</span>
                <label class="checkbox-group" style="margin-left: 4px; margin-right: 15px;"><input type="checkbox" style="margin:0;" {_chk(d, "reg_signature")}></label>
                <span class="inline-bold">PANAH UNLEG</span>
                <label class="checkbox-group" style="margin-left: 4px; margin-right: 15px;"><input type="checkbox" style="margin:0;" {_chk(d, "reg_panah_unleg")}></label>
                <span class="inline-bold">PALETE WARNA BG</span>
                <label class="checkbox-group" style="margin-left: 4px; margin-right: 0;"><input type="checkbox" style="margin:0;" {_chk(d, "reg_palete_warna_bg")}></label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Metode Layout :</td>
            <td class="colon-col"></td>
            <td>
                <label class="checkbox-group" style="width: 100px;"><input type="checkbox" {_chk(d, "metode_manual")}> Manual</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "metode_prinnect")}> Prinnect Software</label>
            </td>
        </tr>
        <tr>
            <td class="label-col">Layout Cetak Mesin :</td>
            <td class="colon-col"></td>
            <td>
                <label class="checkbox-group" style="width: 45px;"><input type="checkbox" {_chk(d, "mesin_pm")}> PM</label>
                <label class="checkbox-group" style="width: 45px;"><input type="checkbox" {_chk(d, "mesin_sm")}> SM</label>
                <label class="checkbox-group" style="width: 45px;"><input type="checkbox" {_chk(d, "mesin_cd")}> CD</label>
                <label class="checkbox-group" style="width: 55px;"><input type="checkbox" {_chk(d, "mesin_gto")}> GTO</label>
                <label class="checkbox-group" style="width: 65px;"><input type="checkbox" {_chk(d, "mesin_sord")}> SOR-D</label>
                <label class="checkbox-group" style="width: 65px;"><input type="checkbox" {_chk(d, "mesin_sors")}> SOR-S</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "mesin_toko")}> TOKO</label>
            </td>
        </tr>
        <tr class="section-end">
            <td class="label-col">UNLEG Mesin</td>
            <td class="colon-col">:</td>
            <td>
                <input type="text" class="dotted-input" style="width: 110px; margin-right: 10px;" value="{_val(d, "unleg_mesin_text")}">
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "unleg_dari_kress")}> dari kress</label>
                <label class="checkbox-group"><input type="checkbox" {_chk(d, "unleg_dari_blok")}> dari blok</label>
            </td>
        </tr>

        <!-- SECTION 4: FINSHING -->
        <tr>
            <td rowspan="9" class="side-cat"><div class="vertical-text">FINSHING</div></td>
            <td rowspan="2" class="fin-left">SPOT UV</td>
            <td colspan="2">
                <span class="fin-sublabel">Ketebalan garis</span> : <input type="text" class="dotted-input" style="width: 140px;" value="{_val(d, "spotuv_ketebalan")}">
            </td>
        </tr>
        <tr>
            <td colspan="2">
                <span class="fin-sublabel">Jumlah UP / druk</span> : <input type="text" class="dotted-input" style="width: 140px;" value="{_val(d, "spotuv_jumlah_up")}">up
            </td>
        </tr>
        <tr>
            <td rowspan="3" class="fin-left">
                <div style="margin-bottom: 4px;">HOTPRINT</div>
                <div style="font-weight: normal;"><b>Kode :</b> <input type="text" class="dotted-input" style="width: 110px;" value="{_val(d, "hotprint_kode")}"></div>
            </td>
            <td colspan="2">
                <span class="fin-sublabel">Ukuran Klise</span> <input type="text" class="dotted-input" style="width: 150px;" value="{_val(d, "hotprint_ukuran_klise")}">
            </td>
        </tr>
        <tr>
            <td colspan="2">
                <span class="fin-sublabel">Ketebalan garis</span> : <input type="text" class="dotted-input" style="width: 140px;" value="{_val(d, "hotprint_ketebalan")}">
            </td>
        </tr>
        <tr>
            <td colspan="2">
                <span class="fin-sublabel">Jumlah klise/druk</span> : <input type="text" class="dotted-input" style="width: 140px;" value="{_val(d, "hotprint_jumlah_up")}">up
            </td>
        </tr>
        <tr>
            <td rowspan="3" class="fin-left">
                <div style="display: flex; align-items: center; justify-content: space-between; width: 115px; margin-bottom: 8px;">
                    <span>EMBOSS</span> <input type="checkbox" style="width: 13px; height: 13px; accent-color: #000; margin: 0;" {_chk(d, "emboss_check")}>
                </div>
                <div style="display: flex; align-items: center; justify-content: space-between; width: 115px;">
                    <span>DEBOSS</span> <input type="checkbox" style="width: 13px; height: 13px; accent-color: #000; margin: 0;" {_chk(d, "deboss_check")}>
                </div>
            </td>
            <td colspan="2">
                <span class="fin-sublabel">Ukuran Klise</span> <input type="text" class="dotted-input" style="width: 150px;" value="{_val(d, "emboss_ukuran_klise")}">
            </td>
        </tr>
        <tr>
            <td colspan="2">
                <span class="fin-sublabel">Ketebalan garis</span> : <input type="text" class="dotted-input" style="width: 140px;" value="{_val(d, "emboss_ketebalan")}">
            </td>
        </tr>
        <tr>
            <td colspan="2">
                <span class="fin-sublabel">Jumlah klise/druk</span> : <input type="text" class="dotted-input" style="width: 140px;" value="{_val(d, "emboss_jumlah_up")}">up
            </td>
        </tr>
        <tr class="section-end">
            <td class="fin-left">LAMINATING</td>
            <td colspan="2">
                <label class="checkbox-group" style="width: 75px;"><input type="checkbox" {_chk(d, "lam_doff")}> Doff</label>
                <label class="checkbox-group" style="width: 90px;"><input type="checkbox" {_chk(d, "lam_glossy")}> Kilap/glossy</label>
                <span class="inline-bold">Batas Laminating Glossy :</span>
                <input type="text" class="dotted-input" style="width: 45px;" value="{_val(d, "batas_lam_glossy_mm")}"> mm dari rit
            </td>
        </tr>
        """
        header_title = "CHECK LIST AKHIR<br>DIVISION : DESIGN"

    font_sz = "11px" if ftype == "AKHIR" else "12px"
    cont_w = "780px" if ftype == "AKHIR" else "750px"

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{FORM_META[ftype]["title"]} - { _val(d, "nama_file") }</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            font-size: {font_sz};
            margin: 15px;
            color: #000;
            background: #fff;
        }}
        .no-print-bar {{
            max-width: {cont_w};
            margin: 0 auto 14px;
            padding: 10px 14px;
            background: #192857;
            color: #fff;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-family: 'Plus Jakarta Sans', Arial, sans-serif;
        }}
        .no-print-bar button {{
            background: #1d6ff2;
            color: #fff;
            border: none;
            padding: 7px 14px;
            border-radius: 999px;
            font-weight: bold;
            cursor: pointer;
            margin-left: 8px;
        }}
        .container {{
            width: {cont_w};
            margin: 0 auto;
        }}
        .header {{
            text-align: center;
            font-weight: bold;
            font-size: 15px;
            margin-bottom: 12px;
            line-height: 1.3;
        }}
        .top-info {{
            width: 100%;
            margin-bottom: 8px;
            font-size: 12.5px;
        }}
        .top-info td {{
            padding: 2px 0;
        }}
        .dotted-input {{
            border: none;
            border-bottom: 1px dotted #000;
            outline: none;
            font-family: inherit;
            font-size: inherit;
            background: transparent;
        }}
        table.checklist-table {{
            width: 100%;
            border-collapse: collapse;
            border: 1.5px solid #000;
        }}
        table.checklist-table td {{
            border-bottom: 1px solid #000;
            padding: 3px 6px;
            vertical-align: middle;
        }}
        .side-cat {{
            width: 22px;
            background-color: #d9d9d9;
            border-right: 1.5px solid #000;
            border-bottom: 1.5px solid #000 !important;
            text-align: center;
            font-weight: bold;
            font-size: 11.5px;
            padding: 0 !important;
        }}
        .vertical-text {{
            writing-mode: vertical-rl;
            transform: rotate(180deg);
            white-space: nowrap;
            display: inline-block;
            letter-spacing: 0.5px;
        }}
        .section-end td {{
            border-bottom: 1.5px solid #000 !important;
        }}
        .label-col {{
            width: 155px;
            font-weight: bold;
            padding-right: 0 !important;
        }}
        .colon-col {{
            width: 8px;
            font-weight: bold;
            padding-left: 2px !important;
            padding-right: 2px !important;
        }}
        .checkbox-group {{
            display: inline-flex;
            align-items: center;
            margin-right: 12px;
            font-weight: normal;
        }}
        .checkbox-group input[type="checkbox"] {{
            width: 13px;
            height: 13px;
            margin: 0 4px 0 0;
            accent-color: #000;
        }}
        .checkbox-multiline {{
            display: inline-flex;
            align-items: center;
            margin-right: 18px;
            font-weight: normal;
        }}
        .checkbox-multiline input[type="checkbox"] {{
            width: 13px;
            height: 13px;
            margin: 0 5px 0 0;
            accent-color: #000;
        }}
        .inline-bold {{
            font-weight: bold;
        }}
        .fin-left {{
            width: 185px;
            border-right: 1px solid #000;
            font-weight: bold;
            padding-left: 12px !important;
        }}
        .fin-sublabel {{
            display: inline-block;
            width: 125px;
        }}
        .preview-box {{
            height: 360px;
            vertical-align: top !important;
            padding: 8px !important;
            position: relative;
        }}
        .catatan-section {{
            position: absolute;
            bottom: 10px;
            left: 8px;
            right: 8px;
        }}
        .textarea-notes {{
            width: 100%;
            border: none;
            resize: none;
            outline: none;
            font-family: inherit;
            font-size: 12px;
            background: transparent;
        }}
        .footer {{
            margin-top: 10px;
            font-weight: bold;
            font-size: 11.5px;
        }}
        .signature-container {{
            display: flex;
            margin-top: 28px;
            gap: 50px;
            padding-left: 10px;
        }}
        .signature-box {{
            text-align: center;
            font-size: 11px;
            min-width: 160px;
        }}
        .sig-line {{
            margin-bottom: 6px;
            font-weight: normal;
        }}
        @media print {{
            body {{ margin: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
            .container {{ width: 100%; }}
            .no-print-bar {{ display: none !important; }}
        }}
    </style>
</head>
<body>
<div class="no-print-bar">
    <div><strong>{FORM_META[ftype]["title"]}</strong> — {_val(d, "customer")} ({_val(d, "nama_file")})</div>
    <div>
        <button onclick="window.print()">🖨️ Cetak / Simpan PDF</button>
        <button style="background:#334155;" onclick="window.close()">✕ Tutup</button>
    </div>
</div>
<div class="container">
    <div class="header">
        {header_title}
    </div>

    <table class="top-info">
        <tr>
            <td style="width: 85px; padding-left: 25px;">Customer</td>
            <td style="width: 15px;">:</td>
            <td><input type="text" class="dotted-input" style="width: 95%;" value="{_val(d, "customer")}"></td>
        </tr>
        <tr>
            <td style="padding-left: 25px;">Nama File</td>
            <td>:</td>
            <td><input type="text" class="dotted-input" style="width: 95%;" value="{_val(d, "nama_file")}"></td>
        </tr>
    </table>

    <table class="checklist-table">
        {body_rows}
    </table>

    <div class="footer">
        <div>Telah diperiksa oleh:</div>
        <div class="signature-container">
            <div class="signature-box">
                <div class="sig-line">{desainer_line}</div>
                <div>DESAINER</div>
                {desainer_sub}
            </div>
            <div class="signature-box">
                <div class="sig-line">{reviewer_line}</div>
                <div>REVIEWER</div>
                {reviewer_sub}
            </div>
        </div>
    </div>
</div>
</body>
</html>"""

