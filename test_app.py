import io
import os
import struct
from fastapi.testclient import TestClient
from PIL import Image

from main import app, on_startup

on_startup()
client = TestClient(app)


def test_dashboard_and_seeded_jobs():
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_jobs" in data
    assert "auto_checks_run" in data

    jobs_resp = client.get("/api/jobs")
    assert jobs_resp.status_code == 200
    jobs = jobs_resp.json()
    assert isinstance(jobs, list)


def test_job_detail_and_auto_parameters():
    # Create a temporary job with demo files to verify auto-parameters and 4-Up layout matching
    create_resp = client.post(
        "/api/jobs",
        json={
            "job_number": "JOB-TEST-AUTO",
            "product_name": "Box Uji Auto Parameter",
            "customer_name": "PT Testing",
            "item_code": "PKG-AUTO-01",
            "target_width_mm": 120.0,
            "target_height_mm": 180.0,
            "required_bleed_mm": 3.0,
            "min_dpi": 300,
            "expected_nup": 4,
            "layout_width_mm": 290.0,
            "layout_height_mm": 410.0,
            "generate_demo_files": True,
            "demo_has_defect": True,
        },
    )
    assert create_resp.status_code == 200
    temp_id = create_resp.json()["id"]

    try:
        det_defect = client.get(f"/api/jobs/{temp_id}").json()
        assert "ORIGINAL" in det_defect["stage_files"]
        assert "AW" in det_defect["stage_files"]
        assert "ONEUP" in det_defect["stage_files"]
        assert "LAYOUT" in det_defect["stage_files"]

        # Check AW file on defect job detected RGB and 180 DPI
        aw_insp = det_defect["stage_files"]["AW"]["inspection"]
        assert aw_insp["has_rgb"] is True
        assert aw_insp["min_dpi"] <= 190

        # Check ONEUP file detected 3.0mm bleed and Dieline spot color
        oneup_insp = det_defect["stage_files"]["ONEUP"]["inspection"]
        assert oneup_insp["detected_bleed_mm"] == 3.0
        assert oneup_insp["has_dieline_spot"] is True

        # Check Visual Comparisons detected 4-Up Layout Imposition
        layout_comp = next(
            c for c in det_defect["comparisons"] if "LAYOUT" in (c["stage_a"], c["stage_b"])
        )
        assert layout_comp["matched_nup_count"] == 4
    finally:
        client.delete(f"/api/jobs/{temp_id}?actor_name=Dio&actor_role=Kepala+Prepress")


def test_upload_raster_and_psd_inspection():
    create_resp = client.post(
        "/api/jobs",
        json={
            "job_number": "JOB-TEST-UPLOAD",
            "product_name": "Box Uji Upload",
            "customer_name": "PT Testing",
            "item_code": "PKG-UP-01",
            "generate_demo_files": False,
        },
    )
    jid = create_resp.json()["id"]
    try:
        # 1. Test uploading a 300 DPI CMYK TIFF/JPEG image
        img = Image.new("CMYK", (1417, 2126), (10, 80, 90, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", dpi=(300, 300))
        buf.seek(0)

        up_resp = client.post(
            f"/api/jobs/{jid}/upload",
            data={
                "stage": "AW",
                "uploaded_by": "Naufal",
                "uploaded_role": "Operator Prepress",
            },
            files={"file": ("test_cmyk_artwork.jpg", buf, "image/jpeg")},
        )
        assert up_resp.status_code == 200
        insp = up_resp.json()["inspection"]
        assert "CMYK" in insp["color_spaces"]
        assert insp["has_rgb"] is False

        # 2. Test uploading a synthetic valid Adobe Photoshop (.psd) header
        psd_header = struct.pack(">4sH6sHIIHH", b"8BPS", 1, b"\x00" * 6, 4, 600, 800, 8, 4)
        psd_body = psd_header + struct.pack(">III", 0, 0, 0)
        psd_resp = client.post(
            f"/api/jobs/{jid}/upload",
            data={
                "stage": "ORIGINAL",
                "uploaded_by": "Halim",
                "uploaded_role": "Operator Prepress",
            },
            files={"file": ("desain_master.psd", io.BytesIO(psd_body), "application/octet-stream")},
        )
        assert psd_resp.status_code == 200
        psd_insp = psd_resp.json()["inspection"]
        assert psd_insp["format_family"] == "Adobe Photoshop (.PSD)"
        assert "CMYK" in psd_insp["color_spaces"]
    finally:
        client.delete(f"/api/jobs/{jid}?actor_name=Dio&actor_role=Kepala+Prepress")


def test_annotations_and_approval_and_pdf_report():
    create_resp = client.post(
        "/api/jobs",
        json={
            "job_number": "JOB-TEST-APPROVAL",
            "product_name": "Box Uji Approval",
            "customer_name": "PT Testing",
            "item_code": "PKG-APP-01",
            "generate_demo_files": False,
        },
    )
    jid = create_resp.json()["id"]
    try:
        # Create Pin Annotation
        pin_resp = client.post(
            f"/api/jobs/{jid}/annotations",
            json={
                "stage": "AW",
                "x_pct": 33.5,
                "y_pct": 62.1,
                "w_pct": 10.0,
                "h_pct": 8.0,
                "severity": "MAJOR",
                "category": "Barcode / Eye Mark",
                "title": "Quiet Zone Barcode Terlalu Sempit",
                "comment": "Margin kanan barcode kurang dari 3mm dari garis lipat.",
                "checklist_code": "PKG-02",
                "actor_name": "Dina",
                "actor_role": "QC Prepress",
            },
        )
        assert pin_resp.status_code == 200
        ann_id = pin_resp.json()["id"]

        # Resolve Pin Annotation
        res_resp = client.put(
            f"/api/jobs/{jid}/annotations/{ann_id}",
            json={
                "status": "RESOLVED",
                "resolved_note": "Sudah digeser 4mm ke kiri.",
                "actor_name": "Naufal",
                "actor_role": "Operator Prepress",
            },
        )
        assert res_resp.status_code == 200

        # Sign off Approval
        app_resp = client.post(
            f"/api/jobs/{jid}/approve",
            json={
                "role_stage": "OPERATOR",
                "decision": "APPROVED",
                "approver_name": "Naufal",
                "approver_nik": "PRP-003",
                "approver_role_label": "Operator Prepress",
                "notes": "Siap diperiksa QC.",
            },
        )
        assert app_resp.status_code == 200
        assert len(app_resp.json()["signature_hash"]) >= 16

        # Export PDF Certificate
        pdf_resp = client.get(f"/api/jobs/{jid}/report.pdf")
        assert pdf_resp.status_code == 200
        assert pdf_resp.headers["content-type"] == "application/pdf"
        assert pdf_resp.content[:4] == b"%PDF"
    finally:
        client.delete(f"/api/jobs/{jid}?actor_name=Dio&actor_role=Kepala+Prepress")


def test_create_edit_and_delete_job():
    # 1. Create a temporary job
    create_resp = client.post(
        "/api/jobs",
        json={
            "job_number": "JOB-2026-9999",
            "product_name": "Box Uji Edit & Hapus",
            "customer_name": "PT Testing Prepress",
            "item_code": "PKG-TEST-99",
            "target_width_mm": 100.0,
            "target_height_mm": 150.0,
            "required_bleed_mm": 3.0,
            "min_dpi": 300,
            "generate_demo_files": False,
        },
    )
    assert create_resp.status_code == 200
    new_id = create_resp.json()["id"]

    # 2. Edit the job
    edit_resp = client.put(
        f"/api/jobs/{new_id}",
        json={
            "job_number": "JOB-2026-9999-REV",
            "product_name": "Box Uji Edit & Hapus (Updated)",
            "customer_name": "PT Testing Prepress Updated",
            "item_code": "PKG-TEST-99-R2",
            "package_type": "Flexible Packaging / Sachet",
            "print_method": "Cetak Digital",
            "revision_no": 2,
            "target_width_mm": 110.0,
            "target_height_mm": 160.0,
            "required_bleed_mm": 2.5,
            "min_dpi": 300,
            "color_spec": "CMYK + White Ink",
            "expected_nup": 6,
            "layout_width_mm": 350.0,
            "layout_height_mm": 500.0,
            "notes": "Spesifikasi telah diperbarui.",
            "actor_name": "Dio",
            "actor_role": "Kepala Prepress",
        },
    )
    assert edit_resp.status_code == 200
    det = client.get(f"/api/jobs/{new_id}").json()["job"]
    assert det["job_number"] == "JOB-2026-9999-REV"
    assert det["revision_no"] == 2
    assert det["target_width_mm"] == 110.0
    assert det["expected_nup"] == 6

    # 3. Delete the job
    del_resp = client.delete(f"/api/jobs/{new_id}?actor_name=Dio&actor_role=Kepala+Prepress")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    # Verify it is gone
    missing_resp = client.get(f"/api/jobs/{new_id}")
    assert missing_resp.status_code == 404


def test_users_and_auth_login():
    users_resp = client.get("/api/users")
    assert users_resp.status_code == 200
    users = users_resp.json()
    names = [u["full_name"] for u in users]
    assert names == ["Dio", "Dina", "Naufal", "Halim"]
    for u in users:
        assert "password_hash" not in u

    # Valid logins for all 4 accounts
    for uname, pwd, expected_name in [
        ("dio", "dio123", "Dio"),
        ("dina", "dina123", "Dina"),
        ("naufal", "naufal123", "Naufal"),
        ("halim", "halim123", "Halim"),
    ]:
        login_resp = client.post("/api/auth/login", json={"username": uname, "password": pwd})
        assert login_resp.status_code == 200
        payload = login_resp.json()
        assert payload["user"]["full_name"] == expected_name
        assert len(payload["token"]) >= 24

    # Invalid password should return 401
    bad_resp = client.post("/api/auth/login", json={"username": "dio", "password": "wrongpassword"})
    assert bad_resp.status_code == 401


def test_three_official_checklist_forms():
    create_resp = client.post(
        "/api/jobs",
        json={
            "job_number": "JOB-TEST-3FORMS",
            "product_name": "Box Uji 3 Form Checklist",
            "customer_name": "PT Prima Kemasan",
            "item_code": "PKG-3FORM-01",
            "target_width_mm": 120.0,
            "target_height_mm": 180.0,
            "required_bleed_mm": 3.0,
            "min_dpi": 300,
            "expected_nup": 4,
            "layout_width_mm": 290.0,
            "layout_height_mm": 410.0,
            "generate_demo_files": True,
            "demo_has_defect": False,
        },
    )
    assert create_resp.status_code == 200
    jid = create_resp.json()["id"]

    try:
        forms_resp = client.get(f"/api/jobs/{jid}/checklist-forms")
        assert forms_resp.status_code == 200
        forms = forms_resp.json()
        assert set(forms.keys()) == {"AWAL", "ONEUP", "AKHIR"}

        # Check auto-filled fields
        awal_data = forms["AWAL"]["form_data"]
        assert awal_data["customer"] == "PT Prima Kemasan"
        assert awal_data["warna_cmyk"] is True
        assert awal_data["bleed_mm"] == "3"
        assert "acuan_warna_acc_foto" in awal_data
        assert "acuan_warna_acc_datang" in awal_data
        assert "acuan_warna_lainnya" in awal_data
        assert "acuan_warna_lainnya_text" in awal_data

        akhir_data = forms["AKHIR"]["form_data"]
        assert akhir_data["jumlah_up_druk"] == "4"
        assert "120 x 180" in akhir_data["ukuran_dimensi_jadi_mm"]
        assert "mesin_sm" in akhir_data

        # Save manual update on AWAL
        save_resp = client.post(
            f"/api/jobs/{jid}/checklist-forms/AWAL",
            json={
                "form_data": {"catatan_notes": "Sudah dicek sesuai file customer."},
                "actor_name": "Naufal",
                "actor_role": "Operator Prepress",
            },
        )
        assert save_resp.status_code == 200
        assert save_resp.json()["form_data"]["catatan_notes"] == "Sudah dicek sesuai file customer."

        # Sign DESAINER and REVIEWER on AWAL
        s1 = client.post(
            f"/api/jobs/{jid}/checklist-forms/AWAL/sign",
            json={
                "slot": "DESAINER",
                "action": "sign",
                "actor_name": "Naufal",
                "actor_nik": "PRP-003",
                "actor_role": "Operator Prepress",
            },
        )
        assert s1.status_code == 200
        assert s1.json()["desainer_name"] == "Naufal"

        s2 = client.post(
            f"/api/jobs/{jid}/checklist-forms/AWAL/sign",
            json={
                "slot": "REVIEWER",
                "action": "sign",
                "actor_name": "Dina",
                "actor_nik": "PRP-002",
                "actor_role": "QC Prepress",
            },
        )
        assert s2.status_code == 200
        assert s2.json()["reviewer_name"] == "Dina"
        assert s2.json()["is_complete"] is True

        # Verify printable HTML pages for all 3 forms
        for ftype, expected_header in [
            ("AWAL", "CHECK LIST AWAL"),
            ("ONEUP", "CHECK LIST ONEUP"),
            ("AKHIR", "CHECK LIST AKHIR"),
        ]:
            pr = client.get(f"/api/jobs/{jid}/checklist-forms/{ftype}/print")
            assert pr.status_code == 200
            assert expected_header in pr.text
            assert "ACC Foto" in pr.text
            assert "ACC Datang" in pr.text
            if ftype == "AKHIR":
                assert "SM" in pr.text
    finally:
        client.delete(f"/api/jobs/{jid}?actor_name=Dio&actor_role=Kepala+Prepress")


def test_spk_scan_and_autofill_job_creation():
    # 1. Test scanning the sample SPK (WH/MO/05661)
    scan_resp = client.post("/api/spk/scan-sample")
    assert scan_resp.status_code == 200
    spk = scan_resp.json()
    assert spk["job_number"] == "SPK-15637 (WH/MO/05661)"
    assert spk["product_name"] == "Stiker 3 Macam Nest Box"
    assert spk["customer_name"] == "Tommy Leong"
    assert spk["item_code"] == "SL-LN-ST"
    assert spk["target_width_mm"] == 228.0
    assert spk["target_height_mm"] == 195.0
    assert spk["layout_width_mm"] == 370.0
    assert spk["layout_height_mm"] == 520.0
    assert spk["expected_nup"] == 7

    # 2. Create a job using the extracted SPK data & checklist_overrides
    create_resp = client.post(
        "/api/jobs",
        json={
            "job_number": "SPK-15637-TEST",
            "product_name": spk["product_name"],
            "customer_name": spk["customer_name"],
            "item_code": spk["item_code"],
            "package_type": spk["package_type"],
            "print_method": spk["print_method"],
            "target_width_mm": spk["target_width_mm"],
            "target_height_mm": spk["target_height_mm"],
            "layout_width_mm": spk["layout_width_mm"],
            "layout_height_mm": spk["layout_height_mm"],
            "expected_nup": spk["expected_nup"],
            "color_spec": spk["color_spec"],
            "notes": spk["notes"],
            "generate_demo_files": False,
            "checklist_overrides": spk["checklist_overrides"],
        },
    )
    assert create_resp.status_code == 200
    jid = create_resp.json()["id"]

    try:
        forms = client.get(f"/api/jobs/{jid}/checklist-forms").json()
        akhir = forms["AKHIR"]["form_data"]
        assert akhir["customer"] == "Tommy Leong"
        assert akhir["uk_potong_kertas_mm"] == "370 x 520"
        assert akhir["jumlah_up_druk"] == "7"
        assert akhir["mesin_pm"] is True
        assert akhir["lam_glossy"] is True
        assert akhir["acuan_warna_cetakan_lama"] is True
    finally:
        client.delete(f"/api/jobs/{jid}?actor_name=Dio&actor_role=Kepala+Prepress")
