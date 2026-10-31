import json
import os
from typing import Any, Dict, List

import pymupdf as fitz


def generate_qc_report_pdf(
    job: Dict[str, Any],
    files: List[Dict[str, Any]],
    checklist: List[Dict[str, Any]],
    annotations: List[Dict[str, Any]],
    comparisons: List[Dict[str, Any]],
    approvals: List[Dict[str, Any]],
    output_pdf_path: str,
) -> str:
    """Generates a formal A4 Prepress Quality Control & Approval Report PDF."""
    os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)
    doc = fitz.open()

    # A4 portrait in points (595 x 842)
    W, H = 595.0, 842.0
    page = doc.new_page(width=W, height=H)
    y = 32.0

    def new_page_if_needed(required_h: float) -> None:
        nonlocal page, y
        if y + required_h > H - 40:
            page = doc.new_page(width=W, height=H)
            y = 36.0

    # Header Banner
    page.draw_rect(fitz.Rect(28, y, W - 28, y + 54), color=None, fill=(0.08, 0.14, 0.24))
    page.insert_text(
        fitz.Point(40, y + 22),
        "LAPORAN PEMERIKSAAN KUALITAS PREPRESS (QC PREPRESS CERTIFICATE)",
        fontsize=11.5,
        fontname="helv",
        color=(1, 1, 1),
    )
    page.insert_text(
        fitz.Point(40, y + 39),
        f"No. Job: {job['job_number']}   |   Produk: {job['product_name']}   |   Revisi: R0{job['revision_no']}",
        fontsize=8.5,
        fontname="helv",
        color=(0.75, 0.85, 1.0),
    )

    status_label = job.get("status", "IN_PROGRESS")
    badge_color = (
        (0.10, 0.62, 0.32)
        if status_label == "APPROVED"
        else ((0.82, 0.18, 0.18) if status_label == "REJECTED" else (0.85, 0.55, 0.08))
    )
    page.draw_rect(fitz.Rect(W - 145, y + 14, W - 38, y + 40), color=None, fill=badge_color)
    page.insert_text(
        fitz.Point(W - 135, y + 30),
        status_label,
        fontsize=9,
        fontname="helv",
        color=(1, 1, 1),
    )
    y += 66

    # Section 1: Job Specifications
    page.draw_rect(fitz.Rect(28, y, W - 28, y + 68), color=(0.82, 0.85, 0.90), fill=(0.97, 0.98, 1.0), width=0.7)
    specs_left = [
        f"Customer: {job['customer_name']}",
        f"Kode Item: {job['item_code']}",
        f"Tipe Kemasan: {job['package_type']} ({job['print_method']})",
        f"Dibuat Oleh: {job['created_by']} pada {job['created_at']}",
    ]
    specs_right = [
        f"Target Ukuran Trim: {job['target_width_mm']} x {job['target_height_mm']} mm",
        f"Standar Bleed Min: {job['required_bleed_mm']} mm  |  Resolusi Min: {job['min_dpi']} DPI",
        f"Spesifikasi Warna: {job['color_spec']}",
        f"Target Layout Imposisi: {job['expected_nup']}-Up ({job['layout_width_mm']} x {job['layout_height_mm']} mm)",
    ]
    for idx, txt in enumerate(specs_left):
        page.insert_text(fitz.Point(38, y + 15 + idx * 13), txt, fontsize=7.8, fontname="helv", color=(0.15, 0.18, 0.22))
    for idx, txt in enumerate(specs_right):
        page.insert_text(fitz.Point(295, y + 15 + idx * 13), txt, fontsize=7.8, fontname="helv", color=(0.15, 0.18, 0.22))
    y += 80

    # Section 2: Uploaded Stage Files & Automated Preflight Summary
    page.insert_text(fitz.Point(28, y), "1. RINGKASAN PARAMETER OTOMATIS FILE PER TAHAP (ORIGINAL / AW / ONEUP / LAYOUT)", fontsize=9, fontname="helv", color=(0.08, 0.14, 0.24))
    y += 8
    page.draw_rect(fitz.Rect(28, y, W - 28, y + 18), color=None, fill=(0.18, 0.26, 0.38))
    headers_f = [("Tahap", 34), ("Nama File", 85), ("Trim / Media (mm)", 215), ("Bleed", 315), ("Warna & Spot", 360), ("DPI & Font", 480)]
    for h_txt, hx in headers_f:
        page.insert_text(fitz.Point(hx, y + 12), h_txt, fontsize=7.5, fontname="helv", color=(1, 1, 1))
    y += 18

    for f in files:
        new_page_if_needed(22)
        insp = json.loads(f["inspection_json"]) if isinstance(f.get("inspection_json"), str) else f.get("inspection", {})
        tw = insp.get("trim_box_mm", {}).get("width", "-")
        th = insp.get("trim_box_mm", {}).get("height", "-")
        mw = insp.get("media_box_mm", {}).get("width", "-")
        mh = insp.get("media_box_mm", {}).get("height", "-")
        bl = insp.get("detected_bleed_mm", 0)
        cs = ", ".join(insp.get("color_spaces", []))
        spots = len(insp.get("spot_colors", []))
        min_dpi = insp.get("min_dpi") or "-"
        f_safe = "Aman" if insp.get("all_fonts_safe") else "UNEMBED!"

        page.draw_rect(fitz.Rect(28, y, W - 28, y + 18), color=(0.86, 0.88, 0.92), fill=(1, 1, 1), width=0.5)
        page.insert_text(fitz.Point(34, y + 12), str(f["stage"]), fontsize=7.2, fontname="helv", color=(0.1, 0.1, 0.1))
        page.insert_text(fitz.Point(85, y + 12), str(f["filename"])[:24], fontsize=7.2, fontname="helv", color=(0.1, 0.1, 0.1))
        page.insert_text(fitz.Point(215, y + 12), f"{tw}x{th} ({mw}x{mh})", fontsize=7.0, fontname="helv", color=(0.1, 0.1, 0.1))
        page.insert_text(fitz.Point(315, y + 12), f"{bl} mm", fontsize=7.2, fontname="helv", color=(0.1, 0.1, 0.1))
        page.insert_text(fitz.Point(360, y + 12), f"{cs[:16]} ({spots} Spot)", fontsize=7.0, fontname="helv", color=(0.1, 0.1, 0.1))
        page.insert_text(fitz.Point(480, y + 12), f"{min_dpi} DPI | Font:{f_safe}", fontsize=6.8, fontname="helv", color=(0.1, 0.1, 0.1))
        y += 18

    y += 14

    # Section 3: Checklist Items
    new_page_if_needed(50)
    page.insert_text(fitz.Point(28, y), "2. HASIL PEMERIKSAAN CHECKLIST PREPRESS (OTOMATIS & VERIFIKASI OPERATOR)", fontsize=9, fontname="helv", color=(0.08, 0.14, 0.24))
    y += 8
    page.draw_rect(fitz.Rect(28, y, W - 28, y + 18), color=None, fill=(0.18, 0.26, 0.38))
    headers_c = [("Kode", 34), ("Tahap", 72), ("Parameter Pemeriksaan", 112), ("Hasil Pengukuran / Catatan", 315), ("Status", 475), ("Pemeriksa", 518)]
    for h_txt, hx in headers_c:
        page.insert_text(fitz.Point(hx, y + 12), h_txt, fontsize=7.3, fontname="helv", color=(1, 1, 1))
    y += 18

    for item in checklist:
        new_page_if_needed(20)
        st = item.get("manual_status") or item.get("auto_status") or "PENDING"
        st_color = (0.08, 0.55, 0.26) if st == "PASS" else ((0.80, 0.15, 0.15) if st == "FAIL" else (0.75, 0.48, 0.05))
        row_bg = (0.99, 0.99, 1.0) if st != "FAIL" else (1.0, 0.95, 0.95)
        page.draw_rect(fitz.Rect(28, y, W - 28, y + 17), color=(0.88, 0.90, 0.93), fill=row_bg, width=0.4)

        val_note = item.get("operator_note") or item.get("auto_value") or item.get("auto_detail") or "-"
        checker = item.get("checked_by") or ("Auto-Preflight" if item.get("is_auto") else "-")

        page.insert_text(fitz.Point(34, y + 11.5), str(item["code"]), fontsize=6.8, fontname="helv", color=(0.1, 0.1, 0.1))
        page.insert_text(fitz.Point(72, y + 11.5), str(item["stage_target"]), fontsize=6.8, fontname="helv", color=(0.2, 0.2, 0.2))
        page.insert_text(fitz.Point(112, y + 11.5), str(item["title"])[:42], fontsize=6.8, fontname="helv", color=(0.1, 0.1, 0.1))
        page.insert_text(fitz.Point(315, y + 11.5), str(val_note)[:34], fontsize=6.6, fontname="helv", color=(0.25, 0.25, 0.25))
        page.insert_text(fitz.Point(475, y + 11.5), str(st), fontsize=7.0, fontname="helv", color=st_color)
        page.insert_text(fitz.Point(518, y + 11.5), str(checker)[:12], fontsize=6.5, fontname="helv", color=(0.2, 0.2, 0.2))
        y += 17

    y += 14

    # Section 4: Visual Annotations / Defect Pins
    new_page_if_needed(60)
    page.insert_text(fitz.Point(28, y), f"3. DAFTAR TEMUAN / PIN MASALAH PADA ARTWORK ({len(annotations)} Temuan)", fontsize=9, fontname="helv", color=(0.08, 0.14, 0.24))
    y += 8
    if not annotations:
        page.insert_text(fitz.Point(34, y + 12), "Tidak ada catatan temuan/masalah visual pada file ini.", fontsize=7.5, fontname="helv", color=(0.3, 0.3, 0.3))
        y += 22
    else:
        for ann in annotations:
            new_page_if_needed(26)
            page.draw_rect(fitz.Rect(28, y, W - 28, y + 22), color=(0.86, 0.88, 0.92), fill=(1, 0.99, 0.97), width=0.5)
            pin_line = (
                f"Pin #{ann['pin_number']} [{ann['stage']} @ X:{ann['x_pct']:.1f}%, Y:{ann['y_pct']:.1f}%] "
                f"[{ann['severity']} - {ann['category']}] {ann['title']}: {ann['comment']} "
                f"(Oleh: {ann['created_by']} | Status: {ann['status']})"
            )
            page.insert_text(fitz.Point(34, y + 14), pin_line[:108], fontsize=6.8, fontname="helv", color=(0.15, 0.15, 0.15))
            y += 22

    y += 16

    # Section 5: Multi-Role Approval Sign-off Matrix
    new_page_if_needed(95)
    page.insert_text(fitz.Point(28, y), "4. OTORISASI & JEJAK APPROVAL DIGITAL (MULTI-LEVEL SIGN-OFF)", fontsize=9, fontname="helv", color=(0.08, 0.14, 0.24))
    y += 10

    roles_order = [
        ("OPERATOR", "1. Operator Prepress"),
        ("QC", "2. QC Prepress Inspector"),
        ("SUPERVISOR", "3. Kepala / Supervisor Prepress"),
    ]
    approval_map = {a["role_stage"]: a for a in approvals}
    box_w = (W - 56 - 20) / 3.0

    for idx, (r_key, r_title) in enumerate(roles_order):
        bx = 28 + idx * (box_w + 10)
        page.draw_rect(fitz.Rect(bx, y, bx + box_w, y + 74), color=(0.75, 0.80, 0.88), fill=(0.98, 0.99, 1.0), width=0.8)
        page.draw_rect(fitz.Rect(bx, y, bx + box_w, y + 16), color=None, fill=(0.18, 0.26, 0.38))
        page.insert_text(fitz.Point(bx + 8, y + 11.5), r_title, fontsize=7.3, fontname="helv", color=(1, 1, 1))

        ap = approval_map.get(r_key)
        if ap:
            dec_color = (0.08, 0.55, 0.26) if "APPROV" in ap["decision"] else (0.80, 0.15, 0.15)
            page.insert_text(fitz.Point(bx + 8, y + 29), f"Status: {ap['decision']}", fontsize=7.5, fontname="helv", color=dec_color)
            page.insert_text(fitz.Point(bx + 8, y + 41), f"Nama: {ap['approver_name']} ({ap['approver_nik']})", fontsize=7.0, fontname="helv", color=(0.1, 0.1, 0.1))
            page.insert_text(fitz.Point(bx + 8, y + 52), f"Waktu: {ap['signed_at']}", fontsize=6.5, fontname="helv", color=(0.3, 0.3, 0.3))
            page.insert_text(fitz.Point(bx + 8, y + 63), f"Sign Hash: {ap['signature_hash'][:22]}", fontsize=6.2, fontname="helv", color=(0.4, 0.4, 0.4))
        else:
            page.insert_text(fitz.Point(bx + 8, y + 42), "MENUNGGU OTORISASI", fontsize=7.5, fontname="helv", color=(0.6, 0.6, 0.6))

    doc.save(output_pdf_path)
    doc.close()
    return output_pdf_path
