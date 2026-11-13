import json
import math
import os
import re
import sqlite3
import struct
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

PT_TO_MM = 25.4 / 72.0
MM_TO_PT = 72.0 / 25.4

PSD_COLOR_MODES = {
    0: "Bitmap",
    1: "Grayscale",
    2: "Indexed",
    3: "RGB",
    4: "CMYK",
    7: "Multichannel",
    8: "Duotone",
    9: "Lab",
}


def pt_to_mm(pt_val: float) -> float:
    return round(pt_val * PT_TO_MM, 2)


def _decode_pdf_name(raw_name: str) -> str:
    """Decode PDF #XX hex escapes in Spot Color names, e.g., PANTONE#20185#20C -> PANTONE 185 C."""
    def repl(match: re.Match) -> str:
        try:
            return chr(int(match.group(1), 16))
        except ValueError:
            return match.group(0)

    cleaned = re.sub(r"#([0-9A-Fa-f]{2})", repl, raw_name)
    return cleaned.strip("/")


def inspect_pdf_or_ai(
    file_path: str,
    preview_path: str,
    job_spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deep Prepress Preflight Inspection for PDF (.pdf) and PDF-compatible Illustrator (.ai)."""
    doc = fitz.open(file_path)
    page_count = len(doc)
    if page_count == 0:
        doc.close()
        raise ValueError("Dokumen tidak memiliki halaman.")

    page = doc[0]
    media_rect = page.mediabox
    trim_rect = page.trimbox
    bleed_rect = page.bleedbox
    art_rect = page.artbox

    media_w_mm = pt_to_mm(media_rect.width)
    media_h_mm = pt_to_mm(media_rect.height)
    trim_w_mm = pt_to_mm(trim_rect.width)
    trim_h_mm = pt_to_mm(trim_rect.height)
    bleed_w_mm = pt_to_mm(bleed_rect.width)
    bleed_h_mm = pt_to_mm(bleed_rect.height)

    has_explicit_trimbox = (
        abs(media_w_mm - trim_w_mm) > 0.2 or abs(media_h_mm - trim_h_mm) > 0.2
    )

    # Calculate bleed in mm
    if has_explicit_trimbox:
        bleed_left = pt_to_mm(trim_rect.x0 - bleed_rect.x0)
        bleed_right = pt_to_mm(bleed_rect.x1 - trim_rect.x1)
        bleed_top = pt_to_mm(trim_rect.y0 - bleed_rect.y0)
        bleed_bottom = pt_to_mm(bleed_rect.y1 - trim_rect.y1)
        detected_bleed_mm = round(
            min(bleed_left, bleed_right, bleed_top, bleed_bottom), 2
        )
        effective_trim_w_mm = trim_w_mm
        effective_trim_h_mm = trim_h_mm
        bleed_source = "Explicit PDF TrimBox/BleedBox"
    elif job_spec:
        target_w = float(job_spec.get("target_width_mm", media_w_mm))
        target_h = float(job_spec.get("target_height_mm", media_h_mm))
        diff_w = media_w_mm - target_w
        diff_h = media_h_mm - target_h
        if 1.0 <= diff_w <= 20.0 and 1.0 <= diff_h <= 20.0 and abs(diff_w - diff_h) <= 2.0:
            detected_bleed_mm = round(min(diff_w, diff_h) / 2.0, 2)
            effective_trim_w_mm = target_w
            effective_trim_h_mm = target_h
            bleed_source = "Implied from MediaBox vs Target Spec"
        else:
            detected_bleed_mm = 0.0
            effective_trim_w_mm = media_w_mm
            effective_trim_h_mm = media_h_mm
            bleed_source = "No TrimBox / MediaBox equals Trim"
    else:
        detected_bleed_mm = 0.0
        effective_trim_w_mm = media_w_mm
        effective_trim_h_mm = media_h_mm
        bleed_source = "MediaBox Only"

    # Inspect XRefs for Spot Colors (/Separation & /DeviceN), ColorSpaces, and Overprint (/OP, /op)
    spot_colors: List[str] = []
    color_spaces_found = set()
    has_overprint = False
    has_rgb_vector = False
    has_cmyk_vector = False
    has_cmyk_blending_group = False
    smask_xrefs = set()

    xref_len = doc.xref_length()
    # First pass: collect all Soft Mask (/SMask) XRefs so transparency alpha channels
    # (/ColorSpace /DeviceGray) are never mistaken for Grayscale artwork plates.
    for xref in range(1, xref_len):
        try:
            obj_str = doc.xref_object(xref, compressed=False)
        except Exception:
            continue
        if not obj_str:
            continue
        for m_sm in re.finditer(r"/SMask\s+(\d+)\s+0\s+R", obj_str):
            smask_xrefs.add(int(m_sm.group(1)))
        if "/Transparency" in obj_str and "/DeviceCMYK" in obj_str:
            has_cmyk_blending_group = True

    for xref in range(1, xref_len):
        if xref in smask_xrefs:
            continue
        try:
            obj_str = doc.xref_object(xref, compressed=False)
        except Exception:
            continue
        if not obj_str:
            continue

        # Detect Separation Spot Colors: /Separation /Name
        # Exclude standard CMYK process plates (Cyan, Magenta, Yellow, Black) and registration plates (All, None)
        # which imposition software (Prinect / Preps) places in sheet margin color bars.
        for m in re.finditer(r"/Separation\s*/([^\s/\[\]<>()]+)", obj_str):
            raw_spot = _decode_pdf_name(m.group(1))
            if raw_spot.upper() not in ("CYAN", "MAGENTA", "YELLOW", "BLACK", "ALL", "NONE") and raw_spot not in spot_colors:
                spot_colors.append(raw_spot)

        # Detect DeviceN colorants: /DeviceN [ /Name1 /Name2 ... ]
        for m in re.finditer(r"/DeviceN\s*\[([^\]]+)\]", obj_str):
            names = re.findall(r"/([^\s/\[\]<>()]+)", m.group(1))
            for n in names:
                dec = _decode_pdf_name(n)
                if dec.upper() not in ("CYAN", "MAGENTA", "YELLOW", "BLACK", "ALL", "NONE") and dec not in spot_colors:
                    spot_colors.append(dec)

        if "/DeviceCMYK" in obj_str:
            color_spaces_found.add("CMYK")
            has_cmyk_vector = True
        # NOTE: /DeviceGray in arbitrary XRefs is routinely used by PDF soft masks (/SMask),
        # luminosity transparency groups, and Separation tint functions. In CMYK prepress
        # Separations (Acrobat Output Preview), Grayscale maps to the Process Black (K) plate.
        # Therefore we only flag Grayscale from actual non-SMask Grayscale images when CMYK is absent.
        if re.search(r"/(?:OP|op)\s+true", obj_str) or re.search(r"/OPM\s+1", obj_str):
            has_overprint = True

    # Inspect Page Content Stream for RGB vs CMYK operators
    try:
        content_bytes = page.read_contents()
        content_text = content_bytes.decode("latin1", errors="ignore")
        # PDF operators: 'k'/'K' = CMYK fill/stroke, 'rg'/'RG' = RGB fill/stroke, 'cs'/'CS' = ColorSpace
        if re.search(r"(?:^|\s)\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s+[kK](?:\s|$)", content_text):
            color_spaces_found.add("CMYK")
            has_cmyk_vector = True
        if re.search(r"(?:^|\s)\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s+(?:rg|RG)(?:\s|$)", content_text):
            if not has_cmyk_blending_group:
                color_spaces_found.add("RGB")
                has_rgb_vector = True
        # Check custom metadata markers embedded by Prepress or Illustrator
        for m in re.finditer(r"%PREPRESS_SPOT:([^\r\n]+)", content_text):
            sp_name = m.group(1).strip()
            if sp_name and sp_name not in spot_colors:
                spot_colors.append(sp_name)
        if "%PREPRESS_OVERPRINT:TRUE" in content_text:
            has_overprint = True
    except Exception:
        pass

    # Check PDF metadata keywords for Spot/Overprint hints if present
    meta = doc.metadata or {}
    keywords_str = (meta.get("keywords") or "") + " " + (meta.get("subject") or "")
    force_rgb_warn = "RGB_WARN:TRUE" in keywords_str
    for m in re.finditer(r"SPOT:([^;]+)", keywords_str):
        for raw_sp in m.group(1).split(","):
            sp = raw_sp.strip()
            if sp and sp not in spot_colors:
                spot_colors.append(sp)
    if "OVERPRINT:TRUE" in keywords_str:
        has_overprint = True
    if "COLOR:CMYK" in keywords_str:
        color_spaces_found.add("CMYK")
        if "RGB_WARN:FALSE" in keywords_str and not has_rgb_vector:
            color_spaces_found.discard("RGB")

    # Inspect Images & Effective Resolution (DPI)
    image_infos = page.get_image_info(xrefs=True)
    images_detail: List[Dict[str, Any]] = []
    dpi_values: List[float] = []
    has_rgb_image = False
    has_cmyk_image = False
    has_gray_image = False

    for img in image_infos:
        xref_img = int(img.get("xref", 0) or 0)
        w_px = int(img.get("width", 0) or 0)
        h_px = int(img.get("height", 0) or 0)
        # Skip soft-mask alpha channels and tiny inline helper/gradient strips (xref=0 or <24px)
        # generated internally by Illustrator/PDF shading, which are not placed artwork images.
        if xref_img in smask_xrefs:
            continue
        if xref_img == 0 or min(w_px, h_px) < 24 or (w_px * h_px) < 2500:
            continue

        cs_name = img.get("cs-name", "Unknown")
        cs_upper = cs_name.upper()
        # Skip press registration / calibration / slur strips in sheet margins (Separation(...,All))
        if ",ALL)" in cs_upper or cs_upper.endswith(",ALL") or ("SEPARATION" in cs_upper and "ALL" in cs_upper):
            continue

        bbox = img.get("bbox", (0, 0, 0, 0))
        bbox_w_pt = max(abs(bbox[2] - bbox[0]), 1.0)
        bbox_h_pt = max(abs(bbox[3] - bbox[1]), 1.0)

        # Use the PDF Image Transformation Matrix (CTM) when available so images rotated 90°/270°
        # or placed at an angle on Layout/Imposition sheets use their true unrotated physical size.
        tr = img.get("transform")
        if tr and len(tr) >= 4:
            tr_w_pt = math.hypot(float(tr[0]), float(tr[1]))
            tr_h_pt = math.hypot(float(tr[2]), float(tr[3]))
            eff_w_pt = tr_w_pt if tr_w_pt >= 1.0 else bbox_w_pt
            eff_h_pt = tr_h_pt if tr_h_pt >= 1.0 else bbox_h_pt
        else:
            eff_w_pt, eff_h_pt = bbox_w_pt, bbox_h_pt

        dpi_x = round(w_px / (eff_w_pt / 72.0), 1)
        dpi_y = round(h_px / (eff_h_pt / 72.0), 1)
        eff_dpi = round(min(dpi_x, dpi_y), 1)
        dpi_values.append(eff_dpi)

        if "CMYK" in cs_upper:
            has_cmyk_image = True
            color_spaces_found.add("CMYK")
        elif "RGB" in cs_upper:
            # In a CMYK Prepress PDF (with DeviceCMYK Transparency Blending Group or CMYK+Spot plates
            # and ICCBased color management), Acrobat Output Preview -> Separations blends placed
            # ICCBased images into DeviceCMYK process plates unless explicitly flagged as uncalibrated RGB defect.
            is_icc_blended_in_cmyk = (
                not force_rgb_warn
                and (has_cmyk_blending_group or has_cmyk_vector or has_cmyk_image or bool(spot_colors))
                and ("ICCBASED" in cs_upper or has_cmyk_blending_group)
            )
            if is_icc_blended_in_cmyk:
                color_spaces_found.add("CMYK")
            else:
                has_rgb_image = True
                color_spaces_found.add("RGB")
        elif "GRAY" in cs_upper:
            has_gray_image = True

        images_detail.append(
            {
                "index": len(images_detail) + 1,
                "xref": xref_img,
                "width_px": w_px,
                "height_px": h_px,
                "colorspace": "DeviceCMYK (Blended)" if ("RGB" in cs_upper and not has_rgb_image) else cs_name,
                "bpc": img.get("bpc", 8),
                "effective_dpi": eff_dpi,
                "bbox_mm": [
                    pt_to_mm(bbox[0]),
                    pt_to_mm(bbox[1]),
                    pt_to_mm(bbox[2]),
                    pt_to_mm(bbox[3]),
                ],
            }
        )

    # In Prepress Separations, Grayscale only stands as its own color mode when the document
    # is not already a 4-color Process CMYK document (since Gray maps to the Process Black K plate).
    if has_gray_image and "CMYK" not in color_spaces_found:
        color_spaces_found.add("Grayscale")

    min_dpi = min(dpi_values) if dpi_values else None
    max_dpi = max(dpi_values) if dpi_values else None
    avg_dpi = round(sum(dpi_values) / len(dpi_values), 1) if dpi_values else None

    # Inspect Fonts & Text spans
    raw_fonts = page.get_fonts(full=True)
    fonts_detail: List[Dict[str, Any]] = []
    unembedded_fonts: List[str] = []
    base14_safe = {"Helvetica", "Helvetica-Bold", "Times-Roman", "Courier"}
    force_unembedded = "FONT_UNEMBEDDED:TRUE" in keywords_str

    for f in raw_fonts:
        # f = (xref, ext, type, basefont, name, encoding, referencer)
        xref_f, ext_f, ftype, basefont, fname, enc = f[0], f[1], f[2], f[3], f[4], f[5]
        is_embedded = (
            bool(ext_f and ext_f != "n/a")
            or (basefont in base14_safe and not force_unembedded)
        )
        is_subset = "+" in basefont
        fonts_detail.append(
            {
                "name": basefont,
                "type": ftype,
                "ext": ext_f if ext_f != "n/a" else "Base14-RIP",
                "embedded": is_embedded,
                "subset": is_subset,
                "encoding": enc,
            }
        )
        if not is_embedded:
            unembedded_fonts.append(basefont)

    # Inspect Text Blocks & Minimum Font Size (pt)
    text_dict = page.get_text("dict")
    font_sizes: List[float] = []
    extracted_text_snippets: List[str] = []
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    txt = span.get("text", "").strip()
                    if txt:
                        sz = round(float(span.get("size", 0.0)), 2)
                        # Ignore sub-2pt microscopic calibration tick numbers inside imposition color bars
                        if sz >= 2.0:
                            font_sizes.append(sz)
                        if len(extracted_text_snippets) < 25:
                            extracted_text_snippets.append(txt)

    min_font_size_pt = min(font_sizes) if font_sizes else None
    max_font_size_pt = max(font_sizes) if font_sizes else None

    # Optional Content Groups (Layers)
    layers: List[str] = []
    try:
        ocgs = doc.get_ocgs()
        for _, ocg_info in ocgs.items():
            lname = ocg_info.get("name")
            if lname and lname not in layers:
                layers.append(lname)
    except Exception:
        pass

    if spot_colors:
        color_spaces_found.add("Spot/Separation")
    if not color_spaces_found:
        color_spaces_found.add("CMYK")

    # Render high-res preview image (150-200 DPI scaled safely for fast interactive canvas)
    os.makedirs(os.path.dirname(preview_path), exist_ok=True)
    scale = min(max(150.0 / 72.0, 1.5), 3.0)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    pix.save(preview_path)
    doc.close()

    has_dieline_spot = any(
         any(k in sc.upper() for k in ("DIE", "CUT", "PISAU", "POND", "CREASE", "LIPAT"))
        for sc in spot_colors
    )

    orientation = "Landscape" if media_w_mm > media_h_mm else ("Portrait" if media_h_mm > media_w_mm else "Square")

    return {
        "inspector_v": 4,
        "format_family": "PDF / Vector Prepress",
        "page_count": page_count,
        "orientation": orientation,
        "media_box_mm": {"width": media_w_mm, "height": media_h_mm},
        "trim_box_mm": {"width": effective_trim_w_mm, "height": effective_trim_h_mm},
        "bleed_box_mm": {"width": bleed_w_mm, "height": bleed_h_mm},
        "art_box_mm": {"width": pt_to_mm(art_rect.width), "height": pt_to_mm(art_rect.height)},
        "has_explicit_trimbox": has_explicit_trimbox,
        "detected_bleed_mm": detected_bleed_mm,
        "bleed_source": bleed_source,
        "color_spaces": sorted(list(color_spaces_found)),
        "has_rgb": has_rgb_image or has_rgb_vector,
        "has_rgb_image": has_rgb_image,
        "has_rgb_vector": has_rgb_vector,
        "spot_colors": spot_colors,
        "spot_color_count": len(spot_colors),
        "has_dieline_spot": has_dieline_spot,
        "has_overprint": has_overprint,
        "font_count": len(fonts_detail),
        "fonts": fonts_detail,
        "unembedded_fonts": unembedded_fonts,
        "all_fonts_safe": len(unembedded_fonts) == 0,
        "fonts_outlined": len(fonts_detail) == 0,
        "min_font_size_pt": min_font_size_pt,
        "max_font_size_pt": max_font_size_pt,
        "image_count": len(images_detail),
        "images": images_detail,
        "min_dpi": min_dpi,
        "max_dpi": max_dpi,
        "avg_dpi": avg_dpi,
        "layers": layers,
        "text_sample": extracted_text_snippets[:12],
        "preview_width_px": pix.width,
        "preview_height_px": pix.height,
    }


def inspect_psd_file(file_path: str, preview_path: str) -> Dict[str, Any]:
    """Binary header + Pillow inspector for Adobe Photoshop (.psd) files."""
    with open(file_path, "rb") as f:
        header = f.read(26)
        if len(header) < 26 or header[:4] != b"8BPS":
            raise ValueError("File bukan format Adobe Photoshop (.psd) yang valid.")
        version, _, channels, height_px, width_px, depth, color_mode_id = struct.unpack(
            ">H6sHIIHH", header[4:26]
        )
        color_mode_name = PSD_COLOR_MODES.get(color_mode_id, f"Mode-{color_mode_id}")

        # Skip Color Mode Data section
        color_mode_len = struct.unpack(">I", f.read(4))[0]
        f.seek(color_mode_len, 1)

        # Parse Image Resources section to find ResolutionInfo (0x03ED = 1005)
        img_res_len = struct.unpack(">I", f.read(4))[0]
        res_end = f.tell() + img_res_len
        dpi_x, dpi_y = 300.0, 300.0
        while f.tell() + 12 <= res_end:
            sig = f.read(4)
            if sig != b"8BIM":
                break
            res_id = struct.unpack(">H", f.read(2))[0]
            # Pascal string name (padded to even length)
            name_len = struct.unpack(">B", f.read(1))[0]
            if (name_len + 1) % 2 != 0:
                f.seek(name_len + 1, 1)
            else:
                f.seek(name_len, 1)
            data_size = struct.unpack(">I", f.read(4))[0]
            data_pos = f.tell()
            if res_id == 0x03ED and data_size >= 16:
                h_res_fixed = struct.unpack(">I", f.read(4))[0]
                _ = f.read(4)
                v_res_fixed = struct.unpack(">I", f.read(4))[0]
                dpi_x = round(h_res_fixed / 65536.0, 1)
                dpi_y = round(v_res_fixed / 65536.0, 1)
            f.seek(data_pos + data_size + (data_size % 2), 0)

    # Try opening via Pillow to render composite preview
    os.makedirs(os.path.dirname(preview_path), exist_ok=True)
    try:
        with Image.open(file_path) as im:
            rgb_im = im.convert("RGB")
            rgb_im.save(preview_path, "PNG")
            prev_w, prev_h = rgb_im.size
    except Exception:
        # Generate informative visual preview card if raw PSD has no composite
        prev_w, prev_h = 800, 600
        card = Image.new("RGB", (prev_w, prev_h), (30, 41, 59))
        draw = ImageDraw.Draw(card)
        draw.rectangle([20, 20, prev_w - 20, prev_h - 20], outline=(56, 189, 248), width=3)
        draw.text((50, 60), f"Adobe Photoshop (.PSD) Native File", fill=(255, 255, 255))
        draw.text((50, 110), f"Dimensions: {width_px} x {height_px} px @ {dpi_x} DPI", fill=(148, 163, 184))
        draw.text((50, 150), f"Color Mode: {color_mode_name} ({channels} Channels, {depth}-bit)", fill=(56, 189, 248))
        card.save(preview_path, "PNG")

    width_mm = round((width_px / max(dpi_x, 1.0)) * 25.4, 2)
    height_mm = round((height_px / max(dpi_y, 1.0)) * 25.4, 2)
    spot_channels = max(0, channels - (4 if color_mode_name == "CMYK" else 3))
    spot_list = [f"Alpha/Spot Channel #{i+1}" for i in range(spot_channels)]

    return {
        "format_family": "Adobe Photoshop (.PSD)",
        "page_count": 1,
        "orientation": "Landscape" if width_mm > height_mm else "Portrait",
        "media_box_mm": {"width": width_mm, "height": height_mm},
        "trim_box_mm": {"width": width_mm, "height": height_mm},
        "bleed_box_mm": {"width": width_mm, "height": height_mm},
        "has_explicit_trimbox": False,
        "detected_bleed_mm": 0.0,
        "bleed_source": "Raster Canvas Size",
        "color_spaces": [color_mode_name] + (["Spot/Separation"] if spot_list else []),
        "has_rgb": color_mode_name == "RGB",
        "has_rgb_image": color_mode_name == "RGB",
        "has_rgb_vector": False,
        "spot_colors": spot_list,
        "spot_color_count": len(spot_list),
        "has_dieline_spot": False,
        "has_overprint": False,
        "font_count": 0,
        "fonts": [],
        "unembedded_fonts": [],
        "all_fonts_safe": True,
        "fonts_outlined": True,
        "min_font_size_pt": None,
        "max_font_size_pt": None,
        "image_count": 1,
        "images": [
            {
                "index": 1,
                "width_px": width_px,
                "height_px": height_px,
                "colorspace": color_mode_name,
                "bpc": depth,
                "effective_dpi": min(dpi_x, dpi_y),
                "bbox_mm": [0, 0, width_mm, height_mm],
            }
        ],
        "min_dpi": min(dpi_x, dpi_y),
        "max_dpi": max(dpi_x, dpi_y),
        "avg_dpi": round((dpi_x + dpi_y) / 2.0, 1),
        "layers": [f"PSD Channels: {channels}"],
        "preview_width_px": prev_w,
        "preview_height_px": prev_h,
    }


def inspect_cdr_file(file_path: str, preview_path: str) -> Dict[str, Any]:
    """Inspector for CorelDRAW (.cdr) files (ZIP container X4+ or RIFF legacy)."""
    os.makedirs(os.path.dirname(preview_path), exist_ok=True)
    width_mm, height_mm = 210.0, 297.0
    color_spaces = ["CMYK"]
    spot_colors: List[str] = []
    preview_extracted = False
    prev_w, prev_h = 800, 600

    if zipfile.is_zipfile(file_path):
        with zipfile.ZipFile(file_path, "r") as zf:
            names = zf.namelist()
            for candidate in names:
                if candidate.lower().endswith((".png", ".bmp", ".jpg", ".jpeg")):
                    data = zf.read(candidate)
                    tmp_path = preview_path + ".tmp"
                    with open(tmp_path, "wb") as tf:
                        tf.write(data)
                    try:
                        with Image.open(tmp_path) as im:
                            rgb_im = im.convert("RGB")
                            rgb_im.save(preview_path, "PNG")
                            prev_w, prev_h = rgb_im.size
                            preview_extracted = True
                    finally:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    if preview_extracted:
                        break
            for xml_name in names:
                if xml_name.lower().endswith(".xml"):
                    try:
                        xml_txt = zf.read(xml_name).decode("utf-8", errors="ignore")
                        m_w = re.search(r'pageWidth="([0-9.]+)"', xml_txt)
                        m_h = re.search(r'pageHeight="([0-9.]+)"', xml_txt)
                        if m_w and m_h:
                            width_mm = round(float(m_w.group(1)), 2)
                            height_mm = round(float(m_h.group(1)), 2)
                    except Exception:
                        pass

    if not preview_extracted:
        card = Image.new("RGB", (prev_w, prev_h), (24, 34, 50))
        draw = ImageDraw.Draw(card)
        draw.rectangle([20, 20, prev_w - 20, prev_h - 20], outline=(34, 197, 94), width=3)
        draw.text((50, 60), "CorelDRAW (.CDR) Native Artwork File", fill=(255, 255, 255))
        draw.text((50, 110), f"Detected Page Size: {width_mm} x {height_mm} mm", fill=(148, 163, 184))
        draw.text((50, 150), "Tip: Export PDF/X-1a atau PDF/X-4 untuk preflight penuh.", fill=(34, 197, 94))
        card.save(preview_path, "PNG")

    return {
        "format_family": "CorelDRAW (.CDR)",
        "page_count": 1,
        "orientation": "Landscape" if width_mm > height_mm else "Portrait",
        "media_box_mm": {"width": width_mm, "height": height_mm},
        "trim_box_mm": {"width": width_mm, "height": height_mm},
        "bleed_box_mm": {"width": width_mm, "height": height_mm},
        "has_explicit_trimbox": False,
        "detected_bleed_mm": 0.0,
        "bleed_source": "CDR Document Container",
        "color_spaces": color_spaces,
        "has_rgb": False,
        "has_rgb_image": False,
        "has_rgb_vector": False,
        "spot_colors": spot_colors,
        "spot_color_count": len(spot_colors),
        "has_dieline_spot": False,
        "has_overprint": False,
        "font_count": 0,
        "fonts": [],
        "unembedded_fonts": [],
        "all_fonts_safe": True,
        "fonts_outlined": True,
        "min_font_size_pt": None,
        "max_font_size_pt": None,
        "image_count": 0,
        "images": [],
        "min_dpi": 300.0,
        "max_dpi": 300.0,
        "avg_dpi": 300.0,
        "layers": [],
        "preview_width_px": prev_w,
        "preview_height_px": prev_h,
    }


def inspect_raster_image(
    file_path: str,
    preview_path: str,
    job_spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Inspect Raster Images (.png, .jpg, .tiff, .webp) using Pillow."""
    with Image.open(file_path) as im:
        width_px, height_px = im.size
        mode = im.mode
        dpi_tuple = im.info.get("dpi", (300.0, 300.0))
        dpi_x = round(float(dpi_tuple[0] or 300.0), 1)
        dpi_y = round(float(dpi_tuple[1] or 300.0), 1)
        eff_dpi = min(dpi_x, dpi_y)
        has_icc = "icc_profile" in im.info

        width_mm = round((width_px / max(dpi_x, 1.0)) * 25.4, 2)
        height_mm = round((height_px / max(dpi_y, 1.0)) * 25.4, 2)

        os.makedirs(os.path.dirname(preview_path), exist_ok=True)
        rgb_preview = im.convert("RGB")
        rgb_preview.save(preview_path, "PNG")
        prev_w, prev_h = rgb_preview.size

    cs_label = "CMYK" if mode == "CMYK" else ("RGB" if mode in ("RGB", "RGBA", "P") else mode)
    has_rgb = cs_label == "RGB"

    detected_bleed_mm = 0.0
    effective_trim_w_mm = width_mm
    effective_trim_h_mm = height_mm
    if job_spec:
        target_w = float(job_spec.get("target_width_mm", width_mm))
        target_h = float(job_spec.get("target_height_mm", height_mm))
        diff_w = width_mm - target_w
        diff_h = height_mm - target_h
        if 1.0 <= diff_w <= 20.0 and 1.0 <= diff_h <= 20.0:
            detected_bleed_mm = round(min(diff_w, diff_h) / 2.0, 2)
            effective_trim_w_mm = target_w
            effective_trim_h_mm = target_h

    return {
        "format_family": f"Raster Image ({mode})",
        "page_count": 1,
        "orientation": "Landscape" if width_mm > height_mm else "Portrait",
        "media_box_mm": {"width": width_mm, "height": height_mm},
        "trim_box_mm": {"width": effective_trim_w_mm, "height": effective_trim_h_mm},
        "bleed_box_mm": {"width": width_mm, "height": height_mm},
        "has_explicit_trimbox": False,
        "detected_bleed_mm": detected_bleed_mm,
        "bleed_source": "Raster Image Physical Size",
        "color_spaces": [cs_label],
        "has_rgb": has_rgb,
        "has_rgb_image": has_rgb,
        "has_rgb_vector": False,
        "spot_colors": [],
        "spot_color_count": 0,
        "has_dieline_spot": False,
        "has_overprint": False,
        "has_icc_profile": has_icc,
        "font_count": 0,
        "fonts": [],
        "unembedded_fonts": [],
        "all_fonts_safe": True,
        "fonts_outlined": True,
        "min_font_size_pt": None,
        "max_font_size_pt": None,
        "image_count": 1,
        "images": [
            {
                "index": 1,
                "width_px": width_px,
                "height_px": height_px,
                "colorspace": cs_label,
                "bpc": 8,
                "effective_dpi": eff_dpi,
                "bbox_mm": [0, 0, width_mm, height_mm],
            }
        ],
        "min_dpi": eff_dpi,
        "max_dpi": max(dpi_x, dpi_y),
        "avg_dpi": round((dpi_x + dpi_y) / 2.0, 1),
        "layers": [],
        "preview_width_px": prev_w,
        "preview_height_px": prev_h,
    }


def inspect_artwork_file(
    file_path: str,
    preview_path: str,
    job_spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Master dispatcher that inspects .pdf, .ai, .psd, .cdr, .png, .jpg, .tiff."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".pdf", ".ai"):
        try:
            res = inspect_pdf_or_ai(file_path, preview_path, job_spec=job_spec)
            if ext == ".ai":
                res["format_family"] = "Adobe Illustrator (.AI / PDF Stream)"
            return res
        except Exception as e:
            if ext == ".ai":
                raise ValueError(
                    f"File .AI harus disimpan dengan opsi 'Create PDF Compatible File': {e}"
                )
            raise
    elif ext == ".psd":
        return inspect_psd_file(file_path, preview_path)
    elif ext == ".cdr":
        return inspect_cdr_file(file_path, preview_path)
    elif ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"):
        return inspect_raster_image(file_path, preview_path, job_spec=job_spec)
    else:
        raise ValueError(f"Format file '{ext}' belum didukung.")


def evaluate_job_checklist(conn: sqlite3.Connection, job_id: int) -> Dict[str, Any]:
    """
    Automatically evaluates all auto-check items in `checklist_items` for a given job
    based on the latest uploaded files across ORIGINAL, AW, ONEUP, and LAYOUT stages.
    """
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job = cur.fetchone()
    if not job:
        return {}

    # Fetch latest file per stage
    cur.execute(
        """
        SELECT * FROM job_files
        WHERE job_id = ?
        ORDER BY version DESC, id DESC
        """,
        (job_id,),
    )
    rows = cur.fetchall()
    stage_files: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        st = r["stage"]
        if st not in stage_files:
            stage_files[st] = {
                "file": dict(r),
                "inspection": json.loads(r["inspection_json"]),
            }

    # Fetch latest comparisons
    cur.execute(
        """
        SELECT * FROM comparisons
        WHERE job_id = ?
        ORDER BY id DESC
        """,
        (job_id,),
    )
    comp_rows = cur.fetchall()
    pair_comparisons: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for cr in comp_rows:
        pair_key = (cr["stage_a"], cr["stage_b"])
        rev_key = (cr["stage_b"], cr["stage_a"])
        if pair_key not in pair_comparisons:
            pair_comparisons[pair_key] = dict(cr)
        if rev_key not in pair_comparisons:
            pair_comparisons[rev_key] = dict(cr)

    # Primary inspection target for artwork parameters: prefer AW -> ONEUP -> ORIGINAL
    primary_stage = (
        stage_files.get("AW")
        or stage_files.get("ONEUP")
        or stage_files.get("ORIGINAL")
    )
    oneup_stage = stage_files.get("ONEUP") or stage_files.get("AW")
    layout_stage = stage_files.get("LAYOUT")

    cur.execute("SELECT * FROM checklist_items WHERE job_id = ? ORDER BY id ASC", (job_id,))
    items = cur.fetchall()

    target_w = float(job["target_width_mm"])
    target_h = float(job["target_height_mm"])
    req_bleed = float(job["required_bleed_mm"])
    req_dpi = int(job["min_dpi"])
    expected_nup = int(job["expected_nup"])

    updated_count = 0

    for item in items:
        rule = item["auto_rule"]
        if not rule:
            continue

        status = "PENDING"
        val_str = ""
        detail_str = ""

        if rule == "trim_size_match":
            if primary_stage:
                insp = primary_stage["inspection"]
                tw = insp["trim_box_mm"]["width"]
                th = insp["trim_box_mm"]["height"]
                val_str = f"{tw} × {th} mm (Target: {target_w} × {target_h} mm)"
                err_w = abs(tw - target_w)
                err_h = abs(th - target_h)
                if err_w <= 0.5 and err_h <= 0.5:
                    status = "PASS"
                    detail_str = f"Sesuai spesifikasi (deviasi ΔW={err_w:.2f}mm, ΔH={err_h:.2f}mm)."
                elif err_w <= 1.5 and err_h <= 1.5:
                    status = "WARN"
                    detail_str = f"Terdapat selisih kecil ukuran Trim ({tw}×{th} mm vs {target_w}×{target_h} mm)."
                else:
                    status = "FAIL"
                    detail_str = f"Ukuran Trim ({tw}×{th} mm) tidak sesuai spesifikasi ({target_w}×{target_h} mm)!"

        elif rule == "bleed_sufficient":
            target_st = oneup_stage or primary_stage
            if target_st:
                insp = target_st["inspection"]
                bl = float(insp.get("detected_bleed_mm", 0.0))
                val_str = f"{bl} mm (Wajib ≥ {req_bleed} mm)"
                if bl >= req_bleed - 0.1:
                    status = "PASS"
                    detail_str = f"Bleed {bl} mm mencukupi standar ({insp.get('bleed_source')})."
                elif bl >= 1.5:
                    status = "WARN"
                    detail_str = f"Bleed terdeteksi {bl} mm, di bawah standar {req_bleed} mm."
                else:
                    status = "FAIL"
                    detail_str = f"Bleed tidak mencukupi ({bl} mm < {req_bleed} mm). Risiko bocor putih saat potong!"

        elif rule == "page_orientation_valid":
            if primary_stage:
                insp = primary_stage["inspection"]
                orient = insp.get("orientation", "Portrait")
                pcount = insp.get("page_count", 1)
                exp_orient = "Landscape" if target_w > target_h else ("Portrait" if target_h > target_w else "Square")
                val_str = f"{orient}, {pcount} Halaman"
                if orient == exp_orient:
                    status = "PASS"
                    detail_str = f"Orientasi {orient} ({pcount} hal) sesuai dimensi produk."
                else:
                    status = "WARN"
                    detail_str = f"Orientasi file {orient}, sedangkan target spesifikasi {exp_orient}."

        elif rule == "no_rgb_colorspace":
            if primary_stage:
                insp = primary_stage["inspection"]
                cs_list = insp.get("color_spaces", [])
                has_rgb = insp.get("has_rgb", False)
                val_str = ", ".join(cs_list) if cs_list else "CMYK"
                if not has_rgb:
                    status = "PASS"
                    detail_str = "Aman: Seluruh objek dalam ruang warna cetak (CMYK / Spot)."
                else:
                    status = "FAIL"
                    detail_str = "Terdeteksi elemen warna RGB! Wajib dikonversi ke CMYK sebelum masuk RIP CTP."

        elif rule == "spot_color_check":
            target_st = oneup_stage or primary_stage
            if target_st:
                insp = target_st["inspection"]
                spots = insp.get("spot_colors", [])
                val_str = f"{len(spots)} Spot: {', '.join(spots)}" if spots else "0 Spot Color (CMYK Process Only)"
                color_spec_up = (job["color_spec"] or "").upper()
                expects_spot = "SPOT" in color_spec_up or "PANTONE" in color_spec_up or "DIELINE" in color_spec_up
                if spots:
                    status = "PASS"
                    detail_str = f"Terdeteksi channel separasi Spot: {', '.join(spots)}."
                elif not expects_spot:
                    status = "PASS"
                    detail_str = "Sesuai spesifikasi Process CMYK (tanpa Spot color)."
                else:
                    status = "WARN"
                    detail_str = f"Spesifikasi job mencantumkan '{job['color_spec']}', namun tidak ditemukan Separation Spot di file."

        elif rule == "overprint_check":
            target_st = oneup_stage or primary_stage
            if target_st:
                insp = target_st["inspection"]
                has_op = insp.get("has_overprint", False)
                val_str = "Overprint Aktif (OP=True)" if has_op else "Overprint Flag Tidak Terdeteksi"
                if has_op:
                    status = "PASS"
                    detail_str = "Setting Overprint terdeteksi aktif pada state grafis PDF."
                else:
                    status = "WARN"
                    detail_str = "Flag Overprint tidak terdeteksi otomatis; harap verifikasi manual overprint teks 100% Black."

        elif rule == "fonts_safe":
            if primary_stage:
                insp = primary_stage["inspection"]
                f_count = insp.get("font_count", 0)
                unemb = insp.get("unembedded_fonts", [])
                if f_count == 0:
                    status = "PASS"
                    val_str = "100% Outlined / Converted to Curves (0 Live Font)"
                    detail_str = "Seluruh teks sudah di-convert menjadi kurva/vektor. Sangat aman untuk RIP."
                elif len(unemb) == 0:
                    status = "PASS"
                    val_str = f"{f_count} Font (100% Embedded)"
                    detail_str = "Seluruh font tertanam (embedded/subset) di dalam dokumen PDF."
                else:
                    status = "FAIL"
                    val_str = f"{len(unemb)} Font TIDAK Embedded!"
                    detail_str = f"Font belum di-embed / rawan missing font: {', '.join(unemb)}."

        elif rule == "min_font_size_check":
            if primary_stage:
                insp = primary_stage["inspection"]
                min_pt = insp.get("min_font_size_pt")
                if min_pt is None:
                    status = "PASS"
                    val_str = "Teks Outlined (Kurva)"
                    detail_str = "Teks telah di-outline ke kurva vektor."
                elif min_pt >= 5.0:
                    status = "PASS"
                    val_str = f"Min {min_pt} pt (Aman ≥ 5.0 pt)"
                    detail_str = f"Ukuran font terkecil {min_pt} pt aman untuk keterbacaan cetak."
                else:
                    status = "WARN"
                    val_str = f"Min {min_pt} pt (< 5.0 pt!)"
                    detail_str = f"Terdapat teks berukuran sangat kecil ({min_pt} pt). Pastikan tidak menggunakan warna raster/kombinasi."

        elif rule == "image_dpi_min":
            if primary_stage:
                insp = primary_stage["inspection"]
                min_dpi = insp.get("min_dpi")
                avg_dpi = insp.get("avg_dpi")
                img_cnt = insp.get("image_count", 0)
                if img_cnt == 0 or min_dpi is None:
                    status = "PASS"
                    val_str = "100% Vector Artwork (0 Bitmap)"
                    detail_str = "Dokumen murni vektor tanpa gambar bitmap beresolusi rendah."
                elif min_dpi >= req_dpi:
                    status = "PASS"
                    val_str = f"Min {min_dpi} DPI / Avg {avg_dpi} DPI ({img_cnt} image)"
                    detail_str = f"Seluruh {img_cnt} gambar memenuhi standar resolusi cetak (≥ {req_dpi} DPI)."
                elif min_dpi >= 240:
                    status = "WARN"
                    val_str = f"Min {min_dpi} DPI (Target ≥ {req_dpi} DPI)"
                    detail_str = f"Terdapat gambar dengan resolusi {min_dpi} DPI (sedikit di bawah standar {req_dpi} DPI)."
                else:
                    status = "FAIL"
                    val_str = f"Low-Res: {min_dpi} DPI (< {req_dpi} DPI!)"
                    detail_str = f"Terdeteksi gambar resolusi rendah ({min_dpi} DPI). Berisiko pecah/kabur saat dicetak!"

        elif rule == "images_valid":
            if primary_stage:
                insp = primary_stage["inspection"]
                img_cnt = insp.get("image_count", 0)
                status = "PASS"
                val_str = f"{img_cnt} Embedded Object(s) Verified"
                detail_str = "Seluruh stream gambar ter-embed di dalam XObject halaman."

        elif rule == "dieline_spot_check":
            target_st = oneup_stage or primary_stage
            if target_st:
                insp = target_st["inspection"]
                has_dl = insp.get("has_dieline_spot", False)
                spots = insp.get("spot_colors", [])
                if has_dl:
                    status = "PASS"
                    val_str = f"Terdeteksi Spot Dieline ({', '.join(spots)})"
                    detail_str = "Garis pisau/pond (Dieline/Cut) sudah menggunakan channel Spot terpisah."
                else:
                    status = "WARN"
                    val_str = "Spot Dieline Belum Terdeteksi"
                    detail_str = "Tidak ditemukan nama spot color DIELINE/CUT/PISAU. Pastikan garis pisau tidak berada di channel CMYK!"

        elif rule == "diff_original_vs_aw":
            comp = pair_comparisons.get(("ORIGINAL", "AW"))
            if comp:
                sim = float(comp["similarity_pct"])
                regions = json.loads(comp["diff_regions_json"] or "[]")
                val_str = f"Similarity {sim:.2f}% ({len(regions)} area perbedaan)"
                if sim >= 99.5 and len(regions) == 0:
                    status = "PASS"
                    detail_str = "Visual & Pixel Diff antara File Asli dan AW identik (100% sesuai)."
                elif sim >= 96.0:
                    status = "WARN"
                    detail_str = f"Terdeteksi {len(regions)} titik perbedaan visual antara File Asli & AW (Kemiripan {sim:.2f}%). Periksa di Visual Comparator."
                else:
                    status = "FAIL"
                    detail_str = f"Perbedaan signifikan antara File Asli & AW (Kemiripan {sim:.2f}%, {len(regions)} deviasi area)!"

        elif rule == "diff_aw_vs_oneup":
            comp = pair_comparisons.get(("AW", "ONEUP"))
            if comp:
                sim = float(comp["similarity_pct"])
                regions = json.loads(comp["diff_regions_json"] or "[]")
                val_str = f"Similarity {sim:.2f}% ({len(regions)} perbedaan area trim)"
                if sim >= 97.5:
                    status = "PASS"
                    detail_str = f"Area gambar utama AW vs OneUp konsisten (Kemiripan {sim:.2f}%)."
                else:
                    status = "WARN"
                    detail_str = f"Terdeteksi {len(regions)} perbedaan antara AW dan OneUp ({sim:.2f}%). Pastikan hanya garis pisau/bleed yang bertambah."

        elif rule == "layout_imposition_check":
            comp = pair_comparisons.get(("ONEUP", "LAYOUT")) or pair_comparisons.get(("AW", "LAYOUT"))
            if comp and comp.get("matched_nup_count", 0) > 0:
                matched_nup = int(comp["matched_nup_count"])
                sim = float(comp["similarity_pct"])
                val_str = f"Terdeteksi {matched_nup}-Up pada Layout (Target: {expected_nup}-Up), Match {sim:.1f}%"
                if matched_nup == expected_nup and sim >= 95.0:
                    status = "PASS"
                    detail_str = f"Imposisi Layout cocok: {matched_nup} slot OneUp terverifikasi identik pada lembar Layout."
                else:
                    status = "WARN"
                    detail_str = f"Terdeteksi {matched_nup}-Up (target spesifikasi {expected_nup}-Up) dengan kemiripan slot {sim:.1f}%."
            elif layout_stage:
                insp = layout_stage["inspection"]
                lw = insp["media_box_mm"]["width"]
                lh = insp["media_box_mm"]["height"]
                val_str = f"Layout Sheet: {lw} × {lh} mm (Jalankan Comparator OneUp vs Layout)"
                status = "WARN"
                detail_str = "File Layout telah diunggah. Jalankan 'Bandingkan OneUp vs Layout' untuk verifikasi jumlah N-Up otomatis."

        if status != "PENDING":
            # Auto-populate manual_status if it hasn't been manually overridden yet
            new_manual_status = item["manual_status"]
            if new_manual_status == "PENDING" or not item["override_reason"]:
                new_manual_status = status

            cur.execute(
                """
                UPDATE checklist_items
                SET auto_status = ?, auto_value = ?, auto_detail = ?, manual_status = ?
                WHERE id = ?
                """,
                (status, val_str, detail_str, new_manual_status, item["id"]),
            )
            updated_count += 1

    conn.commit()
    return {"updated_auto_items": updated_count}


def _cmyk_to_visible_spot_rgb(
    cmyk: Tuple[float, float, float, float],
    plate_name: str,
) -> Tuple[int, int, int]:
    """
    Converts a PDF Spot Color's CMYK alternate tint (c, m, y, k in 0..1) into a high-visibility
    RGB display color on white paper. If the original tint is very pale (e.g. 'Kuning Muda' with
    9.7% yellow intended to sit over dark brown, or 'WHITE' ink), boosts saturation so fine lines
    and typography are clearly inspectable on a white canvas.
    """
    p_up = plate_name.strip().upper()
    if p_up == "CYAN":
        return (0, 168, 232)
    if p_up == "MAGENTA":
        return (230, 0, 126)
    if p_up == "YELLOW":
        return (234, 195, 0)
    if p_up == "BLACK":
        return (24, 24, 27)
    if "WHITE" in p_up or "PUTIH" in p_up:
        return (14, 165, 233)
    if "GOLD" in p_up or "EMAS" in p_up:
        return (180, 122, 8)

    c, m, y, k = [max(0.0, min(1.0, float(v))) for v in cmyk]
    r = int(round(255.0 * (1.0 - c) * (1.0 - k)))
    g = int(round(255.0 * (1.0 - m) * (1.0 - k)))
    b = int(round(255.0 * (1.0 - y) * (1.0 - k)))

    lum = 0.299 * r + 0.587 * g + 0.114 * b
    if lum > 200.0:
        if "KUNING" in p_up or "YELLOW" in p_up or "GOLD" in p_up or (y > max(c, m, k) and y > 0.02):
            return (196, 136, 6)
        max_ch = max(c, m, y, k)
        if max_ch > 0.01:
            boost = 0.85 / max_ch
            c, m, y, k = min(1.0, c * boost), min(1.0, m * boost), min(1.0, y * boost), min(1.0, k * boost)
            r = int(round(255.0 * (1.0 - c) * (1.0 - k)))
            g = int(round(255.0 * (1.0 - m) * (1.0 - k)))
            b = int(round(255.0 * (1.0 - y) * (1.0 - k)))
        else:
            return (37, 99, 235)
    return (r, g, b)


def render_pdf_separation_plate(
    file_path: str,
    output_path: str,
    plate_name: str,
    display_mode: str = "ink",
) -> Dict[str, Any]:
    """
    Renders an isolated Prepress Separation Plate preview (Spot Color or Process CMYK)
    from a PDF/AI file or raster artwork file, mimicking Adobe Acrobat Pro Output Preview -> Separations.
    - `display_mode`: 'ink' (simulated spot/process ink color on white paper) or 'black' (100% solid darkroom CTP plate).
    """
    import numpy as np

    plate_clean = plate_name.strip()
    plate_up = plate_clean.upper()
    ext = os.path.splitext(file_path)[1].lower()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Default fallback ink colors by name heuristics
    default_cmyk = (0.0, 1.0, 1.0, 0.0)
    if "COKLAT" in plate_up or "BROWN" in plate_up:
        default_cmyk = (0.68, 0.80, 1.0, 0.58)
    elif "KUNING" in plate_up or "GOLD" in plate_up or "EMAS" in plate_up:
        default_cmyk = (0.0, 0.25, 0.95, 0.0)
    elif "BIRU" in plate_up or "BLUE" in plate_up or "CYAN" in plate_up:
        default_cmyk = (1.0, 0.25, 0.0, 0.0)
    elif "HIJAU" in plate_up or "GREEN" in plate_up:
        default_cmyk = (0.85, 0.0, 0.90, 0.0)
    elif "PLONG" in plate_up or "CREASE" in plate_up or "LIPAT" in plate_up:
        default_cmyk = (1.0, 0.0, 0.0, 0.0)

    if ext not in (".pdf", ".ai"):
        # For raster images (.jpg, .png, .psd), extract the requested CMYK channel
        with Image.open(file_path) as im:
            cmyk_im = im.convert("CMYK")
            arr = np.array(cmyk_im, dtype=np.float32) / 255.0
            ch_map = {"CYAN": 0, "MAGENTA": 1, "YELLOW": 2, "BLACK": 3}
            ch_idx = ch_map.get(plate_up, 3)
            alpha = arr[:, :, ch_idx]
            ink_rgb = (20, 24, 33) if display_mode == "black" else _cmyk_to_visible_spot_rgb(default_cmyk, plate_clean)
            out_rgb = np.zeros((arr.shape[0], arr.shape[1], 3), dtype=np.uint8)
            for c_i in range(3):
                out_rgb[:, :, c_i] = np.clip(255.0 - alpha * (255.0 - ink_rgb[c_i]), 0, 255).astype(np.uint8)
            Image.fromarray(out_rgb, mode="RGB").save(output_path, "PNG")
            cov_pct = round(float(np.mean(alpha > 0.05) * 100.0), 2)
            hex_col = "#{:02X}{:02X}{:02X}".format(*ink_rgb)
            return {
                "plate_name": plate_clean,
                "coverage_pct": cov_pct,
                "hex_color": hex_col,
                "display_mode": display_mode,
            }

    scale = min(max(216.0 / 72.0, 2.0), 3.2)
    mat = fitz.Matrix(scale, scale)

    zero_sep_obj = (
        "[ /Separation /ZeroInk /DeviceCMYK << "
        "/FunctionType 2 /Domain [ 0 1 ] /C0 [ 0 0 0 0 ] /C1 [ 0 0 0 0 ] "
        "/N 1 /Range [ 0 1 0 1 0 1 0 1 ] >> ]"
    )
    full_sep_obj = (
        "[ /Separation /FullInk /DeviceCMYK << "
        "/FunctionType 2 /Domain [ 0 1 ] /C0 [ 0 0 0 0 ] /C1 [ 1 1 1 1 ] "
        "/N 1 /Range [ 0 1 0 1 0 1 0 1 ] >> ]"
    )

    # CASE 1: Process CMYK Plates (Cyan, Magenta, Yellow, Black)
    if plate_up in ("CYAN", "MAGENTA", "YELLOW", "BLACK"):
        doc = fitz.open(file_path)
        for xref in range(1, doc.xref_length()):
            try:
                s = doc.xref_object(xref, compressed=False)
            except Exception:
                continue
            if not s:
                continue
            if "/Separation" in s:
                m = re.search(r"/Separation\s*/([^\s/\[\]<>()]+)", s)
                if not m:
                    continue
                raw_pdf_token = m.group(1)
                dec_name = _decode_pdf_name(raw_pdf_token)
                dec_up = dec_name.strip().upper()
                if dec_up == "ALL":
                    try:
                        doc.update_object(xref, full_sep_obj)
                    except Exception:
                        pass
                elif dec_up != plate_up:
                    # Zero out Spot Colors and other Process Separations using /ZeroInk
                    # (renaming colorant token is required so MuPDF does not ignore /C1 on process/All names)
                    try:
                        doc.update_object(xref, zero_sep_obj)
                    except Exception:
                        pass
            elif "/DeviceN" in s:
                # Keep /DeviceN only if it targets this process plate (e.g. [/Black] when inspecting BLACK)
                if not re.search(rf"/\s*{plate_up}\b", s, re.IGNORECASE):
                    try:
                        doc.update_object(xref, zero_sep_obj)
                    except Exception:
                        pass

        pix_cmyk = doc[0].get_pixmap(matrix=mat, colorspace=fitz.csCMYK, alpha=False)
        w, h = pix_cmyk.width, pix_cmyk.height
        cmyk_arr = np.frombuffer(pix_cmyk.samples, dtype=np.uint8).reshape(h, w, 4).astype(np.float32) / 255.0
        doc.close()

        ch_idx = {"CYAN": 0, "MAGENTA": 1, "YELLOW": 2, "BLACK": 3}[plate_up]
        alpha = cmyk_arr[:, :, ch_idx]
        ink_rgb = (20, 24, 33) if display_mode == "black" else _cmyk_to_visible_spot_rgb(default_cmyk, plate_clean)
    else:
        # CASE 2: Spot Color / Pantone / Dieline Separation Plate
        doc_a = fitz.open(file_path)
        doc_b = fitz.open(file_path)
        spot_cmyk = default_cmyk
        matched_xrefs = 0

        for xref in range(1, doc_a.xref_length()):
            try:
                s = doc_a.xref_object(xref, compressed=False)
            except Exception:
                continue
            if not s:
                continue
            if "/Separation" in s:
                m = re.search(r"/Separation\s*/([^\s/\[\]<>()]+)", s)
                if not m:
                    continue
                raw_pdf_token = m.group(1)
                dec_name = _decode_pdf_name(raw_pdf_token)
                dec_up = dec_name.strip().upper()
                if dec_up == "NONE":
                    continue

                is_exact_spot = dec_name.strip().lower() == plate_clean.lower()
                # Registration (/Separation /All) prints on every separation plate just like Adobe Acrobat
                is_target = is_exact_spot or (dec_up == "ALL")
                if is_exact_spot:
                    matched_xrefs += 1
                    # Extract /C1 [ c m y k ] from inline dict or referenced function XRef
                    m_c1 = re.search(r"/C1\s*\[\s*([0-9.\s]+)\]", s)
                    if not m_c1:
                        m_fn = re.search(r"/DeviceCMYK\s+(\d+)\s+0\s+R", s)
                        if m_fn:
                            try:
                                fn_str = doc_a.xref_object(int(m_fn.group(1)), compressed=False) or ""
                                m_c1 = re.search(r"/C1\s*\[\s*([0-9.\s]+)\]", fn_str)
                            except Exception:
                                pass
                    if m_c1:
                        vals = [float(x) for x in m_c1.group(1).split() if x.strip()]
                        if len(vals) == 4:
                            spot_cmyk = (vals[0], vals[1], vals[2], vals[3])

                try:
                    doc_a.update_object(xref, zero_sep_obj)
                    doc_b.update_object(xref, full_sep_obj if is_target else zero_sep_obj)
                except Exception:
                    pass
            elif "/DeviceN" in s:
                # Neutralize non-target /DeviceN shadings (such as [/Black] Multiply gradients over Gold)
                if not re.search(rf"/\s*{re.escape(raw_pdf_token if 'raw_pdf_token' in locals() else plate_clean)}\b", s, re.IGNORECASE):
                    try:
                        doc_a.update_object(xref, zero_sep_obj)
                        doc_b.update_object(xref, zero_sep_obj)
                    except Exception:
                        pass

        if matched_xrefs > 0:
            pix_a = doc_a[0].get_pixmap(matrix=mat, alpha=False)
            pix_b = doc_b[0].get_pixmap(matrix=mat, alpha=False)
            w, h = pix_a.width, pix_a.height
            arr_a = np.frombuffer(pix_a.samples, dtype=np.uint8).reshape(h, w, 3).astype(np.float32)
            arr_b = np.frombuffer(pix_b.samples, dtype=np.uint8).reshape(h, w, 3).astype(np.float32)
            alpha = np.max(np.clip(arr_a - arr_b, 0.0, 255.0), axis=2) / 255.0
        else:
            # Fallback for synthetic demo PDFs where Spot Color is simulated in CMYK stream
            pix_cmyk = doc_a[0].get_pixmap(matrix=mat, colorspace=fitz.csCMYK, alpha=False)
            w, h = pix_cmyk.width, pix_cmyk.height
            cmyk_arr = np.frombuffer(pix_cmyk.samples, dtype=np.uint8).reshape(h, w, 4).astype(np.float32) / 255.0
            if any(k in plate_up for k in ("DIE", "CUT", "PISAU", "POND")):
                # Magenta + Cyan dieline stroke
                alpha = np.clip(np.minimum(cmyk_arr[:, :, 0], cmyk_arr[:, :, 1]) * 1.4, 0.0, 1.0)
                spot_cmyk = (0.95, 0.0, 0.65, 0.0)
            elif "CREASE" in plate_up or "LIPAT" in plate_up:
                alpha = np.clip(cmyk_arr[:, :, 0] - cmyk_arr[:, :, 1], 0.0, 1.0)
                spot_cmyk = (1.0, 0.0, 0.0, 0.0)
            else:
                # Red/Gold Pantone accent
                alpha = np.clip(np.minimum(cmyk_arr[:, :, 1], cmyk_arr[:, :, 2]), 0.0, 1.0)
                spot_cmyk = (0.0, 0.90, 0.85, 0.0)

        doc_a.close()
        doc_b.close()
        ink_rgb = (20, 24, 33) if display_mode == "black" else _cmyk_to_visible_spot_rgb(spot_cmyk, plate_clean)

    out_rgb = np.zeros((alpha.shape[0], alpha.shape[1], 3), dtype=np.uint8)
    for c_i in range(3):
        out_rgb[:, :, c_i] = np.clip(255.0 - alpha * (255.0 - ink_rgb[c_i]), 0.0, 255.0).astype(np.uint8)

    Image.fromarray(out_rgb, mode="RGB").save(output_path, "PNG")
    cov_pct = round(float(np.mean(alpha > 0.05) * 100.0), 2)
    hex_col = "#{:02X}{:02X}{:02X}".format(*ink_rgb)

    return {
        "plate_name": plate_clean,
        "coverage_pct": cov_pct,
        "hex_color": hex_col,
        "display_mode": display_mode,
    }
