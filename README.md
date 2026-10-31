# PrepressQC Studio

Aplikasi web internal untuk Divisi Prepress/Design: pemeriksaan file artwork yang terstruktur, otomatis, dan terdokumentasi.

## Menjalankan

```bat
pip install -r requirements.txt
run.bat
```

Buka http://localhost:8000. Saat pertama kali dijalankan, sistem otomatis membuat 2 job demo (1 job dengan masalah prepress, 1 job yang sudah approved).

Reset data: hapus `qc_prepress.db` dan folder `uploads/`.

## Masalah → Solusi

| Masalah | Solusi di aplikasi |
|---|---|
| Banyak item diisi manual | 14 dari 21 item checklist dinilai otomatis dari hasil baca file |
| Pemeriksaan butuh waktu | Preflight otomatis saat upload (ukuran, bleed, warna, spot, overprint, font, DPI) |
| Parameter bisa dibaca dari file | PDF/AI (PyMuPDF), PSD (header biner), CDR (container), PNG/JPG/TIFF (Pillow) |
| Perbandingan Asli/AW/OneUp/Layout visual | Pixel Diff otomatis + Side-by-Side, Overlay/Difference, Curtain, Blink; deteksi N-Up di Layout (template matching 0°/180°) |
| Riwayat sulit dilacak | Audit trail per aksi + versi file per tahap |
| Human error | Item yang FAIL otomatis tidak bisa diubah ke PASS tanpa alasan override |
| Siapa yang approve | Approval 3 tingkat (Operator → QC → Supervisor) dengan NIK, waktu, dan hash SHA-256 |
| Bagian file yang bermasalah | Pin koordinat di kanvas artwork; area hasil diff bisa langsung dijadikan pin |

Laporan/sertifikat QC dapat diekspor ke PDF.

## Struktur

- `main.py` — API FastAPI + data demo
- `backend/inspector.py` — preflight file & evaluasi checklist otomatis
- `backend/comparator.py` — pixel diff & verifikasi imposisi
- `backend/report_generator.py` — laporan PDF
- `backend/sample_generator.py` — pembuat file PDF demo
- `backend/database.py` — skema SQLite & template checklist
- `static/` — frontend (HTML/CSS/JS)
- `test_app.py` — tes otomatis (`python -m pytest test_app.py`)

## Catatan & batasan

- Login masih berupa pemilih personel (belum ada autentikasi); perlu diintegrasikan dengan SSO/LDAP sebelum dipakai produksi.
- File `.AI` harus disimpan dengan opsi "Create PDF Compatible File". File `.CDR` hanya dibaca preview/ukurannya; untuk preflight penuh, ekspor ke PDF/X.
- Deteksi overprint dan spot color berdasarkan struktur PDF; hasil WARN tetap perlu diverifikasi manual.
