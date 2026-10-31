import io
import os
from typing import Dict, Tuple

import pymupdf as fitz
from PIL import Image, ImageDraw

MM_TO_PT = 72.0 / 25.4


def mm_to_pt(mm_val: float) -> float:
    return mm_val * MM_TO_PT


def _k(rgb):
    """Convert an RGB tuple (0-1) to a CMYK tuple so demo vector art is print-safe (no DeviceRGB)."""
    r, g, b = rgb
    k = 1 - max(r, g, b)
    if k >= 1:
        return (0, 0, 0, 1)
    return ((1 - r - k) / (1 - k), (1 - g - k) / (1 - k), (1 - b - k) / (1 - k), k)


def _create_sample_product_bitmap(dpi: int = 300, mode: str = "CMYK", variant: str = "clean") -> bytes:
    """Create a realistic product illustration bitmap at specified DPI and Color Mode."""
    # Physical target: 60mm x 45mm
    w_px = max(60, int(round((60.0 / 25.4) * dpi)))
    h_px = max(45, int(round((45.0 / 25.4) * dpi)))

    img = Image.new("RGB", (w_px, h_px), (25, 118, 210))
    draw = ImageDraw.Draw(img)

    # Decorative packaging graphic waves/circles
    draw.rectangle([0, 0, w_px, int(h_px * 0.35)], fill=(13, 71, 161))
    draw.ellipse(
        [int(w_px * 0.15), int(h_px * 0.15), int(w_px * 0.85), int(h_px * 0.85)],
        fill=(255, 193, 7),
        outline=(255, 255, 255),
        width=max(2, w_px // 60),
    )
    draw.rectangle(
        [int(w_px * 0.3), int(h_px * 0.38), int(w_px * 0.7), int(h_px * 0.65)],
        fill=(211, 47, 47) if variant == "clean" else (56, 142, 60),
    )

    buf = io.BytesIO()
    if mode == "CMYK":
        cmyk_img = img.convert("CMYK")
        cmyk_img.save(buf, format="JPEG", quality=92)
    else:
        img.save(buf, format="PNG")
    return buf.getvalue()


def _draw_artwork_card(
    page: fitz.Page,
    x0_pt: float,
    y0_pt: float,
    trim_w_mm: float,
    trim_h_mm: float,
    bleed_mm: float = 0.0,
    stage: str = "ORIGINAL",
    has_defect: bool = False,
    img_bytes: bytes = b"",
) -> None:
    """Draws a realistic packaging artwork on a PDF page at (x0_pt, y0_pt)."""
    tw = mm_to_pt(trim_w_mm)
    th = mm_to_pt(trim_h_mm)
    bl = mm_to_pt(bleed_mm)

    # Outer bleed background (extended by bleed_mm around trim box)
    bg_rect = fitz.Rect(x0_pt - bl, y0_pt - bl, x0_pt + tw + bl, y0_pt + th + bl)
    page.draw_rect(bg_rect, color=None, fill=_k((0.96, 0.94, 0.89)))

    # Top header band (extends into bleed)
    header_rect = fitz.Rect(x0_pt - bl, y0_pt - bl, x0_pt + tw + bl, y0_pt + mm_to_pt(38))
    page.draw_rect(header_rect, color=None, fill=_k((0.78, 0.12, 0.18)))

    # Gold accent stripe
    stripe_rect = fitz.Rect(x0_pt - bl, y0_pt + mm_to_pt(38), x0_pt + tw + bl, y0_pt + mm_to_pt(43))
    page.draw_rect(stripe_rect, color=None, fill=_k((0.92, 0.72, 0.15)))

    # Brand Title & Subtitle
    page.insert_text(
        fitz.Point(x0_pt + mm_to_pt(10), y0_pt + mm_to_pt(18)),
        "NUSANTARA ROAST",
        fontsize=17,
        fontname="helv",
        color=_k((1, 1, 1)),
    )
    page.insert_text(
        fitz.Point(x0_pt + mm_to_pt(10), y0_pt + mm_to_pt(29)),
        "PREMIUM ARABICA COFFEE DRIP - SINGLE ORIGIN GAYO",
        fontsize=7.5,
        fontname="helv",
        color=_k((1, 0.92, 0.75)),
    )

    # Defect 1 in AW if has_defect=True: Halal / Organic Badge shifted or altered text
    badge_x = x0_pt + tw - mm_to_pt(26) + (mm_to_pt(4.5) if (has_defect and stage == "AW") else 0)
    badge_y = y0_pt + mm_to_pt(20)
    page.draw_circle(
        fitz.Point(badge_x, badge_y),
        mm_to_pt(10),
        color=_k((1, 1, 1)),
        fill=_k((0.12, 0.55, 0.28)) if not (has_defect and stage == "AW") else _k((0.85, 0.35, 0.05)),
        width=1.5,
    )
    page.insert_text(
        fitz.Point(badge_x - mm_to_pt(7), badge_y + mm_to_pt(1.5)),
        "100% ASLI" if not (has_defect and stage == "AW") else "99% ASLI",
        fontsize=6.5,
        fontname="helv",
        color=_k((1, 1, 1)),
    )

    # Insert center product bitmap (60mm x 45mm)
    if img_bytes:
        img_rect = fitz.Rect(
            x0_pt + mm_to_pt(30),
            y0_pt + mm_to_pt(52),
            x0_pt + mm_to_pt(90),
            y0_pt + mm_to_pt(97),
        )
        page.insert_image(img_rect, stream=img_bytes)

    # Nutrition / Product Specs Panel
    panel_rect = fitz.Rect(
        x0_pt + mm_to_pt(10),
        y0_pt + mm_to_pt(105),
        x0_pt + tw - mm_to_pt(10),
        y0_pt + mm_to_pt(142),
    )
    page.draw_rect(panel_rect, color=_k((0.25, 0.22, 0.20)), fill=_k((1, 1, 1)), width=0.8)
    page.insert_text(
        fitz.Point(x0_pt + mm_to_pt(14), y0_pt + mm_to_pt(113)),
        "KOMPOSISI: 100% Kopi Arabika Gayo Pilihan (Roast Level: Medium-Dark)",
        fontsize=6.8,
        fontname="helv",
        color=_k((0.1, 0.1, 0.1)),
    )
    page.insert_text(
        fitz.Point(x0_pt + mm_to_pt(14), y0_pt + mm_to_pt(121)),
        "BPOM RI MD 868728001299  |  Diproduksi Oleh: PT Nusantara Aroma Cemerlang",
        fontsize=6.5,
        fontname="helv",
        color=_k((0.15, 0.15, 0.15)),
    )

    # Defect 2 in AW if has_defect=True: Net weight typo (Netto 200g instead of 250g)
    netto_txt = "BERAT BERSIH / NETTO: 200g (10 Sachet @ 20g)" if (has_defect and stage == "AW") else "BERAT BERSIH / NETTO: 250g (10 Sachet @ 25g)"
    page.insert_text(
        fitz.Point(x0_pt + mm_to_pt(14), y0_pt + mm_to_pt(131)),
        netto_txt,
        fontsize=8.5,
        fontname="helv",
        color=_k((0.78, 0.12, 0.18)) if not (has_defect and stage == "AW") else _k((0.05, 0.25, 0.80)),
    )

    # Barcode Box (Bottom Right)
    bc_rect = fitz.Rect(
        x0_pt + tw - mm_to_pt(48),
        y0_pt + mm_to_pt(148),
        x0_pt + tw - mm_to_pt(10),
        y0_pt + mm_to_pt(170),
    )
    page.draw_rect(bc_rect, color=_k((0, 0, 0)), fill=_k((1, 1, 1)), width=0.6)
    # Barcode bars
    bar_x = bc_rect.x0 + mm_to_pt(4)
    for i in range(24):
        bw = 1.4 if i % 3 == 0 else (0.7 if i % 2 == 0 else 2.0)
        page.draw_rect(
            fitz.Rect(bar_x, bc_rect.y0 + mm_to_pt(2.5), bar_x + bw, bc_rect.y1 - mm_to_pt(5)),
            color=None,
            fill=_k((0, 0, 0)),
        )
        bar_x += bw + 1.8
    page.insert_text(
        fitz.Point(bc_rect.x0 + mm_to_pt(4), bc_rect.y1 - mm_to_pt(1.2)),
        "8 997012 345678",
        fontsize=6.5,
        fontname="helv",
        color=_k((0, 0, 0)),
    )

    # Item Code & Storage Icon (Bottom Left)
    page.insert_text(
        fitz.Point(x0_pt + mm_to_pt(10), y0_pt + mm_to_pt(156)),
        "KODE ITEM: PKG-NR-250G-R02",
        fontsize=7.0,
        fontname="helv",
        color=_k((0.15, 0.15, 0.15)),
    )
    page.insert_text(
        fitz.Point(x0_pt + mm_to_pt(10), y0_pt + mm_to_pt(165)),
        "Simpan di tempat sejuk & kering.",
        fontsize=6.5,
        fontname="helv",
        color=_k((0.3, 0.3, 0.3)),
    )

    # Draw Die-line (Cut & Crease) on ONEUP and LAYOUT
    if stage in ("ONEUP", "LAYOUT"):
        trim_box_draw = fitz.Rect(x0_pt, y0_pt, x0_pt + tw, y0_pt + th)
        # Magenta/Cyan Dieline representation
        page.draw_rect(trim_box_draw, color=_k((0.95, 0.0, 0.65)), width=1.1)
        # Crease lines
        page.draw_line(
            fitz.Point(x0_pt, y0_pt + mm_to_pt(12)),
            fitz.Point(x0_pt + tw, y0_pt + mm_to_pt(12)),
            color=_k((0.0, 0.65, 0.90)),
            width=0.7,
            dashes="[3 3] 0",
        )


def generate_stage_pdfs_for_job(
    output_dir: str,
    job_prefix: str,
    trim_w_mm: float = 120.0,
    trim_h_mm: float = 180.0,
    bleed_mm: float = 3.0,
    has_aw_defect: bool = False,
) -> Dict[str, str]:
    """
    Generates 4 realistic PDF files representing the 4 Prepress stages:
    1. ORIGINAL: Customer design file (Trim size 120x180mm)
    2. AW: Prepress Artwork file (120x180mm, either clean or with deliberate prepress issues for QC demo)
    3. ONEUP: 1-Up file with 3mm Bleed (126x186mm), explicit TrimBox/BleedBox, Dieline & Overprint
    4. LAYOUT: 4-Up Step-and-Repeat Imposition Sheet (290x410mm) with Crop Marks, Register Marks & Color Bar
    """
    os.makedirs(output_dir, exist_ok=True)
    paths: Dict[str, str] = {}

    clean_cmyk_img = _create_sample_product_bitmap(dpi=300, mode="CMYK", variant="clean")
    aw_img = (
        _create_sample_product_bitmap(dpi=180, mode="RGB", variant="altered")
        if has_aw_defect
        else clean_cmyk_img
    )

    # 1. ORIGINAL PDF
    orig_path = os.path.join(output_dir, f"{job_prefix}_01_ORIGINAL.pdf")
    doc_orig = fitz.open()
    p_orig = doc_orig.new_page(width=mm_to_pt(trim_w_mm), height=mm_to_pt(trim_h_mm))
    _draw_artwork_card(
        p_orig, 0, 0, trim_w_mm, trim_h_mm, bleed_mm=0.0, stage="ORIGINAL", has_defect=False, img_bytes=clean_cmyk_img
    )
    doc_orig.set_metadata(
        {
            "title": f"{job_prefix} Original Customer File",
            "keywords": "COLOR:CMYK; RGB_WARN:FALSE; SPOT:PANTONE 185 C; OVERPRINT:TRUE",
        }
    )
    doc_orig.save(orig_path)
    doc_orig.close()
    paths["ORIGINAL"] = orig_path

    # 2. AW (Artwork) PDF
    aw_path = os.path.join(output_dir, f"{job_prefix}_02_AW.pdf")
    doc_aw = fitz.open()
    p_aw = doc_aw.new_page(width=mm_to_pt(trim_w_mm), height=mm_to_pt(trim_h_mm))
    _draw_artwork_card(
        p_aw,
        0,
        0,
        trim_w_mm,
        trim_h_mm,
        bleed_mm=0.0,
        stage="AW",
        has_defect=has_aw_defect,
        img_bytes=aw_img,
    )
    if has_aw_defect:
        doc_aw.set_metadata(
            {
                "title": f"{job_prefix} Prepress AW (Revision Check)",
                "keywords": "COLOR:RGB; RGB_WARN:TRUE; SPOT:PANTONE 185 C",
            }
        )
    else:
        doc_aw.set_metadata(
            {
                "title": f"{job_prefix} Prepress AW (Approved Master)",
                "keywords": "COLOR:CMYK; RGB_WARN:FALSE; SPOT:PANTONE 185 C, PANTONE 871 C; OVERPRINT:TRUE",
            }
        )
    doc_aw.save(aw_path)
    doc_aw.close()
    paths["AW"] = aw_path

    # 3. ONEUP PDF (Includes 3mm Bleed around TrimBox + Dieline Spot + Overprint)
    oneup_path = os.path.join(output_dir, f"{job_prefix}_03_ONEUP.pdf")
    doc_oneup = fitz.open()
    full_w_pt = mm_to_pt(trim_w_mm + 2 * bleed_mm)
    full_h_pt = mm_to_pt(trim_h_mm + 2 * bleed_mm)
    bl_pt = mm_to_pt(bleed_mm)

    p_oneup = doc_oneup.new_page(width=full_w_pt, height=full_h_pt)
    mb = p_oneup.mediabox
    p_oneup.set_trimbox(fitz.Rect(bl_pt, bl_pt, mb.width - bl_pt, mb.height - bl_pt))
    p_oneup.set_bleedbox(mb)

    _draw_artwork_card(
        p_oneup,
        bl_pt,
        bl_pt,
        trim_w_mm,
        trim_h_mm,
        bleed_mm=bleed_mm,
        stage="ONEUP",
        has_defect=False,
        img_bytes=clean_cmyk_img,
    )
    doc_oneup.set_metadata(
        {
            "title": f"{job_prefix} 1-Up Prepress with Bleed & Dieline",
            "keywords": "COLOR:CMYK; RGB_WARN:FALSE; SPOT:PANTONE 185 C, DIELINE_CUT, CREASE_LINE; OVERPRINT:TRUE",
        }
    )
    doc_oneup.save(oneup_path)
    doc_oneup.close()
    paths["ONEUP"] = oneup_path

    # 4. LAYOUT PDF (4-Up Imposition Sheet: 290 x 410 mm with Color Bar, Register Marks, Gripper Margin)
    layout_path = os.path.join(output_dir, f"{job_prefix}_04_LAYOUT.pdf")
    layout_w_mm = 290.0
    layout_h_mm = 410.0
    doc_layout = fitz.open()
    p_layout = doc_layout.new_page(width=mm_to_pt(layout_w_mm), height=mm_to_pt(layout_h_mm))

    # White sheet background
    p_layout.draw_rect(
        fitz.Rect(0, 0, mm_to_pt(layout_w_mm), mm_to_pt(layout_h_mm)),
        color=None,
        fill=_k((1, 1, 1)),
    )

    # Top Color Calibration Bar (CMYK + Spot patches)
    cb_y = mm_to_pt(6)
    cb_h = mm_to_pt(5)
    patch_w = mm_to_pt(12)
    colors = [
        (0, 0.7, 0.9, 0),
        (0.85, 0, 0.5, 0),
        (0.95, 0.9, 0, 0),
        (0.1, 0.1, 0.1, 0),
        (0.78, 0.12, 0.18, 0),
        (0.12, 0.55, 0.28, 0),
    ]
    for i in range(18):
        c = colors[i % len(colors)][:3]
        px = mm_to_pt(22) + i * patch_w
        p_layout.draw_rect(fitz.Rect(px, cb_y, px + patch_w - 1, cb_y + cb_h), color=_k((0, 0, 0)), fill=_k(c), width=0.3)

    # Place 4-Up grid (2 columns x 2 rows) of the 1-Up card
    positions_mm = [
        (18.0, 18.0),
        (152.0, 18.0),
        (18.0, 206.0),
        (152.0, 206.0),
    ]
    for up_idx, (pos_x_mm, pos_y_mm) in enumerate(positions_mm):
        _draw_artwork_card(
            p_layout,
            mm_to_pt(pos_x_mm),
            mm_to_pt(pos_y_mm),
            trim_w_mm,
            trim_h_mm,
            bleed_mm=bleed_mm,
            stage="LAYOUT",
            has_defect=False,
            img_bytes=clean_cmyk_img,
        )

    # Registration marks & Gripper Legend at bottom of Layout sheet
    for rx_mm, ry_mm in [(10.0, 200.0), (280.0, 200.0), (145.0, 12.0), (145.0, 396.0)]:
        rc = fitz.Point(mm_to_pt(rx_mm), mm_to_pt(ry_mm))
        r_rad = mm_to_pt(3.0)
        p_layout.draw_circle(rc, r_rad, color=_k((0, 0, 0)), width=0.6)
        p_layout.draw_line(fitz.Point(rc.x - r_rad * 1.4, rc.y), fitz.Point(rc.x + r_rad * 1.4, rc.y), color=_k((0, 0, 0)), width=0.6)
        p_layout.draw_line(fitz.Point(rc.x, rc.y - r_rad * 1.4), fitz.Point(rc.x, rc.y + r_rad * 1.4), color=_k((0, 0, 0)), width=0.6)

    p_layout.insert_text(
        fitz.Point(mm_to_pt(18), mm_to_pt(403)),
        f"PLATE LAYOUT: {job_prefix} | SHEET: 290x410mm | 4-UP STEP & REPEAT | GRIPPER: 12mm | GRAIN: LONG GRAIN",
        fontsize=7.5,
        fontname="helv",
        color=_k((0, 0, 0)),
    )

    doc_layout.set_metadata(
        {
            "title": f"{job_prefix} 4-Up Imposition Layout",
            "keywords": "COLOR:CMYK; RGB_WARN:FALSE; SPOT:PANTONE 185 C, DIELINE_CUT, CREASE_LINE; OVERPRINT:TRUE",
        }
    )
    doc_layout.save(layout_path)
    doc_layout.close()
    paths["LAYOUT"] = layout_path

    return paths
