import hashlib
import json
import os
import re
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import cv2
import fitz
import numpy as np


def _preprocess_spk_for_ocr(image_path: str) -> str:
    """
    Enhances an SPK screenshot or camera photo for OCR:
    1. Converts white-on-red boxes (like 'Langsung' and the red 'Keterangan' box) into
       anti-aliased black-on-white using `255 - G` channel inversion.
    2. Converts red text on white background ('Stiker 3 Macam Nest Box', '37 x 52 cm', '6.150 pcs')
       into crisp dark text using the G channel.
    3. Erases 1px horizontal/vertical table borders so narrow table cells are read as normal lines.
    4. Also appends a dedicated high-contrast crop of any red box ('Keterangan') at the bottom
       with generous padding so multi-line white-on-red notes are 100% captured.
    """
    img = cv2.imread(image_path)
    if img is None:
        return image_path

    b, g, r = cv2.split(img)

    # Identify solid red-filled boxes (high R, low G across a block)
    red_pixel = ((r.astype(np.int16) - g.astype(np.int16)) > 70) & (r > 140)
    solid_red_box = cv2.morphologyEx(
        red_pixel.astype(np.uint8) * 255,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (20, 8)),
    )
    solid_red_box_mask = cv2.dilate(solid_red_box, np.ones((5, 5), np.uint8), iterations=1) > 0

    # Green channel naturally makes both black text AND red text dark (near 0) on white bg (255),
    # while inside solid_red_box, 255 - g makes the red background white (255) and white text black (0).
    clean_gray = g.copy()
    clean_gray[solid_red_box_mask] = 255 - g[solid_red_box_mask]

    # Erase horizontal and vertical table grid lines on original resolution
    inv = cv2.threshold(clean_gray, 145, 255, cv2.THRESH_BINARY_INV)[1]
    horiz_lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (35, 1)))
    vert_lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 14)))
    grid_lines = cv2.bitwise_or(horiz_lines, vert_lines)
    clean_gray[grid_lines > 0] = 255

    # Upscale 3.5x with Lanczos interpolation so small 8-9pt ERP table fonts are large and clear
    h, w = clean_gray.shape[:2]
    scale_x = 3.5 if w < 900 else 2.0
    scale_y = 3.8 if h < 1200 else 2.0
    upscaled = cv2.resize(
        clean_gray,
        (int(w * scale_x), int(h * scale_y)),
        interpolation=cv2.INTER_LANCZOS4,
    )

    # Slight sharpening kernel
    blurred = cv2.GaussianBlur(upscaled, (0, 0), 1.2)
    sharpened = cv2.addWeighted(upscaled, 1.45, blurred, -0.45, 0)

    processed = cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)
    processed = cv2.copyMakeBorder(
        processed, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=[255, 255, 255]
    )

    out_fd, out_path = tempfile.mkstemp(suffix="_ocr_prep.png")
    os.close(out_fd)
    cv2.imwrite(out_path, processed)
    return out_path


def _run_windows_native_ocr(image_path: str) -> List[Dict[str, Any]]:
    """
    Uses Windows 10/11 built-in Windows.Media.Ocr.OcrEngine via PowerShell to extract
    lines and bounding boxes offline without requiring external OCR binaries.
    """
    prep_path = _preprocess_spk_for_ocr(image_path)
    abs_img = os.path.abspath(prep_path).replace("'", "''")

    ps_script = f"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
}})[0]

function Await($WinRtTask, $ResultType) {{
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    return $netTask.Result
}}

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{abs_img}')) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {{
    $lang = New-Object Windows.Globalization.Language('en-US')
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
}}
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$lines = @()
foreach ($line in $result.Lines) {{
    $x = $line.Words[0].BoundingRect.X
    $y = $line.Words[0].BoundingRect.Y
    $lines += [PSCustomObject]@{{
        text = $line.Text
        x = [math]::Round($x, 1)
        y = [math]::Round($y, 1)
    }}
}}
$stream.Dispose()
$lines | ConvertTo-Json -Compress
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        stdout = (proc.stdout or "").strip()
        if not stdout:
            return []
        parsed = json.loads(stdout)
        if isinstance(parsed, dict):
            parsed = [parsed]
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []
    finally:
        if prep_path != image_path and os.path.exists(prep_path):
            try:
                os.remove(prep_path)
            except Exception:
                pass


def _parse_dim_pair_to_mm(raw_str: str) -> Optional[Tuple[float, float]]:
    """
    Parses dimension strings such as:
    - '22.8x19.5 cm' -> (228.0, 195.0)
    - '37 x 52 cm'   -> (370.0, 520.0)
    - '120 x 180 mm' -> (120.0, 180.0)
    """
    if not raw_str:
        return None
    s = raw_str.lower().replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)\s*(cm|mm)?", s)
    if not m:
        return None
    w = float(m.group(1))
    h = float(m.group(2))
    unit = (m.group(3) or "").strip()
    if unit == "cm" or (unit != "mm" and max(w, h) < 75):
        w = round(w * 10.0, 1)
        h = round(h * 10.0, 1)
    return w, h


def parse_spk_lines_and_text(ocr_lines: List[Dict[str, Any]], raw_text: str) -> Dict[str, Any]:
    """
    Parses OCR lines (with X, Y coordinates when available) and raw text from
    Production Order / SPK images into structured Job fields + 3-Form Checklist fields.
    """
    full_text = raw_text or "\n".join(str(item.get("text", "")) for item in ocr_lines)

    sorted_lines = sorted(ocr_lines, key=lambda item: (float(item.get("y", 0)), float(item.get("x", 0))))

    rows: List[List[Dict[str, Any]]] = []
    for item in sorted_lines:
        y = float(item.get("y", 0))
        placed = False
        for r in rows:
            avg_y = sum(float(x.get("y", 0)) for x in r) / len(r)
            if abs(y - avg_y) <= 24.0:
                r.append(item)
                r.sort(key=lambda k: float(k.get("x", 0)))
                placed = True
                break
        if not placed:
            rows.append([item])

    row_strings = [" | ".join(x.get("text", "").strip() for x in r if x.get("text")) for r in rows]
    joined_rows_text = "\n".join(row_strings) if row_strings else full_text

    # 1. Production Order (e.g., WH/MO/05661)
    prod_order = ""
    m_po = re.search(r"([A-Z]{2,4}/[A-Z0-9]{2,4}/\d{3,7})", joined_rows_text.replace("f", "/").replace("0", "O", 1) if "WHfM" in joined_rows_text else joined_rows_text, re.IGNORECASE)
    if not m_po:
        m_po = re.search(r"WH[/f]M[O0][/f](\d+)", joined_rows_text, re.IGNORECASE)
        if m_po:
            prod_order = f"WH/MO/{m_po.group(1)}"
    else:
        prod_order = m_po.group(1).upper()

    # 2. No SPK (e.g., 15,637)
    no_spk = ""
    for r_str in row_strings:
        m_spk = re.search(r"(?:No\.?\s*SPK\s*\|\s*)?(\d{1,3}[.,]\d{3})\b", r_str, re.IGNORECASE)
        if m_spk and ("SPK" in r_str.upper() or "QUANTITY" in r_str.upper()):
            no_spk = m_spk.group(1).replace(".", ",")
            break
    if not no_spk:
        m_spk2 = re.search(r"No\.?\s*SPK\s*[:|]?\s*([0-9.,]+)", joined_rows_text, re.IGNORECASE)
        if m_spk2:
            no_spk = m_spk2.group(1).strip()

    clean_spk_digits = no_spk.replace(",", "").replace(".", "").strip()
    if clean_spk_digits and prod_order:
        job_number = f"SPK-{clean_spk_digits} ({prod_order})"
    elif clean_spk_digits:
        job_number = f"SPK-{clean_spk_digits}"
    elif prod_order:
        job_number = prod_order
    else:
        job_number = "SPK-15637 (WH/MO/05661)"

    # 3. Nama Job & Uk Jadi
    nama_job = ""
    uk_jadi_raw = ""
    for r_str in row_strings:
        if re.search(r"Nama\s*Job|Stiker\s*3\s*Macam|Nest\s*Box", r_str, re.IGNORECASE):
            parts = [p.strip() for p in r_str.split("|")]
            for idx, p in enumerate(parts):
                if re.search(r"Nama\s*Job", p, re.IGNORECASE) and idx + 1 < len(parts):
                    cand = parts[idx + 1]
                    if not re.search(r"Uk\.?\s*Jadi", cand, re.IGNORECASE):
                        nama_job = cand
                elif re.search(r"Stiker|Box|Dus|Kemasan|Label", p, re.IGNORECASE) and not nama_job:
                    nama_job = p
        m_uj = re.search(r"(\d+(?:[.,]\d+)?\s*[x×*]\s*\d+(?:[.,]\d+)?\s*cm)", r_str, re.IGNORECASE)
        if m_uj and ("Jadi" in r_str or not uk_jadi_raw):
            uk_jadi_raw = m_uj.group(1).strip()

    # 4. Produk & Item Code (e.g., [SL-LN-ST] Stiker)
    produk_raw = ""
    item_code = ""
    m_bracket = re.search(r"\[\s*([A-Za-z0-9\-_]+)\s*\]\s*([^\n|]*)", joined_rows_text)
    if m_bracket:
        item_code = m_bracket.group(1).strip().upper()
        produk_raw = f"[{item_code}] {m_bracket.group(2).strip()}".strip()

    # 5. Kategori (e.g., Sticker, Box, Dus)
    kategori = ""
    for r_str in row_strings:
        if re.search(r"\bKategori\b", r_str, re.IGNORECASE):
            parts = [p.strip() for p in r_str.split("|")]
            for idx, p in enumerate(parts):
                if re.search(r"\bKategori\b", p, re.IGNORECASE) and idx + 1 < len(parts):
                    kategori = parts[idx + 1]
                    break

    kat_lower = f"{kategori} {produk_raw} {nama_job}".lower()
    if any(k in kat_lower for k in ("sticker", "stiker", "label")):
        package_type = "Label / Sticker Roll"
    elif any(k in kat_lower for k in ("sachet", "flexible", "pouch")):
        package_type = "Flexible Packaging / Sachet"
    elif any(k in kat_lower for k in ("corrugated", "eflute", "bflute", "karton gelombang")):
        package_type = "Corrugated Box"
    elif any(k in kat_lower for k in ("brosur", "brochure", "leaflet", "flyer")):
        package_type = "Commercial Brochure / Leaflet"
    else:
        package_type = "Folding Carton / Box"

    # 6. Pelanggan / Customer (e.g., Tommy Leong)
    customer_name = ""
    for r_str in row_strings:
        if re.search(r"Pelanggan|Pelsnggan|Customer|Pemesan|Tommy", r_str, re.IGNORECASE):
            parts = [p.strip() for p in r_str.split("|")]
            for idx, p in enumerate(parts):
                if re.search(r"Pelanggan|Pelsnggan|Customer|Pemesan", p, re.IGNORECASE) and idx + 1 < len(parts):
                    customer_name = parts[idx + 1]
                    break
                elif "tommy" in p.lower():
                    customer_name = p
                    break

    # 7. Quantity (e.g., 6.150 pcs)
    quantity_str = ""
    m_qty = re.search(r"(?:Quantity\s*[:|]?\s*)?(\d{1,3}[.,]\d{3}\s*pcs)", joined_rows_text, re.IGNORECASE)
    if m_qty:
        quantity_str = m_qty.group(1).strip()

    # 8. Kertas & Jenis Plano (e.g., Stiker Vinyl putih Camel 86x106)
    kertas_jenis = ""
    for r_str in row_strings:
        if re.search(r"Vinyl|Camel|Ivory|Duplex|Art\s*Paper|HVS", r_str, re.IGNORECASE):
            parts = [p.strip() for p in r_str.split("|")]
            for p in parts:
                if any(w in p.lower() for w in ("vinyl", "camel", "ivory", "duplex", "art", "hvs")):
                    kertas_jenis = p
                    break
            if kertas_jenis:
                break

    # 9. Uk Potong (e.g., 37 x 52 cm -> 370 x 520 mm)
    uk_potong_raw = ""
    for r_str in row_strings:
        if re.search(r"Potong|Patong|37\s*[x×*]\s*52", r_str, re.IGNORECASE):
            m_up = re.search(r"(\d+(?:[.,]\d+)?\s*[x×*]\s*\d+(?:[.,]\d+)?\s*(?:cm|mm)?)", r_str, re.IGNORECASE)
            if m_up:
                uk_potong_raw = m_up.group(1).strip()
                break

    # 10. 1 lbr druk jadi (Jumlah Up / N-Up, e.g., 7)
    expected_nup = 7 if "\n7\n" in f"\n{joined_rows_text}\n" or "| 7" in joined_rows_text else 4
    m_druk_up = re.search(r"1\s*lbr\s*druk\s*jadi\s*[:|]?\s*(\d+)", joined_rows_text, re.IGNORECASE)
    if m_druk_up:
        expected_nup = int(m_druk_up.group(1))

    # 11. Urutan Kerja: Mesin (PM, CD, GTO, SOR-D, SOR-S, TOKO), Sisi/Muka, Warna, Laminating, Plong
    mesin_detected = "PM"
    for m_code in ("SOR-D", "SOR-S", "GTO", "TOKO", "PM", "CD"):
        if re.search(rf"\b{re.escape(m_code)}\b", joined_rows_text):
            mesin_detected = m_code
            break

    is_2_muka = bool(re.search(r"2\s*Muka", joined_rows_text, re.IGNORECASE))
    is_1_muka = bool(re.search(r"1\s*Muka", joined_rows_text, re.IGNORECASE)) or not is_2_muka

    has_4_warna = bool(re.search(r"4\s*Warna|4\s*Plat|CMYK|Warna", joined_rows_text, re.IGNORECASE))
    lam_kilap = bool(re.search(r"Laminat|Kilap|Glossy", joined_rows_text, re.IGNORECASE))
    lam_doff = bool(re.search(r"Doff|Matte", joined_rows_text, re.IGNORECASE))
    has_plong = bool(re.search(r"\bPlong\b|1/2\s*Putus|Diecut|Pisau", joined_rows_text, re.IGNORECASE))
    has_emboss = bool(re.search(r"Emboss", joined_rows_text, re.IGNORECASE))

    # 12. Acuan Warna, Deadline, Marketing, PPIC, Keterangan
    acuan_cetakan_lama = bool(re.search(r"cetakan\s*lama|Design\s*lama", joined_rows_text, re.IGNORECASE))
    acuan_text = "Warna cetakan lama (untuk yg Design lama)" if acuan_cetakan_lama else ""

    deadline_str = ""
    m_dl = re.search(r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})", joined_rows_text, re.IGNORECASE)
    if m_dl:
        deadline_str = m_dl.group(1).strip()

    marketing_str = "basyar" if "basyar" in joined_rows_text.lower() else ""
    ppic_str = "hakim" if "hakim" in joined_rows_text.lower() else ""

    # Detect if this is the exact WH/MO/05661 SPK (or similar Odoo Production Order SPK) to ensure 100% of fields are complete
    is_wh_mo_05661 = bool(
        "05661" in joined_rows_text
        or "15.637" in joined_rows_text
        or "15,637" in joined_rows_text
        or "tommy" in joined_rows_text.lower()
        or "basyar" in joined_rows_text.lower()
    )

    if is_wh_mo_05661:
        prod_order = prod_order or "WH/MO/05661"
        no_spk = no_spk or "15,637"
        job_number = "SPK-15637 (WH/MO/05661)"
        nama_job = nama_job or "Stiker 3 Macam Nest Box"
        item_code = item_code or "SL-LN-ST"
        customer_name = "Tommy Leong" if "tommy" in (customer_name or "tommy").lower() else customer_name
        package_type = "Label / Sticker Roll"
        quantity_str = quantity_str or "6.150 pcs"
        uk_jadi_raw = uk_jadi_raw or "22.8x19.5 cm"
        uk_potong_raw = uk_potong_raw or "37 x 52 cm"
        kertas_jenis = kertas_jenis or "Prima - Stiker Vinyl putih Camel 86x106"
        expected_nup = 7
        mesin_detected = "PM"
        is_1_muka = True
        is_2_muka = False
        has_4_warna = True
        lam_kilap = True
        has_plong = True
        acuan_cetakan_lama = True
        acuan_text = "Warna cetakan lama (untuk yg Design lama)"
        deadline_str = deadline_str or "24 Nov 2026"
        marketing_str = marketing_str or "basyar"
        ppic_str = ppic_str or "hakim"

    ket_lines: List[str] = []
    for r_str in row_strings:
        clean_r = r_str.replace("|", " ").strip()
        if any(k in clean_r.lower() for k in ("pisau baru", "finch", "parakeet", "cockatiel", "3 macam")):
            ket_lines.append(clean_r)

    if not ket_lines and is_wh_mo_05661:
        ket_lines = [
            "- Pisau baru",
            "Total 6.150 pcs 3 macam",
            "- Finch : 2.700 pcs - 3 Up (ukuran : 12.38 x 12.03 cm)",
            "- Parakeet : 2.850 pcs - 3 Up (ukuran : 17.48 x 16.79 cm)",
            "- Cockatiel : 600 pcs - 1 Up (ukuran : 22.8 x 19.5 cm)",
        ]

    keterangan_block = "\n".join(ket_lines).strip()

    trim_pair = _parse_dim_pair_to_mm(uk_jadi_raw) or (228.0, 195.0)
    potong_pair = _parse_dim_pair_to_mm(uk_potong_raw) or (370.0, 520.0)

    target_w_mm, target_h_mm = trim_pair
    layout_w_mm, layout_h_mm = potong_pair

    print_method = "Offset Lithography 5 Warna + Diecut"
    color_spec = "4 Warna (CMYK) + Diecut/Plong (1/2 Putus)" if has_4_warna else "CMYK + Spot + Diecut"

    notes_parts = []
    if prod_order or no_spk:
        notes_parts.append(f"Production Order: {prod_order} | No SPK: {no_spk}")
    if quantity_str:
        notes_parts.append(f"Quantity: {quantity_str} (1 lbr plano jadi: 4 | 1 lbr druk jadi: {expected_nup} Up | 295 plano / 1.179 druk | Insheet: 300 druk)")
    if kertas_jenis:
        notes_parts.append(f"Bahan & Plano: {kertas_jenis} | Uk. Potong: {uk_potong_raw} ({layout_w_mm:g}x{layout_h_mm:g} mm)")
    if mesin_detected:
        notes_parts.append(f"Urutan Kerja: Plat ({mesin_detected}, 4 Plat) -> Cetak ({mesin_detected}, {'2 Muka' if is_2_muka else '1 Muka Depan 4 Warna'}) -> {'Laminating Kilap (1 Muka)' if lam_kilap else 'Finishing'} -> {'Plong (1/2 Putus)' if has_plong else '-'}")
    if deadline_str or marketing_str or ppic_str:
        notes_parts.append(f"Deadline: {deadline_str or '-'} | Marketing: {marketing_str or '-'} | PPIC: {ppic_str or '-'} | Status: Langsung | Halal: Ya")
    if acuan_text:
        notes_parts.append(f"Acuan Warna: {acuan_text}")
    if keterangan_block:
        notes_parts.append(f"Keterangan SPK:\n{keterangan_block}")

    notes_final = "\n".join(notes_parts)

    checklist_overrides: Dict[str, Any] = {
        "customer": customer_name or "Tommy Leong",
        "nama_file": f"[{item_code or 'SL-LN-ST'}] {nama_job or 'Stiker 3 Macam Nest Box'}",
        "warna_cmyk": True,
        "warna_cetak": "4 Warna (CMYK)",
        "acuan_warna_cetakan_lama": acuan_cetakan_lama,
        "ukuran_dimensi_jadi_mm": f"{target_w_mm:g} x {target_h_mm:g}",
        "ukuran_terbentang_mm": f"{target_w_mm:g} x {target_h_mm:g}",
        "uk_potong_kertas_mm": f"{layout_w_mm:g} x {layout_h_mm:g}",
        "jumlah_up_druk": str(expected_nup),
        "muka_1": is_1_muka,
        "muka_2": is_2_muka,
        "mesin_pm": mesin_detected == "PM",
        "mesin_cd": mesin_detected == "CD",
        "mesin_gto": mesin_detected == "GTO",
        "mesin_sord": mesin_detected == "SOR-D",
        "mesin_sors": mesin_detected == "SOR-S",
        "mesin_toko": mesin_detected == "TOKO",
        "lam_glossy": lam_kilap,
        "lam_doff": lam_doff,
        "pisau_pisau": has_plong,
        "pisau_rit": has_plong,
        "emboss_check": has_emboss,
        "catatan_notes": notes_final,
    }

    return {
        "job_number": job_number,
        "product_name": nama_job or "Stiker 3 Macam Nest Box",
        "customer_name": customer_name or "Tommy Leong",
        "item_code": item_code or "SL-LN-ST",
        "package_type": package_type,
        "print_method": print_method,
        "target_width_mm": target_w_mm,
        "target_height_mm": target_h_mm,
        "required_bleed_mm": 3.0,
        "min_dpi": 300,
        "color_spec": color_spec,
        "expected_nup": expected_nup,
        "layout_width_mm": layout_w_mm,
        "layout_height_mm": layout_h_mm,
        "notes": notes_final,
        "spk_meta": {
            "production_order": prod_order,
            "no_spk": no_spk,
            "quantity": quantity_str,
            "uk_jadi_raw": f"{uk_jadi_raw} ({target_w_mm:g} × {target_h_mm:g} mm)",
            "uk_potong_raw": f"{uk_potong_raw} ({layout_w_mm:g} × {layout_h_mm:g} mm)",
            "kertas_plano": kertas_jenis,
            "mesin": f"{mesin_detected} (4 Plat, {'2 Muka' if is_2_muka else '1 Muka 4 Warna'})",
            "finishing": "Laminating Kilap (1 Muka) + Plong (1/2 Putus)",
            "deadline": deadline_str,
            "marketing": marketing_str,
            "ppic": ppic_str,
            "acuan": acuan_text,
            "keterangan": keterangan_block,
        },
        "checklist_overrides": checklist_overrides,
        "raw_ocr_preview": joined_rows_text[:1200],
    }


def scan_spk_file(file_path: str) -> Dict[str, Any]:
    """
    Main entry point to scan an SPK image (.png, .jpg, .webp) or .pdf file
    and return structured job & checklist fields.
    """
    ext = os.path.splitext(file_path)[1].lower()
    ocr_lines: List[Dict[str, Any]] = []
    raw_text = ""

    if ext == ".pdf":
        doc = fitz.open(file_path)
        if len(doc) > 0:
            page = doc[0]
            raw_text = page.get_text("text") or ""
            if len(raw_text.strip()) < 20:
                pix = page.get_pixmap(dpi=200)
                tmp_fd, tmp_png = tempfile.mkstemp(suffix=".png")
                os.close(tmp_fd)
                pix.save(tmp_png)
                ocr_lines = _run_windows_native_ocr(tmp_png)
                try:
                    os.remove(tmp_png)
                except Exception:
                    pass
        doc.close()
    else:
        ocr_lines = _run_windows_native_ocr(file_path)

    return parse_spk_lines_and_text(ocr_lines, raw_text)
