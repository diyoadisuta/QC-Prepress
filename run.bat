@echo off
cd /d %~dp0
echo PrepressQC Studio berjalan di http://localhost:8000
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
