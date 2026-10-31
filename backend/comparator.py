import json
import os
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


def _non_max_suppression(
    boxes: List[Tuple[int, int, int, int, float, int]], overlap_thresh: float = 0.35
) -> List[Tuple[int, int, int, int, float, int]]:
    """Filter overlapping template matches (x, y, w, h, score, angle)."""
    if not boxes:
        return []
    boxes_sorted = sorted(boxes, key=lambda b: b[4], reverse=True)
    picked: List[Tuple[int, int, int, int, float, int]] = []

    for candidate in boxes_sorted:
        cx1, cy1, cw, ch, cscore, cangle = candidate
        cx2, cy2 = cx1 + cw, cy1 + ch
        c_area = float(cw * ch)
        keep = True
        for p in picked:
            px1, py1, pw, ph, _, _ = p
            px2, py2 = px1 + pw, py1 + ph
            xx1 = max(cx1, px1)
            yy1 = max(cy1, py1)
            xx2 = min(cx2, px2)
            yy2 = min(cy2, py2)
            w = max(0, xx2 - xx1)
            h = max(0, yy2 - yy1)
            inter = float(w * h)
            union = c_area + float(pw * ph) - inter
            if union > 0 and (inter / union) > overlap_thresh:
                keep = False
                break
        if keep:
            picked.append(candidate)
    # Sort top-to-bottom, left-to-right for natural UP #1, UP #2 numbering
    picked.sort(key=lambda b: (b[1] // 40, b[0]))
    return picked


def compare_oneup_to_layout(
    img_oneup: np.ndarray,
    img_layout: np.ndarray,
    insp_oneup: Dict[str, Any],
    insp_layout: Dict[str, Any],
    diff_output_path: str,
) -> Dict[str, Any]:
    """
    Smart Imposition Comparator: Matches a 1-Up artwork (OneUp or AW) against a multi-up Layout sheet.
    Finds how many N-Up slots are present, checks upright (0 deg) and head-to-head (180 deg) orientations,
    and checks pixel fidelity of each placed slot.
    """
    lh, lw = img_layout.shape[:2]
    oh, ow = img_oneup.shape[:2]

    # Calculate expected scale ratio from physical mm dimensions
    oneup_w_mm = float(insp_oneup.get("media_box_mm", {}).get("width", 100.0))
    layout_w_mm = float(insp_layout.get("media_box_mm", {}).get("width", 250.0))

    expected_w_px = int(round((oneup_w_mm / max(layout_w_mm, 1.0)) * lw))
    scale_factor = expected_w_px / max(float(ow), 1.0)
    target_w = max(40, min(int(round(ow * scale_factor)), lw - 4))
    target_h = max(40, min(int(round(oh * scale_factor)), lh - 4))

    tpl_0 = cv2.resize(img_oneup, (target_w, target_h), interpolation=cv2.INTER_AREA)
    tpl_180 = cv2.rotate(tpl_0, cv2.ROTATE_180)

    gray_layout = cv2.cvtColor(img_layout, cv2.COLOR_BGR2GRAY)
    gray_tpl_0 = cv2.cvtColor(tpl_0, cv2.COLOR_BGR2GRAY)
    gray_tpl_180 = cv2.cvtColor(tpl_180, cv2.COLOR_BGR2GRAY)

    candidates: List[Tuple[int, int, int, int, float, int]] = []
    threshold = 0.65

    for angle, gtpl in ((0, gray_tpl_0), (180, gray_tpl_180)):
        res = cv2.matchTemplate(gray_layout, gtpl, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= threshold)
        for x, y in zip(xs, ys):
            score = float(res[y, x])
            candidates.append((int(x), int(y), target_w, target_h, score, angle))

    matched_slots = _non_max_suppression(candidates, overlap_thresh=0.3)

    # Render annotated Layout Verification Canvas
    vis = img_layout.copy()
    slot_regions: List[Dict[str, Any]] = []
    slot_similarities: List[float] = []

    for idx, (x, y, w, h, score, angle) in enumerate(matched_slots):
        roi = img_layout[y : y + h, x : x + w]
        ref = tpl_0 if angle == 0 else tpl_180
        diff = cv2.absdiff(roi, ref)
        diff_gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
        _, thresh_mask = cv2.threshold(diff_gray, 38, 255, cv2.THRESH_BINARY)
        changed_px = int(np.count_nonzero(thresh_mask))
        total_px = max(1, w * h)
        slot_sim = round(max(0.0, 100.0 - (changed_px / total_px) * 100.0), 2)
        slot_similarities.append(slot_sim)

        is_clean = slot_sim >= 96.0
        color = (34, 197, 94) if is_clean else (0, 68, 255)  # BGR: Green or Red-Orange
        cv2.rectangle(vis, (x, y), (x + w, y + h), color, 3)

        # Highlight internal pixel deviations inside this slot if any
        if not is_clean:
            red_overlay = np.zeros_like(roi)
            red_overlay[:, :] = (120, 20, 255)
            mask_bool = thresh_mask > 0
            vis_roi = vis[y : y + h, x : x + w]
            vis_roi[mask_bool] = cv2.addWeighted(vis_roi, 0.35, red_overlay, 0.65, 0)[mask_bool]

        label = f"UP #{idx + 1} ({angle} deg) - {slot_sim:.1f}%"
        (tw, th_txt), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        cv2.rectangle(vis, (x, max(0, y - th_txt - 10)), (x + tw + 12, y), color, -1)
        cv2.putText(
            vis,
            label,
            (x + 6, max(15, y - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        slot_regions.append(
            {
                "index": idx + 1,
                "type": "IMPOSITION_SLOT" if is_clean else "IMPOSITION_MISMATCH",
                "x_pct": round((x / lw) * 100.0, 2),
                "y_pct": round((y / lh) * 100.0, 2),
                "w_pct": round((w / lw) * 100.0, 2),
                "h_pct": round((h / lh) * 100.0, 2),
                "rotation_deg": angle,
                "similarity_pct": slot_sim,
                "label": f"Slot UP #{idx + 1} (Rotasi {angle}°, Kemiripan {slot_sim}%)",
            }
        )

    os.makedirs(os.path.dirname(diff_output_path), exist_ok=True)
    cv2.imwrite(diff_output_path, vis)

    avg_sim = round(sum(slot_similarities) / len(slot_similarities), 2) if slot_similarities else 0.0
    nup_count = len(matched_slots)

    summary = (
        f"Imposisi Layout: Terdeteksi {nup_count}-Up pada lembar Layout dengan rata-rata kecocokan slot {avg_sim:.2f}%."
        if nup_count > 0
        else "Tidak ditemukan kecocokan pola OneUp pada Layout (periksa skala atau orientasi)."
    )

    return {
        "mode": "LAYOUT_IMPOSITION",
        "similarity_pct": avg_sim,
        "diff_pixel_count": 0,
        "matched_nup_count": nup_count,
        "regions": slot_regions,
        "summary": summary,
    }


def compare_stage_files(
    path_a: str,
    path_b: str,
    stage_a: str,
    stage_b: str,
    insp_a: Dict[str, Any],
    insp_b: Dict[str, Any],
    diff_output_path: str,
) -> Dict[str, Any]:
    """
    Runs automated visual & pixel comparison between Stage A and Stage B previews.
    Automatically handles:
    - 1-Up vs Layout imposition template matching
    - Trim-box crop alignment when comparing Trim-sized file (ORIGINAL/AW) vs Bleed-sized file (ONEUP)
    - High-contrast Pixel Diff Heatmap & Difference Region Bounding Box extraction
    """
    img_a = cv2.imread(path_a)
    img_b = cv2.imread(path_b)
    if img_a is None or img_b is None:
        raise ValueError("Gagal membaca gambar preview untuk perbandingan visual.")

    # Case 1: Comparing 1-Up (ORIGINAL / AW / ONEUP) against Multi-Up LAYOUT
    if stage_b == "LAYOUT" and stage_a in ("ONEUP", "AW", "ORIGINAL"):
        return compare_oneup_to_layout(img_a, img_b, insp_a, insp_b, diff_output_path)
    if stage_a == "LAYOUT" and stage_b in ("ONEUP", "AW", "ORIGINAL"):
        return compare_oneup_to_layout(img_b, img_a, insp_b, insp_a, diff_output_path)

    # Case 2: Comparing two 1-Up stages (e.g. ORIGINAL vs AW, or AW vs ONEUP)
    # Check if one file includes outer bleed while the other is Trim-only
    bleed_a = float(insp_a.get("detected_bleed_mm", 0.0))
    bleed_b = float(insp_b.get("detected_bleed_mm", 0.0))
    media_w_a = float(insp_a.get("media_box_mm", {}).get("width", 100.0))
    media_h_a = float(insp_a.get("media_box_mm", {}).get("height", 100.0))
    media_w_b = float(insp_b.get("media_box_mm", {}).get("width", 100.0))
    media_h_b = float(insp_b.get("media_box_mm", {}).get("height", 100.0))

    work_a = img_a.copy()
    work_b = img_b.copy()

    # If B has bleed and A does not (e.g. ORIGINAL/AW without bleed vs ONEUP with bleed), crop B to TrimBox for fair comparison
    if bleed_b > 0.5 and bleed_a == 0.0 and abs((media_w_b - 2 * bleed_b) - media_w_a) <= 1.5:
        hb, wb = work_b.shape[:2]
        crop_x = int(round((bleed_b / media_w_b) * wb))
        crop_y = int(round((bleed_b / media_h_b) * hb))
        work_b = work_b[crop_y : hb - crop_y, crop_x : wb - crop_x]
    elif bleed_a > 0.5 and bleed_b == 0.0 and abs((media_w_a - 2 * bleed_a) - media_w_b) <= 1.5:
        ha, wa = work_a.shape[:2]
        crop_x = int(round((bleed_a / media_w_a) * wa))
        crop_y = int(round((bleed_a / media_h_a) * ha))
        work_a = work_a[crop_y : ha - crop_y, crop_x : wa - crop_x]

    target_h, target_w = work_a.shape[:2]
    if work_b.shape[:2] != (target_h, target_w):
        work_b = cv2.resize(work_b, (target_w, target_h), interpolation=cv2.INTER_AREA)

    # Compute per-channel absolute difference
    diff_bgr = cv2.absdiff(work_a, work_b)
    diff_max = np.max(diff_bgr, axis=2)

    # Threshold at 28/255 to ignore sub-pixel anti-aliasing noise
    _, mask = cv2.threshold(diff_max, 28, 255, cv2.THRESH_BINARY)

    # Morphological opening (remove 1px speckle) then dilation (group nearby changed characters/shapes)
    kernel_open = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    mask_clean = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_open)
    kernel_dilate = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    mask_grouped = cv2.dilate(mask_clean, kernel_dilate, iterations=1)

    diff_pixel_count = int(np.count_nonzero(mask_clean))
    total_pixels = max(1, target_w * target_h)
    similarity_pct = round(max(0.0, 100.0 - (diff_pixel_count / total_pixels) * 100.0), 2)

    contours, _ = cv2.findContours(mask_grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Build a crisp Visual Diff Heatmap (dimmed desaturated base + glowing magenta/red diff pixels + numbered boxes)
    gray_base = cv2.cvtColor(work_b, cv2.COLOR_BGR2GRAY)
    dim_base = cv2.cvtColor(gray_base, cv2.COLOR_GRAY2BGR)
    dim_base = cv2.addWeighted(dim_base, 0.45, np.full_like(dim_base, 245), 0.55, 0)

    # Blend actual artwork lightly so operator can see full context
    context_img = cv2.addWeighted(work_b, 0.35, dim_base, 0.65, 0)

    # Highlight changed pixels in intense Magenta-Red
    highlight_layer = np.zeros_like(context_img)
    highlight_layer[:, :] = (90, 0, 255)  # BGR: Vivid Crimson/Magenta
    changed_bool = mask_clean > 0
    context_img[changed_bool] = cv2.addWeighted(
        context_img, 0.15, highlight_layer, 0.85, 0
    )[changed_bool]

    regions: List[Dict[str, Any]] = []
    # Sort contours by area descending
    valid_contours = [c for c in contours if cv2.contourArea(c) >= 35]
    valid_contours.sort(key=cv2.contourArea, reverse=True)

    for idx, cnt in enumerate(valid_contours[:20]):
        x, y, w, h = cv2.boundingRect(cnt)
        # Draw bounding box & badge on heatmap
        cv2.rectangle(context_img, (x, y), (x + w, y + h), (0, 30, 240), 2)
        badge_txt = f"DIFF #{idx + 1}"
        (tw, th_txt), _ = cv2.getTextSize(badge_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
        cv2.rectangle(
            context_img,
            (x, max(0, y - th_txt - 8)),
            (x + tw + 10, y),
            (0, 30, 240),
            -1,
        )
        cv2.putText(
            context_img,
            badge_txt,
            (x + 5, max(12, y - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

        regions.append(
            {
                "index": idx + 1,
                "type": "PIXEL_DEVIATION",
                "x_pct": round(((x + w / 2.0) / target_w) * 100.0, 2),
                "y_pct": round(((y + h / 2.0) / target_h) * 100.0, 2),
                "box_x_pct": round((x / target_w) * 100.0, 2),
                "box_y_pct": round((y / target_h) * 100.0, 2),
                "w_pct": round((w / target_w) * 100.0, 2),
                "h_pct": round((h / target_h) * 100.0, 2),
                "area_px": int(w * h),
                "label": f"Area Perbedaan #{idx + 1} ({round((w/target_w)*media_w_a, 1)}×{round((h/target_h)*media_h_a, 1)} mm)",
            }
        )

    os.makedirs(os.path.dirname(diff_output_path), exist_ok=True)
    cv2.imwrite(diff_output_path, context_img)

    if len(regions) == 0:
        summary = f"Identik: Tidak ditemukan perbedaan visual antara {stage_a} dan {stage_b} (Kemiripan {similarity_pct:.2f}%)."
    else:
        summary = (
            f"Terdeteksi {len(regions)} area perbedaan visual antara {stage_a} dan {stage_b} "
            f"(Kemiripan {similarity_pct:.2f}%, {diff_pixel_count:,} piksel berbeda)."
        )

    return {
        "mode": "PIXEL_DIFF",
        "similarity_pct": similarity_pct,
        "diff_pixel_count": diff_pixel_count,
        "matched_nup_count": 1,
        "regions": regions,
        "summary": summary,
    }
