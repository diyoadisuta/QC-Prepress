// PrepressQC Studio Frontend Controller
const state = {
  users: [],
  activeUser: null,
  dashboard: null,
  jobs: [],
  selectedJobId: null,
  jobDetail: null,
  activeTab: 'inspector', // 'inspector' | 'comparator' | 'checklist' | 'approval' | 'audit'
  selectedStage: 'AW', // 'ORIGINAL' | 'AW' | 'ONEUP' | 'LAYOUT'
  uploadTargetStage: 'AW',
  activeSubFileId: {}, // { [stage]: fileId }
  canvasZoom: 1.0,
  canvasPanX: 0,
  canvasPanY: 0,
  showTrimGuide: true,
  pinModeActive: false,
  highlightedPinId: null,
  pendingPinCoords: { x_pct: 50, y_pct: 50, w_pct: 8, h_pct: 8, stage: 'AW' },
  activeSeparation: {
    fileId: null,
    plate: null,
    mode: 'ink',
    imageUrl: null,
    coveragePct: null,
    hexColor: null,
    loading: false,
  },

  // Comparator State
  compStageA: 'ORIGINAL',
  compStageB: 'AW',
  compViewMode: 'PIXEL_DIFF', // 'PIXEL_DIFF' | 'SIDE_BY_SIDE' | 'OVERLAY' | 'CURTAIN' | 'BLINK'
  overlayOpacity: 50,
  overlayBlend: 'normal',
  curtainPos: 50,
  blinkTimer: null,
  blinkShowB: false,
  blinkIntervalMs: 550,

  // Checklist Filter & Active Official Form
  checklistFilter: 'ALL',
  activeChecklistForm: 'AWAL', // 'AWAL' | 'ONEUP' | 'AKHIR'
  checklistSaveTimer: null,
};

const STAGE_META = {
  ORIGINAL: { label: 'File Asli', short: 'ORIG', desc: 'File Desain / Customer Asli' },
  AW: { label: 'Artwork (AW)', short: 'AW', desc: 'File Artwork Prepress Final' },
  ONEUP: { label: 'One-Up', short: '1UP', desc: '1-Up + Bleed + Garis Pisau' },
  LAYOUT: { label: 'Layout', short: 'LAYOUT', desc: 'Montase / Step & Repeat Cetak' },
};

document.addEventListener('DOMContentLoaded', async () => {
  await initAuthAndLoad();
});

async function initAuthAndLoad() {
  state.users = await apiFetch('/api/users');
  const savedRaw = localStorage.getItem('qc_prepress_auth');
  if (savedRaw) {
    try {
      const parsed = JSON.parse(savedRaw);
      const validUser = state.users.find(
        (u) => u.username === parsed?.user?.username
      );
      if (validUser) {
        state.activeUser = validUser;
        document.getElementById('loginScreen')?.classList.add('hidden');
        updateWelcomeName();
        await refreshAllData();
        return;
      }
    } catch (_) {
      localStorage.removeItem('qc_prepress_auth');
    }
  }
  // Not logged in yet -> show Login Screen
  document.getElementById('loginScreen')?.classList.remove('hidden');
}

function fillLoginQuick(username, password) {
  const uInput = document.getElementById('loginUsername');
  const pInput = document.getElementById('loginPassword');
  const errBox = document.getElementById('loginErrorBox');
  if (errBox) errBox.style.display = 'none';
  if (uInput) uInput.value = username;
  if (pInput) pInput.value = password;
}

async function handleLoginSubmit(event) {
  if (event) event.preventDefault();
  const username = (document.getElementById('loginUsername')?.value || '').trim();
  const password = (document.getElementById('loginPassword')?.value || '').trim();
  const errBox = document.getElementById('loginErrorBox');
  if (errBox) errBox.style.display = 'none';

  try {
    const res = await apiFetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    state.activeUser = res.user;
    localStorage.setItem('qc_prepress_auth', JSON.stringify(res));
    document.getElementById('loginScreen')?.classList.add('hidden');
    updateWelcomeName();
    showToast(
      `Selamat datang, <strong>${res.user.full_name}</strong> (${res.user.role_label})!`,
      'success'
    );
    await refreshAllData();
  } catch (err) {
    if (errBox) {
      errBox.textContent = err.message || 'Username atau Password salah.';
      errBox.style.display = 'block';
    }
  }
}

function handleLogout() {
  localStorage.removeItem('qc_prepress_auth');
  state.activeUser = null;
  const pInput = document.getElementById('loginPassword');
  if (pInput) pInput.value = '';
  document.getElementById('loginScreen')?.classList.remove('hidden');
  showToast('Anda telah keluar dari sesi.', 'info');
}

function showToast(msg, type = 'info') {
  const container = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = 'toast';
  if (type === 'success') el.style.borderLeftColor = '#10b981';
  if (type === 'error') el.style.borderLeftColor = '#ef4444';
  if (type === 'warn') el.style.borderLeftColor = '#f59e0b';
  el.innerHTML = msg;
  container.appendChild(el);
  setTimeout(() => {
    el.remove();
  }, 4200);
}

async function apiFetch(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let errText = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      errText = data.detail || JSON.stringify(data);
    } catch (_) {
      errText = await res.text();
    }
    throw new Error(errText);
  }
  return res.json();
}

function updateWelcomeName() {
  const el = document.getElementById('welcomeName');
  const badge = document.getElementById('loggedInUserBadge');
  if (el && state.activeUser) {
    el.textContent = state.activeUser.full_name;
  }
  if (badge && state.activeUser) {
    badge.textContent = `${state.activeUser.full_name} (${state.activeUser.role_label})`;
  }
}

async function refreshAllData(keepJobId = true) {
  const [dash, jobs] = await Promise.all([
    apiFetch('/api/dashboard'),
    apiFetch('/api/jobs'),
  ]);
  state.dashboard = dash;
  state.jobs = jobs;
  renderDashboardKpis();
  renderJobList();

  if (jobs.length > 0) {
    const stillExists = jobs.some((j) => j.id === state.selectedJobId);
    const targetId =
      keepJobId && state.selectedJobId && stillExists
        ? state.selectedJobId
        : jobs[0].id;
    await selectJob(targetId);
  } else {
    state.selectedJobId = null;
    state.jobDetail = null;
    const ws = document.getElementById('mainWorkspace');
    if (ws) {
      ws.innerHTML = `
        <div class="panel-card" style="padding: 60px 24px; text-align: center;">
          <h3 style="margin-bottom:8px; color:var(--text-strong);">Belum ada Job Pemeriksaan Prepress</h3>
          <p style="color:var(--text-secondary); margin-bottom:16px;">Klik tombol di bawah untuk membuat Job baru atau men-generate sampel pemeriksaan otomatis.</p>
          <button class="btn btn-primary" onclick="openCreateJobModal()">+ Buat Job Baru</button>
        </div>
      `;
    }
  }
}

function renderDashboardKpis() {
  const d = state.dashboard;
  if (!d) return;
  document.getElementById('kpiTotalJobs').textContent = d.total_jobs;
  document.getElementById('kpiAutoChecks').textContent = d.auto_checks_run;
  document.getElementById('kpiOpenPins').textContent = d.open_pins;
  document.getElementById('kpiRevisionJobs').textContent = d.revision_jobs;
  document.getElementById('kpiApprovedJobs').textContent = d.approved_jobs;
}

function statusBadgeHtml(status) {
  const s = (status || '').toUpperCase();
  if (s === 'PASS' || s === 'APPROVED' || s === 'RESOLVED' || s === 'VERIFIED') {
    return `<span class="badge badge-pass">${s}</span>`;
  }
  if (s === 'FAIL' || s === 'REJECTED' || s === 'REVISION_REQUIRED' || s === 'CRITICAL' || s === 'OPEN') {
    return `<span class="badge badge-fail">${s}</span>`;
  }
  if (s === 'WARN' || s.startsWith('WAITING') || s === 'MAJOR' || s === 'APPROVED_WITH_NOTE') {
    return `<span class="badge badge-warn">${s}</span>`;
  }
  return `<span class="badge badge-info">${s || 'PENDING'}</span>`;
}

function renderJobList() {
  const q = (document.getElementById('jobSearchInput')?.value || '').toLowerCase().trim();
  const filtered = state.jobs.filter((j) => {
    if (!q) return true;
    return (
      j.job_number.toLowerCase().includes(q) ||
      j.product_name.toLowerCase().includes(q) ||
      j.customer_name.toLowerCase().includes(q) ||
      j.item_code.toLowerCase().includes(q)
    );
  });

  document.getElementById('jobCountBadge').textContent = `${filtered.length} Job`;
  const container = document.getElementById('jobListContainer');

  container.innerHTML = filtered
    .map((j) => {
      const isAct = j.id === state.selectedJobId ? 'active' : '';
      const uploaded = j.uploaded_stages || [];
      const stagePills = ['ORIGINAL', 'AW', 'ONEUP', 'LAYOUT']
        .map((st) => {
          const has = uploaded.includes(st);
          return `<span class="stage-pill ${has ? 'uploaded' : ''}">${STAGE_META[st].short} ${has ? '✓' : ''}</span>`;
        })
        .join('');

      return `
        <div class="job-card ${isAct}" onclick="selectJob(${j.id})">
          <div class="job-card-top">
            <span class="job-num">${j.job_number} (R0${j.revision_no})</span>
            <div style="display:flex; align-items:center; gap:4px;">
              ${statusBadgeHtml(j.status)}
              <button
                class="btn btn-sm btn-outline"
                style="padding:2px 7px; font-size:11px;"
                title="Edit Job ${j.job_number}"
                onclick="openEditJobModal(event, ${j.id})"
              >&#9998;</button>
              <button
                class="btn btn-sm btn-danger"
                style="padding:2px 7px; font-size:11px;"
                title="Hapus Job ${j.job_number}"
                onclick="confirmDeleteJob(event, ${j.id}, '${j.job_number.replace(/'/g, "\\'")}')"
              >&#128465;</button>
            </div>
          </div>
          <div class="job-title">${j.product_name}</div>
          <div class="job-cust">${j.customer_name} • <strong>${j.item_code}</strong></div>
          <div class="stage-pills">${stagePills}</div>
          <div class="progress-row">
            <span>Checklist: ${j.checklist_forms_signed ?? 0}/3 Form (Awal &bull; OneUp &bull; Akhir)</span>
            ${
              j.open_annotations > 0
                ? `<span style="color:var(--danger); font-weight:700;">&#9888; ${j.open_annotations} Pin Masalah</span>`
                : `<span style="color:var(--success);">0 Masalah</span>`
            }
          </div>
          <div class="progress-bar">
            <div class="progress-fill" style="width: ${Math.round(((j.checklist_forms_signed ?? 0) / 3) * 100)}%;"></div>
          </div>
        </div>
      `;
    })
    .join('');
}

async function selectJob(jobId) {
  stopBlinkTimer();
  state.selectedJobId = jobId;
  renderJobList();
  state.jobDetail = await apiFetch(`/api/jobs/${jobId}`);
  renderWorkspace();
}

function switchTab(tabName) {
  stopBlinkTimer();
  state.activeTab = tabName;
  renderWorkspace();
}

function renderWorkspace() {
  const detail = state.jobDetail;
  if (!detail) return;
  const job = detail.job;
  const checklist = detail.checklist || [];
  const annotations = detail.annotations || [];
  const comparisons = detail.comparisons || [];
  const approvals = detail.approvals || [];
  const auditLogs = detail.audit_logs || [];

  const openPinsCount = annotations.filter((a) => a.status === 'OPEN').length;
  const failCheckCount = checklist.filter(
    (c) => c.manual_status === 'FAIL' || c.auto_status === 'FAIL'
  ).length;
  const passCheckCount = checklist.filter((c) => c.manual_status === 'PASS').length;
  const autoItemsCount = checklist.filter((c) => c.is_auto && c.auto_status !== 'PENDING').length;
  const uploadedStageCount = Object.keys(detail.stage_files || {}).length;
  const pctPass = Math.round((passCheckCount / Math.max(checklist.length, 1)) * 100);

  const formsMap = detail.checklist_forms || {};
  const signedFormsCount = ['AWAL', 'ONEUP', 'AKHIR'].filter(
    (k) => formsMap[k]?.desainer_name || formsMap[k]?.reviewer_name
  ).length;

  document.querySelectorAll('#topPillNav .top-nav-item').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === state.activeTab);
  });
  const elSt = document.getElementById('heroPillStages');
  const elAu = document.getElementById('heroPillAuto');
  const elCh = document.getElementById('heroPillChecklist');
  const elAp = document.getElementById('heroPillApproval');
  if (elSt) elSt.textContent = `${uploadedStageCount}/4 Tahap`;
  if (elAu) elAu.textContent = `${autoItemsCount} Auto-Read`;
  if (elCh) elCh.textContent = `3 Form Checklist (${signedFormsCount}/3 TTD)`;
  if (elAp) elAp.textContent = `${approvals.length}/3 Approval`;

  const ws = document.getElementById('mainWorkspace');
  ws.innerHTML = `
    <!-- Job Header Banner -->
    <div class="job-banner">
      <div>
        <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
          <span style="font-family:monospace; font-size:13.5px; font-weight:800; color:var(--accent);">${job.job_number}</span>
          <h2 style="font-size:17px; font-weight:800; color:var(--text-strong);">${job.product_name}</h2>
          ${statusBadgeHtml(job.status)}
          <span class="badge badge-info">Revisi R0${job.revision_no}</span>
        </div>
        <div style="font-size:12.5px; color:var(--text-secondary); margin-top:3px;">
          Customer: <strong>${job.customer_name}</strong> &nbsp;|&nbsp;
          Kode Item: <strong>${job.item_code}</strong> &nbsp;|&nbsp;
          Kemasan: <strong>${job.package_type}</strong> (${job.print_method})
        </div>
        <div class="job-specs-chips">
          <span class="spec-chip">Target Trim: <strong>${job.target_width_mm} &times; ${job.target_height_mm} mm</strong></span>
          <span class="spec-chip">Wajib Bleed: <strong>&ge; ${job.required_bleed_mm} mm</strong></span>
          <span class="spec-chip">Resolusi Min: <strong>&ge; ${job.min_dpi} DPI</strong></span>
          <span class="spec-chip">Spesifikasi Warna: <strong>${job.color_spec}</strong></span>
          <span class="spec-chip">Target Imposisi Layout: <strong>${job.expected_nup}-Up (${job.layout_width_mm}&times;${job.layout_height_mm} mm)</strong></span>
        </div>
      </div>

      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
        <button class="btn btn-outline" onclick="openEditJobModal(event, ${job.id})">
          <span>&#9998; Edit Job</span>
        </button>
        <button class="btn btn-danger" onclick="confirmDeleteJob(event, ${job.id}, '${job.job_number.replace(/'/g, "\\'")}')">
          <span>&#128465; Hapus</span>
        </button>
        <a class="btn btn-outline" href="/api/jobs/${job.id}/report.pdf" target="_blank">
          <span>&#128196; Laporan PDF</span>
        </a>
        <button class="btn btn-primary" onclick="switchTab('approval')">
          <span>&#10004; Approval ${approvals.length}/3</span>
        </button>
      </div>
    </div>

    <!-- Workspace Navigation Tabs -->
    <div class="workspace-tabs">
      <button class="tab-btn ${state.activeTab === 'inspector' ? 'active' : ''}" onclick="switchTab('inspector')">
        <span>Inspeksi File</span>
        <span class="tab-count" style="${openPinsCount > 0 ? 'background:var(--danger-soft); color:var(--danger);' : ''}">
          ${openPinsCount} Pin
        </span>
      </button>
      <button class="tab-btn ${state.activeTab === 'comparator' ? 'active' : ''}" onclick="switchTab('comparator')">
        <span>Perbandingan Visual</span>
        <span class="tab-count">${comparisons.length} Diff</span>
      </button>
      <button class="tab-btn ${state.activeTab === 'checklist' ? 'active' : ''}" onclick="switchTab('checklist')">
        <span>Checklist (Awal &bull; OneUp &bull; Akhir)</span>
        <span class="tab-count">
          ${signedFormsCount}/3 Form
        </span>
      </button>
      <button class="tab-btn ${state.activeTab === 'approval' ? 'active' : ''}" onclick="switchTab('approval')">
        <span>Approval</span>
        <span class="tab-count">${approvals.length}/3</span>
      </button>
      <button class="tab-btn ${state.activeTab === 'audit' ? 'active' : ''}" onclick="switchTab('audit')">
        <span>Riwayat</span>
        <span class="tab-count">${auditLogs.length} Log</span>
      </button>
    </div>

    <!-- Active Tab Content -->
    <div class="tab-content" id="activeTabContent"></div>
  `;

  const container = document.getElementById('activeTabContent');
  if (state.activeTab === 'inspector') {
    renderInspectorTab(container);
  } else if (state.activeTab === 'comparator') {
    renderComparatorTab(container);
  } else if (state.activeTab === 'checklist') {
    renderChecklistTab(container);
  } else if (state.activeTab === 'approval') {
    renderApprovalTab(container);
  } else if (state.activeTab === 'audit') {
    renderAuditTab(container);
  }
}

/* ============================================================================
 * TAB 1: FILE INSPECTOR, AUTO-PARAMETERS & VISUAL PINNING ON CANVAS
 * ============================================================================ */
function renderInspectorTab(container) {
  const detail = state.jobDetail;
  const stageFiles = detail.stage_files || {};
  const stageAllFiles = detail.stage_all_files || {};
  const annotations = detail.annotations || [];

  const stageBoxesHtml = ['ORIGINAL', 'AW', 'ONEUP', 'LAYOUT']
    .map((st) => {
      const allForStage = stageAllFiles[st] || (stageFiles[st] ? [stageFiles[st]] : []);
      const f =
        allForStage.find((item) => item.id === (state.activeSubFileId && state.activeSubFileId[st])) ||
        stageFiles[st] ||
        (allForStage.length > 0 ? allForStage[0] : null);
      const isSel = state.selectedStage === st ? 'active' : '';
      const stageAnnotations = annotations.filter((a) => a.stage === st && a.status === 'OPEN');
      if (!f) {
        return `
          <div class="stage-box ${isSel}" onclick="selectStage('${st}')">
            <div class="stage-box-header">
              <span class="stage-name">${STAGE_META[st].label}</span>
              <span class="badge badge-pending">BELUM ADA FILE</span>
            </div>
            <div class="stage-meta">
              <span>${STAGE_META[st].desc}</span>
              <span style="color:var(--text-muted);">Mendukung .PDF, .AI, .PSD, .CDR, .PNG, .TIFF</span>
            </div>
            <button class="btn btn-sm btn-primary" onclick="triggerStageUpload(event, '${st}')">
              &#8682; Unggah File ${STAGE_META[st].short}
            </button>
          </div>
        `;
      }
      const insp = f.inspection || {};
      const tw = insp.trim_box_mm?.width ?? '-';
      const th = insp.trim_box_mm?.height ?? '-';
      const bl = insp.detected_bleed_mm ?? 0;
      const cs = (insp.color_spaces || [])
        .map((c) => (c === 'Spot/Separation' ? 'Spot' : c))
        .join(' + ');

      const fileCountBadge =
        allForStage.length > 1
          ? `<span class="badge" style="background:#e0e7ff; color:#3730a3; font-size:10.5px; padding:2px 6px;">${allForStage.length} File</span>`
          : '';

      const fileSelectorHtml =
        allForStage.length > 1
          ? `
            <div style="margin:4px 0;" onclick="event.stopPropagation()">
              <select class="form-control" style="font-size:11px; padding:2px 6px; height:26px; border-color:var(--border);" onchange="switchActiveSubFile('${st}', this.value)">
                ${allForStage
                  .map(
                    (sub) =>
                      `<option value="${sub.id}" ${sub.id === f.id ? 'selected' : ''}>${sub.filename} (v${sub.version})</option>`
                  )
                  .join('')}
              </select>
            </div>
          `
          : `
            <div style="font-size:12px; font-weight:600; color:var(--accent); word-break:break-all;">
              ${f.filename} <span style="color:var(--text-muted);">(v${f.version})</span>
            </div>
          `;

      const uploadBtnLabel =
        st === 'ONEUP' || allForStage.length > 1
          ? '&#8682; + Tambah / Revisi'
          : '&#8682; Upload Revisi';

      return `
        <div class="stage-box ${isSel}" onclick="selectStage('${st}')">
          <div class="stage-box-header">
            <div style="display:flex; align-items:center; gap:6px;">
              <span class="stage-name">${STAGE_META[st].label}</span>
              ${fileCountBadge}
            </div>
            ${statusBadgeHtml(f.auto_status)}
          </div>
          ${fileSelectorHtml}
          <div class="stage-meta">
            <div style="display:flex; justify-content:space-between; gap:8px;"><span>Ukuran Trim</span><strong>${tw} &times; ${th} mm</strong></div>
            <div style="display:flex; justify-content:space-between; gap:8px;"><span>Area Bleed</span><strong>${bl} mm</strong></div>
            <div style="display:flex; justify-content:space-between; gap:8px;"><span>Mode Warna</span><strong style="text-align:right; ${insp.has_rgb ? 'color:var(--danger);' : 'color:var(--success);'}">${cs} (${insp.spot_color_count || 0} Spot)</strong></div>
            <div style="display:flex; justify-content:space-between; gap:8px;"><span>DPI &amp; Temuan</span><strong>${insp.min_dpi ?? 'Vector'} DPI &bull; <span style="${stageAnnotations.length > 0 ? 'color:var(--danger);' : 'color:var(--success);'}">${stageAnnotations.length} Pin</span></strong></div>
          </div>
          <div style="display:flex; gap:6px; margin-top:6px;">
            <button class="btn btn-sm btn-outline" style="flex:1;" onclick="triggerStageUpload(event, '${st}')" title="Unggah file baru atau revisi">
              ${uploadBtnLabel}
            </button>
            <button class="btn btn-sm btn-outline" style="color:var(--danger); border-color:#fca5a5; background:#fff1f2; padding:0 10px;" onclick="confirmDeleteStageFile(event, ${f.id}, '${st}')" title="Hapus file ini">&#128465; Hapus</button>
          </div>
        </div>
      `;
    })
    .join('');

  const allCurrentStageFiles =
    stageAllFiles[state.selectedStage] ||
    (stageFiles[state.selectedStage] ? [stageFiles[state.selectedStage]] : []);
  const activeFile =
    allCurrentStageFiles.find(
      (item) => item.id === (state.activeSubFileId && state.activeSubFileId[state.selectedStage])
    ) ||
    stageFiles[state.selectedStage] ||
    (allCurrentStageFiles.length > 0 ? allCurrentStageFiles[0] : null);

  const stagePins = annotations.filter((a) => a.stage === state.selectedStage);

  let canvasAreaHtml = '';
  let rightParamsHtml = '';

  if (!activeFile) {
    canvasAreaHtml = `
      <div class="canvas-panel" style="padding: 60px 20px; text-align:center;">
        <h3 style="margin-bottom:8px;">Belum ada file untuk tahap ${STAGE_META[state.selectedStage].label}</h3>
        <p style="color:var(--text-muted); margin-bottom:16px;">
          Silakan unggah file (.PDF, .AI, .PSD, .CDR, .PNG, .JPG, .TIFF) untuk membaca parameter dimensi, warna, font, dan resolusi secara otomatis.
        </p>
        <button class="btn btn-primary" onclick="triggerStageUpload(event, '${state.selectedStage}')">
          &#8682; Pilih &amp; Unggah File ${state.selectedStage}
        </button>
      </div>
    `;
    rightParamsHtml = `<div class="panel-card"><div class="panel-card-body">Menunggu file diunggah...</div></div>`;
  } else {
    const insp = activeFile.inspection || {};
    const mediaW = insp.media_box_mm?.width || 120;
    const mediaH = insp.media_box_mm?.height || 180;
    const bleedMm = insp.detected_bleed_mm || 0;

    // Trim guide inset % if bleed > 0
    const insetLeftPct = mediaW > 0 ? (bleedMm / mediaW) * 100 : 0;
    const insetTopPct = mediaH > 0 ? (bleedMm / mediaH) * 100 : 0;

    const pinsOverlayHtml = stagePins
      .map((p) => {
        const isResolved = p.status === 'RESOLVED' || p.status === 'VERIFIED';
        const isHigh = state.highlightedPinId === p.id ? 'highlighted' : '';
        const boxLeft = Math.max(0, p.x_pct - (p.w_pct || 8) / 2);
        const boxTop = Math.max(0, p.y_pct - (p.h_pct || 8) / 2);
        return `
          <div
            class="canvas-pin-box ${isResolved ? 'resolved' : ''}"
            style="left:${boxLeft}%; top:${boxTop}%; width:${p.w_pct || 8}%; height:${p.h_pct || 8}%;"
          ></div>
          <div
            class="canvas-pin ${isResolved ? 'resolved' : ''} ${isHigh}"
            style="left:${p.x_pct}%; top:${p.y_pct}%;"
            title="Pin #${p.pin_number}: ${p.title} (${p.severity})"
            onclick="focusPin(event, ${p.id})"
          >
            ${p.pin_number}
          </div>
        `;
      })
      .join('');

    const activeSep =
      state.activeSeparation && state.activeSeparation.fileId === activeFile.id
        ? state.activeSeparation
        : { fileId: activeFile.id, plate: null, mode: 'ink', imageUrl: null, coveragePct: null, hexColor: null, loading: false };
    const isSepActive = Boolean(activeSep.plate && activeSep.imageUrl);
    const canvasImgSrc = isSepActive
      ? `${activeSep.imageUrl}${activeSep.cacheStamp ? `?t=${activeSep.cacheStamp}` : ''}`
      : `${activeFile.preview_path}?v=${activeFile.version || 1}`;

    const cmykPlates = [
      { name: 'Cyan', color: '#00A8E8' },
      { name: 'Magenta', color: '#E6007E' },
      { name: 'Yellow', color: '#D9A406' },
      { name: 'Black', color: '#18181B' },
    ];
    const cmykButtonsHtml = cmykPlates
      .map((pl) => {
        const isSel = activeSep.plate && activeSep.plate.toLowerCase() === pl.name.toLowerCase();
        return `<button
          type="button"
          class="badge"
          style="margin:2px; cursor:pointer; border:1.5px solid ${isSel ? pl.color : 'var(--border)'}; background:${isSel ? pl.color : '#f8fafc'}; color:${isSel ? '#ffffff' : 'var(--text-strong)'}; padding:4px 9px; font-size:11px; font-weight:700; display:inline-flex; align-items:center; gap:5px; border-radius:999px; transition:all 0.15s;"
          onclick="toggleSeparationPlate(${activeFile.id}, '${pl.name}')"
          title="Klik untuk melihat pelat separasi proses ${pl.name}"
        >
          <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:${isSel ? '#fff' : pl.color};"></span>
          ${pl.name}
        </button>`;
      })
      .join(' ');

    const spotsList =
      (insp.spot_colors || []).length > 0
        ? insp.spot_colors
            .map((s) => {
              const isSel = activeSep.plate && activeSep.plate.toLowerCase() === String(s).toLowerCase();
              const activeBg = activeSep.hexColor || '#4f46e5';
              return `<button
                type="button"
                class="badge badge-auto"
                style="margin:2px; cursor:pointer; border:1.5px solid ${isSel ? activeBg : '#c7d2fe'}; background:${isSel ? activeBg : '#eef2ff'}; color:${isSel ? '#ffffff' : 'var(--accent)'}; padding:4px 10px; font-size:11.5px; font-weight:700; border-radius:999px; transition:all 0.15s; box-shadow:${isSel ? '0 2px 6px rgba(79,70,229,0.28)' : 'none'};"
                onclick="toggleSeparationPlate(${activeFile.id}, '${escapeAttr(s)}')"
                title="Klik untuk menampilkan separasi warna '${escapeAttr(s)}' di kanvas"
              >
                ${isSel ? '&#10004; ' : '&#128065; '}${escapeHtml(s)}
              </button>`;
            })
            .join(' ')
        : `<span style="color:var(--text-muted); font-size:11.5px;">Tidak ada Spot Color (Process Only)</span>`;

    canvasAreaHtml = `
      <div class="canvas-panel">
        <div class="canvas-toolbar">
          <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
            <strong style="color:var(--text-strong);">Kanvas Inspeksi: ${STAGE_META[state.selectedStage].short}</strong>
            ${
              allCurrentStageFiles.length > 1
                ? `<select class="form-control" style="font-size:11.5px; padding:2px 8px; height:28px; width:auto; max-width:260px;" onchange="switchActiveSubFile('${state.selectedStage}', this.value)">
                    ${allCurrentStageFiles
                      .map(
                        (sub) =>
                          `<option value="${sub.id}" ${sub.id === activeFile.id ? 'selected' : ''}>${sub.filename} (v${sub.version})</option>`
                      )
                      .join('')}
                   </select>`
                : `<span class="badge badge-info">${activeFile.filename} (v${activeFile.version})</span>`
            }
            <button
              class="btn btn-sm ${state.pinModeActive ? 'btn-danger' : 'btn-outline'}"
              onclick="togglePinMode()"
            >
              ${state.pinModeActive ? '&#10006; Batal Mode Pin (Klik Kanvas Aktif)' : '&#128204; + Tandai Masalah di Kanvas'}
            </button>
            <button class="btn btn-sm btn-outline" style="color:var(--danger); border-color:#fca5a5; background:#fff1f2;" onclick="confirmDeleteStageFile(event, ${activeFile.id}, '${state.selectedStage}')" title="Hapus file aktif ini">&#128465; Hapus</button>
          </div>
          <div style="display:flex; align-items:center; gap:6px;">
            <label style="font-size:11.5px; display:flex; align-items:center; gap:4px; cursor:pointer; margin-right:6px;">
              <input type="checkbox" ${state.showTrimGuide ? 'checked' : ''} onchange="toggleTrimGuide(this.checked)" />
              <span>Garis Trim/Bleed</span>
            </label>
            <button class="btn btn-sm btn-outline" onclick="adjustZoom(-0.25)" title="Zoom Out">&#8722;</button>
            <button class="btn btn-sm btn-outline" id="canvasZoomLabel" onclick="resetZoom()" title="Klik untuk Reset Zoom &amp; Posisi (100%)">${Math.round(state.canvasZoom * 100)}%</button>
            <button class="btn btn-sm btn-outline" onclick="adjustZoom(0.25)" title="Zoom In">&#43;</button>
            <button class="btn btn-sm btn-outline" onclick="resetZoom()" title="Reset posisi tengah &amp; 100%">Reset</button>
          </div>
        </div>

        <!-- Always-Visible Interactive Separation Plate Selector Bar -->
        <div style="background:#f8fafc; border-bottom:1px solid var(--border); padding:6px 14px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:6px;">
          <div style="display:flex; align-items:center; flex-wrap:wrap; gap:4px;">
            <span style="font-size:11.5px; font-weight:700; color:var(--text-strong); margin-right:4px;">&#127912; Klik Pelat Separasi:</span>
            <button
              type="button"
              class="badge"
              style="margin:2px; cursor:pointer; border:1.5px solid ${!isSepActive ? 'var(--accent)' : 'var(--border)'}; background:${!isSepActive ? 'var(--accent)' : '#ffffff'}; color:${!isSepActive ? '#ffffff' : 'var(--text-strong)'}; padding:4px 10px; font-size:11px; font-weight:700; border-radius:999px;"
              onclick="clearSeparationPlate()"
            >
              ${!isSepActive ? '&#10004; ' : ''}Semua Warna
            </button>
            ${(insp.spot_colors || []).length > 0 ? spotsList : ''}
            <span style="color:var(--border); margin:0 2px;">|</span>
            ${cmykButtonsHtml}
          </div>
          ${
            isSepActive
              ? `<div style="display:flex; align-items:center; gap:6px; font-size:11.5px;">
                  ${activeSep.coveragePct !== null ? `<span class="badge badge-info">Area Tinta: <strong>${activeSep.coveragePct}%</strong></span>` : ''}
                  <button type="button" class="btn btn-sm ${activeSep.mode !== 'black' ? 'btn-primary' : 'btn-outline'}" style="padding:2px 8px; font-size:11px;" onclick="switchSeparationMode(${activeFile.id}, 'ink')">&#127912; Warna Tinta</button>
                  <button type="button" class="btn btn-sm ${activeSep.mode === 'black' ? 'btn-primary' : 'btn-outline'}" style="padding:2px 8px; font-size:11px;" onclick="switchSeparationMode(${activeFile.id}, 'black')">&#9899; Film Hitam (CTP)</button>
                 </div>`
              : ''
          }
        </div>

        ${
          state.pinModeActive
            ? `<div style="background:var(--danger-soft); border-bottom:1px solid #f6caca; padding:7px 14px; font-size:12px; color:var(--danger);">
                <strong>Mode Tandai Masalah Aktif:</strong> Klik tepat pada bagian gambar/teks artwork di bawah yang bermasalah untuk menaruh Pin Koordinat &amp; Catatan Revisi.
               </div>`
            : ''
        }

        <div
          class="canvas-viewport ${state.pinModeActive ? 'pin-cursor' : ''}"
          id="inspectorCanvasViewport"
          onmousedown="onCanvasMouseDown(event)"
          onwheel="onCanvasWheel(event)"
          ondblclick="onCanvasDblClick(event)"
          title="${state.pinModeActive ? 'Klik pada artwork untuk menaruh Pin' : 'Drag untuk geser preview • Scroll mouse untuk Zoom In/Out • Double-click untuk Reset'}"
        >
          <div
            class="artwork-stage-wrapper ${state.pinModeActive ? 'pin-mode-active' : ''}"
            id="inspectorArtworkWrapper"
            style="transform: translate(${state.canvasPanX}px, ${state.canvasPanY}px) scale(${state.canvasZoom});"
            onclick="onArtworkCanvasClick(event)"
          >
            <img src="${canvasImgSrc}" alt="Preview ${state.selectedStage}" draggable="false" />
            ${
              state.showTrimGuide && bleedMm > 0
                ? `<div class="trim-guide-overlay" style="left:${insetLeftPct}%; top:${insetTopPct}%; right:${insetLeftPct}%; bottom:${insetTopPct}%;" title="Garis Potong TrimBox (Bleed ${bleedMm}mm)"></div>`
                : ''
            }
            ${pinsOverlayHtml}
          </div>
        </div>
      </div>
    `;

    // Build Right Side Auto-Parameter Readout + Annotations List
    const fontsSummary = insp.fonts_outlined
      ? `<span style="color:var(--success);">100% Outlined / Kurva (0 Font Aktif)</span>`
      : insp.all_fonts_safe
      ? `<span style="color:var(--success);">${insp.font_count} Font (100% Embedded)</span>`
      : `<span style="color:var(--danger);">${insp.unembedded_fonts?.length} Font TIDAK Embedded!</span>`;

    const pinItemsHtml =
      stagePins.length === 0
        ? `<div style="font-size:12px; color:var(--text-muted); padding:8px 0;">
            Belum ada Pin Masalah pada tahap <strong>${state.selectedStage}</strong>. Klik tombol <strong>"+ Tandai Masalah di Kanvas"</strong> untuk menandai bagian file yang bermasalah.
           </div>`
        : stagePins
            .map((p) => {
              const isOpen = p.status === 'OPEN';
              return `
                <div
                  style="background:var(--bg-card); border:1px solid ${state.highlightedPinId === p.id ? 'var(--accent)' : 'var(--border)'}; border-left:4px solid ${isOpen ? 'var(--danger)' : 'var(--success)'}; border-radius:6px; padding:10px; margin-bottom:8px; cursor:pointer;"
                  onclick="focusPin(event, ${p.id})"
                >
                  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                    <strong style="color:var(--text-strong); font-size:12.5px;">Pin #${p.pin_number}: ${p.title}</strong>
                    ${statusBadgeHtml(p.status)}
                  </div>
                  <div style="font-size:11px; color:var(--text-muted); margin-bottom:5px;">
                    Lokasi: <strong>X:${p.x_pct}%, Y:${p.y_pct}%</strong> &bull;
                    Severity: <strong>${p.severity}</strong> &bull;
                    Kategori: <strong>${p.category}</strong>
                    ${p.checklist_code ? `&bull; Checklist: <strong>${p.checklist_code}</strong>` : ''}
                  </div>
                  <div style="font-size:12px; color:var(--text-secondary); margin-bottom:6px;">
                    ${p.comment}
                  </div>
                  <div style="display:flex; justify-content:space-between; align-items:center; font-size:11px; color:var(--text-muted);">
                    <span>Ditandai oleh: <strong>${p.created_by}</strong> (${p.created_role})</span>
                    <div style="display:flex; gap:5px;">
                      ${
                        isOpen
                          ? `<button class="btn btn-sm btn-success" onclick="resolvePin(event, ${p.id}, 'RESOLVED')">&#10004; Tandai Selesai</button>`
                          : `<button class="btn btn-sm btn-outline" onclick="resolvePin(event, ${p.id}, 'OPEN')">Buka Kembali</button>`
                      }
                      <button class="btn btn-sm btn-outline" onclick="deletePin(event, ${p.id})">&#128465;</button>
                    </div>
                  </div>
                </div>
              `;
            })
            .join('');

    rightParamsHtml = `
      <div class="side-stack">
        <!-- Pin Masalah Card -->
        <div class="panel-card">
          <div class="panel-card-header">
            <span>Pin Masalah · ${state.selectedStage} (${stagePins.length})</span>
            <button class="btn btn-sm btn-danger" onclick="togglePinMode()">+ Tambah Pin</button>
          </div>
          <div class="panel-card-body" style="max-height: 290px; overflow-y: auto;">
            ${pinItemsHtml}
          </div>
        </div>

        <!-- Auto-Extracted File Parameters Card -->
        <div class="panel-card">
          <div class="panel-card-header">
            <span>Parameter Otomatis (${activeFile.file_format})</span>
            <span class="badge badge-auto">AUTO-READ</span>
          </div>
          <div class="panel-card-body">
            <div class="param-grid">
              <div class="param-item">
                <div class="param-label">Ukuran TrimBox (Bersih)</div>
                <div class="param-val">${insp.trim_box_mm?.width} &times; ${insp.trim_box_mm?.height} mm</div>
              </div>
              <div class="param-item">
                <div class="param-label">Ukuran MediaBox (Kertas)</div>
                <div class="param-val">${insp.media_box_mm?.width} &times; ${insp.media_box_mm?.height} mm</div>
              </div>
              <div class="param-item">
                <div class="param-label">Area Bleed Terdeteksi</div>
                <div class="param-val" style="color:${insp.detected_bleed_mm >= detail.job.required_bleed_mm ? 'var(--success)' : 'var(--warning)'};">
                  ${insp.detected_bleed_mm} mm
                </div>
              </div>
              <div class="param-item">
                <div class="param-label">Ruang Warna (Color Space)</div>
                <div class="param-val" style="color:${insp.has_rgb ? 'var(--danger)' : 'var(--success)'};">
                  ${(insp.color_spaces || []).join(', ')}
                </div>
              </div>
              <div class="param-item">
                <div class="param-label">Resolusi Gambar (DPI Efektif)</div>
                <div class="param-val" style="color:${insp.min_dpi && insp.min_dpi < detail.job.min_dpi ? 'var(--danger)' : 'var(--success)'};">
                  ${insp.min_dpi ? `Min ${insp.min_dpi} / Avg ${insp.avg_dpi} DPI` : '100% Vector'}
                </div>
              </div>
              <div class="param-item">
                <div class="param-label">Status Overprint &amp; Halaman</div>
                <div class="param-val">
                  ${insp.has_overprint ? 'OP: Aktif ✓' : 'OP: Standar'} (${insp.page_count} Hal)
                </div>
              </div>
            </div>

            <div style="margin-top:10px; background:var(--bg-card); border:1px solid var(--border); padding:9px 11px; border-radius:6px;">
              <div style="display:flex; justify-content:space-between; align-items:center; gap:6px; flex-wrap:wrap;">
                <div class="param-label">Separasi Spot Color / Pantone / Garis Pisau (${insp.spot_color_count || 0}) &bull; <span style="color:var(--accent); font-weight:700;">Klik Warna untuk Preview Pelat</span>:</div>
                ${
                  isSepActive
                    ? `<button type="button" class="btn btn-sm btn-outline" style="padding:1px 7px; font-size:10.5px;" onclick="clearSeparationPlate()">&#8634; Tampilkan Semua Warna</button>`
                    : ''
                }
              </div>
              <div style="margin-top:5px; display:flex; flex-wrap:wrap; align-items:center; gap:2px;">
                ${spotsList}
              </div>
              <div style="margin-top:7px; padding-top:6px; border-top:1px dashed var(--border); display:flex; align-items:center; flex-wrap:wrap; gap:3px;">
                <span style="font-size:11px; color:var(--text-muted); margin-right:4px; font-weight:600;">Pelat Proses CMYK:</span>
                ${cmykButtonsHtml}
              </div>
            </div>

            <div style="margin-top:8px; background:var(--bg-card); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
              <div class="param-label">Status Tipografi &amp; Font:</div>
              <div style="font-size:12px; font-weight:700; margin-top:2px;">
                ${fontsSummary}
                ${insp.min_font_size_pt ? ` &bull; Ukuran Teks Terkecil: <strong>${insp.min_font_size_pt} pt</strong>` : ''}
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  container.innerHTML = `
    <div class="stages-grid">${stageBoxesHtml}</div>
    <div class="inspector-split">
      ${canvasAreaHtml}
      ${rightParamsHtml}
    </div>
  `;
}

function escapeHtml(val) {
  return String(val ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escapeAttr(val) {
  return String(val ?? '')
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '&quot;');
}

async function toggleSeparationPlate(fileId, plateName) {
  if (!state.selectedJobId || !fileId || !plateName) return;
  const cur = state.activeSeparation || {};
  if (cur.fileId === fileId && cur.plate && cur.plate.toLowerCase() === plateName.toLowerCase()) {
    clearSeparationPlate();
    return;
  }
  const mode = cur.mode || 'ink';
  await loadSeparationPlate(fileId, plateName, mode);
}

async function switchSeparationMode(fileId, newMode) {
  const cur = state.activeSeparation || {};
  if (!cur.plate || !fileId) return;
  await loadSeparationPlate(fileId, cur.plate, newMode);
}

async function loadSeparationPlate(fileId, plateName, mode = 'ink') {
  try {
    showToast(`Merender pelat separasi <strong>${escapeHtml(plateName)}</strong>...`, 'info');
    const res = await apiFetch(
      `/api/jobs/${state.selectedJobId}/files/${fileId}/separation?plate=${encodeURIComponent(
        plateName
      )}&mode=${encodeURIComponent(mode)}`
    );
    state.activeSeparation = {
      fileId,
      plate: res.plate_name || plateName,
      mode: res.display_mode || mode,
      imageUrl: res.image_url,
      coveragePct: res.coverage_pct ?? null,
      hexColor: res.hex_color || '#4f46e5',
      cacheStamp: Date.now(),
      loading: false,
    };
    renderWorkspace();
  } catch (err) {
    showToast(`Gagal memuat separasi warna: ${err.message}`, 'error');
  }
}

function clearSeparationPlate() {
  state.activeSeparation = {
    fileId: null,
    plate: null,
    mode: 'ink',
    imageUrl: null,
    coveragePct: null,
    hexColor: null,
    loading: false,
  };
  renderWorkspace();
}

function selectStage(stage) {
  state.selectedStage = stage;
  state.pinModeActive = false;
  state.canvasZoom = 1.0;
  state.canvasPanX = 0;
  state.canvasPanY = 0;
  renderWorkspace();
}

function triggerStageUpload(event, stage) {
  if (event) event.stopPropagation();
  state.uploadTargetStage = stage;
  const input = document.getElementById('hiddenStageFileInput');
  input.value = '';
  input.click();
}

function switchActiveSubFile(stage, fileId) {
  if (!state.activeSubFileId) state.activeSubFileId = {};
  state.activeSubFileId[stage] = parseInt(fileId, 10);
  state.selectedStage = stage;
  state.canvasZoom = 1.0;
  state.canvasPanX = 0;
  state.canvasPanY = 0;
  renderWorkspace();
}

async function confirmDeleteStageFile(event, fileId, stage) {
  if (event) event.stopPropagation();
  const fileObj = (state.jobDetail?.file_history || []).find((x) => x.id === fileId);
  const filename = fileObj?.filename || 'file ini';
  const stageName = STAGE_META[stage]?.label || stage;

  const ok = confirm(
    `Hapus file "${filename}" dari tahap ${stageName}?\n\nTindakan ini akan menghapus file fisik dari server dan memperbarui evaluasi checklist otomatis.`
  );
  if (!ok) return;

  try {
    showToast(`Menghapus file <strong>${filename}</strong>...`, 'info');
    await apiFetch(
      `/api/jobs/${state.selectedJobId}/files/${fileId}?actor_name=${encodeURIComponent(
        state.activeUser.full_name
      )}&actor_role=${encodeURIComponent(state.activeUser.role_label)}`,
      { method: 'DELETE' }
    );
    if (state.activeSubFileId && state.activeSubFileId[stage] === fileId) {
      delete state.activeSubFileId[stage];
    }
    showToast(`File <strong>${filename}</strong> berhasil dihapus.`, 'success');
    await refreshAllData(true);
  } catch (err) {
    showToast(`Gagal menghapus file: ${err.message}`, 'error');
  }
}

async function handleStageFileSelected(event) {
  const files = Array.from(event.target.files || []);
  if (!files.length || !state.selectedJobId) return;

  const formData = new FormData();
  formData.append('stage', state.uploadTargetStage);
  formData.append('uploaded_by', state.activeUser.full_name);
  formData.append('uploaded_role', state.activeUser.role_label);
  for (const f of files) {
    formData.append('files', f);
  }

  const fileCountLabel =
    files.length > 1 ? `${files.length} file` : `<strong>${files[0].name}</strong>`;
  try {
    showToast(`Mengunggah &amp; membaca parameter otomatis ${fileCountLabel}...`, 'info');
    const res = await apiFetch(`/api/jobs/${state.selectedJobId}/upload`, {
      method: 'POST',
      body: formData,
    });
    state.selectedStage = state.uploadTargetStage;
    if (res && res.file_id) {
      if (!state.activeSubFileId) state.activeSubFileId = {};
      state.activeSubFileId[state.selectedStage] = res.file_id;
    }
    showToast(
      `${fileCountLabel} berhasil dianalisis! Parameter checklist otomatis telah diperbarui.`,
      'success'
    );
    await refreshAllData(true);
  } catch (err) {
    showToast(`Gagal mengunggah file: ${err.message}`, 'error');
  }
}

function applyCanvasTransform() {
  const wrapper = document.getElementById('inspectorArtworkWrapper');
  if (wrapper) {
    wrapper.style.transform = `translate(${state.canvasPanX}px, ${state.canvasPanY}px) scale(${state.canvasZoom})`;
  }
  const zoomLabel = document.getElementById('canvasZoomLabel');
  if (zoomLabel) {
    zoomLabel.textContent = `${Math.round(state.canvasZoom * 100)}%`;
  }
}

function adjustZoom(delta) {
  state.canvasZoom = Math.min(6.0, Math.max(0.25, +(state.canvasZoom + delta).toFixed(2)));
  applyCanvasTransform();
}

function resetZoom() {
  state.canvasZoom = 1.0;
  state.canvasPanX = 0;
  state.canvasPanY = 0;
  applyCanvasTransform();
}

function onCanvasWheel(event) {
  event.preventDefault();
  const viewport = document.getElementById('inspectorCanvasViewport');
  if (!viewport) return;

  const rect = viewport.getBoundingClientRect();
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  const mouseX = event.clientX - cx;
  const mouseY = event.clientY - cy;

  const oldZoom = state.canvasZoom;
  const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
  const newZoom = Math.min(6.0, Math.max(0.25, +(oldZoom * factor).toFixed(3)));
  if (newZoom === oldZoom) return;

  const ratio = newZoom / oldZoom;
  state.canvasPanX = +(mouseX - (mouseX - state.canvasPanX) * ratio).toFixed(1);
  state.canvasPanY = +(mouseY - (mouseY - state.canvasPanY) * ratio).toFixed(1);
  state.canvasZoom = newZoom;
  applyCanvasTransform();
}

function onCanvasMouseDown(event) {
  // Allow panning with left button when NOT in pin mode, OR middle button anytime
  const isMiddle = event.button === 1;
  if (event.button !== 0 && !isMiddle) return;
  if (state.pinModeActive && !isMiddle) return;
  if (event.target && event.target.closest && event.target.closest('.canvas-pin')) return;

  event.preventDefault();
  const viewport = document.getElementById('inspectorCanvasViewport');
  if (viewport) viewport.classList.add('is-panning');

  const startX = event.clientX;
  const startY = event.clientY;
  const initPanX = state.canvasPanX;
  const initPanY = state.canvasPanY;

  const onMove = (moveEvt) => {
    state.canvasPanX = +(initPanX + (moveEvt.clientX - startX)).toFixed(1);
    state.canvasPanY = +(initPanY + (moveEvt.clientY - startY)).toFixed(1);
    applyCanvasTransform();
  };

  const onUp = () => {
    if (viewport) viewport.classList.remove('is-panning');
    window.removeEventListener('mousemove', onMove);
    window.removeEventListener('mouseup', onUp);
  };

  window.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);
}

function onCanvasDblClick(event) {
  if (state.pinModeActive) return;
  if (event.target && event.target.closest && event.target.closest('.canvas-pin')) return;
  resetZoom();
}

function toggleTrimGuide(val) {
  state.showTrimGuide = val;
  renderWorkspace();
}

function togglePinMode() {
  state.pinModeActive = !state.pinModeActive;
  renderWorkspace();
}

function onArtworkCanvasClick(event) {
  if (!state.pinModeActive) return;
  const wrapper = document.getElementById('inspectorArtworkWrapper');
  if (!wrapper) return;
  const rect = wrapper.getBoundingClientRect();
  const x_pct = Math.max(1, Math.min(99, +(((event.clientX - rect.left) / rect.width) * 100).toFixed(1)));
  const y_pct = Math.max(1, Math.min(99, +(((event.clientY - rect.top) / rect.height) * 100).toFixed(1)));

  openPinModalWithCoords(state.selectedStage, x_pct, y_pct, 12, 10, '', '');
}

function openPinModalWithCoords(stage, x_pct, y_pct, w_pct = 12, h_pct = 10, defaultTitle = '', defaultChecklist = '') {
  state.pendingPinCoords = { stage, x_pct, y_pct, w_pct, h_pct };
  document.getElementById('pinStageDisplay').textContent = stage;
  document.getElementById('pinCoordDisplay').textContent = `X: ${x_pct}%, Y: ${y_pct}%`;
  document.getElementById('pinTitle').value = defaultTitle;
  document.getElementById('pinComment').value = '';
  document.getElementById('pinChecklistCode').value = defaultChecklist;
  document.getElementById('pinModal').classList.add('open');
}

async function submitNewPin() {
  const title = document.getElementById('pinTitle').value.trim();
  const comment = document.getElementById('pinComment').value.trim();
  if (!title || !comment) {
    showToast('Mohon isi Judul Masalah dan Penjelasan Detail terlebih dahulu.', 'warn');
    return;
  }
  const payload = {
    stage: state.pendingPinCoords.stage,
    x_pct: state.pendingPinCoords.x_pct,
    y_pct: state.pendingPinCoords.y_pct,
    w_pct: state.pendingPinCoords.w_pct,
    h_pct: state.pendingPinCoords.h_pct,
    severity: document.getElementById('pinSeverity').value,
    category: document.getElementById('pinCategory').value,
    title,
    comment,
    checklist_code: document.getElementById('pinChecklistCode').value.trim(),
    actor_name: state.activeUser.full_name,
    actor_role: state.activeUser.role_label,
  };

  try {
    await apiFetch(`/api/jobs/${state.selectedJobId}/annotations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    closeModal('pinModal');
    state.pinModeActive = false;
    showToast('Pin Masalah berhasil disimpan pada koordinat file!', 'success');
    await refreshAllData(true);
  } catch (err) {
    showToast(`Gagal menyimpan pin: ${err.message}`, 'error');
  }
}

function focusPin(event, pinId) {
  if (event) event.stopPropagation();
  state.highlightedPinId = pinId;
  renderWorkspace();
}

async function resolvePin(event, pinId, newStatus) {
  if (event) event.stopPropagation();
  try {
    await apiFetch(`/api/jobs/${state.selectedJobId}/annotations/${pinId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        status: newStatus,
        resolved_note: newStatus === 'RESOLVED' ? 'Telah diperbaiki dan diverifikasi.' : 'Dibuka kembali',
        actor_name: state.activeUser.full_name,
        actor_role: state.activeUser.role_label,
      }),
    });
    showToast(`Status Pin diperbarui menjadi ${newStatus}.`, 'success');
    await refreshAllData(true);
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function deletePin(event, pinId) {
  if (event) event.stopPropagation();
  try {
    await apiFetch(
      `/api/jobs/${state.selectedJobId}/annotations/${pinId}?actor_name=${encodeURIComponent(
        state.activeUser.full_name
      )}&actor_role=${encodeURIComponent(state.activeUser.role_label)}`,
      { method: 'DELETE' }
    );
    showToast('Pin masalah dihapus.', 'info');
    await refreshAllData(true);
  } catch (err) {
    showToast(err.message, 'error');
  }
}

/* ============================================================================
 * TAB 2: MULTI-STAGE VISUAL COMPARATOR (PIXEL DIFF, OVERLAY, CURTAIN, BLINK)
 * ============================================================================ */
function stopBlinkTimer() {
  if (state.blinkTimer) {
    clearInterval(state.blinkTimer);
    state.blinkTimer = null;
  }
}

function renderComparatorTab(container) {
  const detail = state.jobDetail;
  const stageFiles = detail.stage_files || {};
  const comparisons = detail.comparisons || [];

  const fileA = stageFiles[state.compStageA];
  const fileB = stageFiles[state.compStageB];

  // Find latest stored comparison for (stageA, stageB)
  const activeComp = comparisons.find(
    (c) =>
      (c.stage_a === state.compStageA && c.stage_b === state.compStageB) ||
      (c.stage_a === state.compStageB && c.stage_b === state.compStageA)
  );

  let viewerHtml = '';
  if (!fileA || !fileB) {
    viewerHtml = `
      <div class="panel-card" style="padding:45px; text-align:center;">
        <h3>File untuk tahap ${state.compStageA} atau ${state.compStageB} belum tersedia</h3>
        <p style="color:var(--text-muted); margin-top:6px;">
          Pastikan kedua tahap sudah diunggah pada Tab 1 untuk menjalankan perbandingan visual &amp; Pixel Diff otomatis.
        </p>
      </div>
    `;
  } else if (state.compViewMode === 'PIXEL_DIFF') {
    const regions = activeComp?.regions || [];
    viewerHtml = `
      <div class="inspector-split">
        <div class="canvas-panel">
          <div class="canvas-toolbar">
            <div>
              <strong style="color:var(--text-strong);">Hasil Deteksi Pixel Diff / Imposisi Otomatis (${state.compStageA} vs ${state.compStageB})</strong>
              ${
                activeComp
                  ? `<span class="badge ${activeComp.similarity_pct >= 99 ? 'badge-pass' : 'badge-warn'}" style="margin-left:8px;">
                      Kemiripan: ${activeComp.similarity_pct}%
                     </span>`
                  : ''
              }
            </div>
            <button class="btn btn-sm btn-primary" onclick="runStageComparison('${state.compStageA}', '${state.compStageB}')">
              &#8635; Jalankan Ulang Analisis Pixel Diff
            </button>
          </div>
          <div class="canvas-viewport">
            ${
              activeComp
                ? `<div class="artwork-stage-wrapper">
                    <img src="${activeComp.diff_image_path}?t=${Date.now()}" alt="Diff Heatmap" />
                   </div>`
                : `<div style="text-align:center;">
                    <p style="color:var(--text-muted); margin-bottom:12px;">Belum ada hasil analisis Pixel Diff untuk pasangan tahap ini.</p>
                    <button class="btn btn-primary" onclick="runStageComparison('${state.compStageA}', '${state.compStageB}')">
                      &#9889; Analisis Perbedaan Otomatis (${state.compStageA} vs ${state.compStageB})
                    </button>
                   </div>`
            }
          </div>
        </div>

        <div class="panel-card">
          <div class="panel-card-header">
            <span>Area Perbedaan (${regions.length})</span>
          </div>
          <div class="panel-card-body" style="max-height:540px; overflow-y:auto;">
            ${
              activeComp
                ? `<div style="background:var(--bg-card); border:1px solid var(--border); padding:10px; border-radius:6px; margin-bottom:10px; font-size:12px;">
                    <strong>Ringkasan Analisis:</strong><br/>
                    ${activeComp.summary}
                   </div>`
                : ''
            }
            ${
              regions.length === 0
                ? `<div style="color:var(--text-muted); font-size:12px;">
                    Tidak ada deviasi piksel yang terdeteksi atau analisis belum dijalankan.
                   </div>`
                : regions
                    .map(
                      (r) => `
                        <div style="background:var(--bg-card); border:1px solid var(--border); border-radius:6px; padding:10px; margin-bottom:8px;">
                          <div style="display:flex; justify-content:space-between; align-items:center;">
                            <strong style="color:var(--text-strong); font-size:12.5px;">#${r.index}: ${r.label}</strong>
                            <span class="badge ${r.type === 'IMPOSITION_SLOT' ? 'badge-pass' : 'badge-fail'}">${r.type}</span>
                          </div>
                          <div style="font-size:11.5px; color:var(--text-muted); margin:4px 0 8px;">
                            Koordinat Lokasi: X: ${r.x_pct}%, Y: ${r.y_pct}% (Ukuran Kotak: ${r.w_pct}% &times; ${r.h_pct}%)
                          </div>
                          <button
                            class="btn btn-sm btn-danger"
                            onclick="convertDiffRegionToPin('${state.compStageB}', ${r.x_pct}, ${r.y_pct}, ${r.w_pct}, ${r.h_pct}, '${r.label}')"
                          >
                            &#128204; Jadikan Pin Masalah pada ${state.compStageB}
                          </button>
                        </div>
                      `
                    )
                    .join('')
            }
          </div>
        </div>
      </div>
    `;
  } else if (state.compViewMode === 'SIDE_BY_SIDE') {
    viewerHtml = `
      <div class="side-by-side-grid">
        <div class="comp-stage-pane">
          <div class="comp-pane-header">
            <span>TAHAP A: ${STAGE_META[state.compStageA].label}</span>
            <span class="badge badge-info">${fileA.filename}</span>
          </div>
          <div class="comp-pane-body">
            <img src="${fileA.preview_path}" alt="${state.compStageA}" />
          </div>
        </div>
        <div class="comp-stage-pane">
          <div class="comp-pane-header">
            <span>TAHAP B: ${STAGE_META[state.compStageB].label}</span>
            <span class="badge badge-info">${fileB.filename}</span>
          </div>
          <div class="comp-pane-body">
            <img src="${fileB.preview_path}" alt="${state.compStageB}" />
          </div>
        </div>
      </div>
    `;
  } else if (state.compViewMode === 'OVERLAY') {
    viewerHtml = `
      <div class="canvas-panel">
        <div class="canvas-toolbar">
          <div style="display:flex; align-items:center; gap:14px; flex-wrap:wrap;">
            <strong>Mode Overlay / Onion Skin (${state.compStageA} + ${state.compStageB})</strong>
            <label style="display:flex; align-items:center; gap:8px; font-size:12px;">
              <span>Transparansi ${state.compStageB}: <strong id="opacityLabelVal">${state.overlayOpacity}%</strong></span>
              <input
                type="range"
                min="0"
                max="100"
                value="${state.overlayOpacity}"
                oninput="updateOverlayOpacity(this.value)"
                style="width:160px;"
              />
            </label>
            <label style="display:flex; align-items:center; gap:6px; font-size:12px;">
              <span>Blend Mode:</span>
              <select onchange="updateOverlayBlend(this.value)" style="background:var(--bg-input); color:var(--text-strong); border:1px solid var(--border); padding:3px 8px; border-radius:4px;">
                <option value="normal" ${state.overlayBlend === 'normal' ? 'selected' : ''}>Normal Alpha</option>
                <option value="difference" ${state.overlayBlend === 'difference' ? 'selected' : ''}>Difference (Hitam = Identik)</option>
                <option value="multiply" ${state.overlayBlend === 'multiply' ? 'selected' : ''}>Multiply (Cek Overprint/Trapping)</option>
              </select>
            </label>
          </div>
        </div>
        <div class="canvas-viewport">
          <div class="overlay-stack-container">
            <img class="base-img" src="${fileA.preview_path}" alt="Base" />
            <img
              class="top-img"
              id="overlayTopImg"
              src="${fileB.preview_path}"
              alt="Top"
              style="opacity: ${state.overlayOpacity / 100}; mix-blend-mode: ${state.overlayBlend};"
            />
          </div>
        </div>
      </div>
    `;
  } else if (state.compViewMode === 'CURTAIN') {
    viewerHtml = `
      <div class="canvas-panel">
        <div class="canvas-toolbar">
          <div style="display:flex; align-items:center; gap:14px;">
            <strong>Mode Curtain Split Slider (Kiri: ${state.compStageA} | Kanan: ${state.compStageB})</strong>
            <label style="display:flex; align-items:center; gap:8px; font-size:12px;">
              <span>Geser Tirai Pembanding:</span>
              <input
                type="range"
                min="0"
                max="100"
                value="${state.curtainPos}"
                oninput="updateCurtainPos(this.value)"
                style="width:220px;"
              />
            </label>
          </div>
        </div>
        <div class="canvas-viewport">
          <div class="overlay-stack-container">
            <img class="base-img" src="${fileB.preview_path}" alt="Stage B" />
            <div class="curtain-clip" id="curtainClipDiv" style="width: ${state.curtainPos}%;">
              <img class="base-img" src="${fileA.preview_path}" alt="Stage A" />
            </div>
          </div>
        </div>
      </div>
    `;
  } else if (state.compViewMode === 'BLINK') {
    viewerHtml = `
      <div class="canvas-panel">
        <div class="canvas-toolbar">
          <div style="display:flex; align-items:center; gap:14px;">
            <strong>Mode Blink Comparator (Kedip Otomatis ${state.compStageA} &#8644; ${state.compStageB})</strong>
            <span class="badge badge-warn" id="blinkActiveBadge">MENAMPILKAN: ${state.compStageA}</span>
            <span style="font-size:12px; color:var(--text-secondary);">
              Mata manusia langsung menangkap pergeseran 0.1 mm atau huruf berubah saat gambar berkedip di tempat yang sama.
            </span>
          </div>
        </div>
        <div class="canvas-viewport">
          <div class="overlay-stack-container">
            <img class="base-img" id="blinkImgElement" src="${fileA.preview_path}" alt="Blink Preview" />
          </div>
        </div>
      </div>
    `;
    setTimeout(() => startBlinkTimer(fileA.preview_path, fileB.preview_path), 60);
  }

  container.innerHTML = `
    <div class="comparator-toolbar">
      <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
        <strong style="font-size:12.5px;">Pasangan Tahap:</strong>
        <button
          class="btn btn-sm ${state.compStageA === 'ORIGINAL' && state.compStageB === 'AW' ? 'btn-primary' : 'btn-outline'}"
          onclick="setComparisonPair('ORIGINAL', 'AW')"
        >
          Asli vs AW
        </button>
        <button
          class="btn btn-sm ${state.compStageA === 'AW' && state.compStageB === 'ONEUP' ? 'btn-primary' : 'btn-outline'}"
          onclick="setComparisonPair('AW', 'ONEUP')"
        >
          AW vs OneUp
        </button>
        <button
          class="btn btn-sm ${state.compStageA === 'ONEUP' && state.compStageB === 'LAYOUT' ? 'btn-primary' : 'btn-outline'}"
          onclick="setComparisonPair('ONEUP', 'LAYOUT')"
        >
          OneUp vs Layout
        </button>
      </div>

      <div class="mode-pills">
        <button class="mode-pill ${state.compViewMode === 'PIXEL_DIFF' ? 'active' : ''}" onclick="setCompViewMode('PIXEL_DIFF')">
          Pixel Diff
        </button>
        <button class="mode-pill ${state.compViewMode === 'SIDE_BY_SIDE' ? 'active' : ''}" onclick="setCompViewMode('SIDE_BY_SIDE')">
          Side-by-Side
        </button>
        <button class="mode-pill ${state.compViewMode === 'OVERLAY' ? 'active' : ''}" onclick="setCompViewMode('OVERLAY')">
          Overlay
        </button>
        <button class="mode-pill ${state.compViewMode === 'CURTAIN' ? 'active' : ''}" onclick="setCompViewMode('CURTAIN')">
          Curtain
        </button>
        <button class="mode-pill ${state.compViewMode === 'BLINK' ? 'active' : ''}" onclick="setCompViewMode('BLINK')">
          Blink
        </button>
      </div>
    </div>

    ${viewerHtml}
  `;
}

async function setComparisonPair(stageA, stageB) {
  stopBlinkTimer();
  state.compStageA = stageA;
  state.compStageB = stageB;
  const comparisons = state.jobDetail?.comparisons || [];
  const exists = comparisons.some(
    (c) =>
      (c.stage_a === stageA && c.stage_b === stageB) ||
      (c.stage_a === stageB && c.stage_b === stageA)
  );
  if (!exists && state.jobDetail?.stage_files?.[stageA] && state.jobDetail?.stage_files?.[stageB]) {
    await runStageComparison(stageA, stageB);
  } else {
    renderWorkspace();
  }
}

function setCompViewMode(mode) {
  stopBlinkTimer();
  state.compViewMode = mode;
  renderWorkspace();
}

function updateOverlayOpacity(val) {
  state.overlayOpacity = parseInt(val, 10);
  const img = document.getElementById('overlayTopImg');
  const lbl = document.getElementById('opacityLabelVal');
  if (img) img.style.opacity = state.overlayOpacity / 100;
  if (lbl) lbl.textContent = `${state.overlayOpacity}%`;
}

function updateOverlayBlend(blend) {
  state.overlayBlend = blend;
  const img = document.getElementById('overlayTopImg');
  if (img) img.style.mixBlendMode = blend;
}

function updateCurtainPos(val) {
  state.curtainPos = parseInt(val, 10);
  const clip = document.getElementById('curtainClipDiv');
  if (clip) clip.style.width = `${state.curtainPos}%`;
}

function startBlinkTimer(srcA, srcB) {
  stopBlinkTimer();
  state.blinkShowB = false;
  state.blinkTimer = setInterval(() => {
    const img = document.getElementById('blinkImgElement');
    const badge = document.getElementById('blinkActiveBadge');
    if (!img || !badge) {
      stopBlinkTimer();
      return;
    }
    state.blinkShowB = !state.blinkShowB;
    img.src = state.blinkShowB ? srcB : srcA;
    badge.textContent = `MENAMPILKAN: ${state.blinkShowB ? state.compStageB : state.compStageA}`;
    badge.className = `badge ${state.blinkShowB ? 'badge-info' : 'badge-warn'}`;
  }, state.blinkIntervalMs);
}

async function runStageComparison(stageA, stageB) {
  try {
    showToast(`Menjalankan komparasi piksel &amp; visual <strong>${stageA} vs ${stageB}</strong>...`, 'info');
    await apiFetch(`/api/jobs/${state.selectedJobId}/compare`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        stage_a: stageA,
        stage_b: stageB,
        actor_name: state.activeUser.full_name,
        actor_role: state.activeUser.role_label,
      }),
    });
    showToast(`Komparasi visual <strong>${stageA} vs ${stageB}</strong> selesai!`, 'success');
    await refreshAllData(true);
  } catch (err) {
    showToast(`Gagal membandingkan: ${err.message}`, 'error');
  }
}

function convertDiffRegionToPin(stage, x_pct, y_pct, w_pct, h_pct, label) {
  state.selectedStage = stage;
  openPinModalWithCoords(
    stage,
    x_pct,
    y_pct,
    Math.max(w_pct, 8),
    Math.max(h_pct, 8),
    `Temuan Visual Comparator: ${label}`,
    'CMP-01'
  );
}

/* ============================================================================
 * TAB 3: 3 OFFICIAL CHECKLIST FORMS (CHECK LIST AWAL, ONEUP, AKHIR)
 * ============================================================================ */
function switchChecklistForm(formType) {
  state.activeChecklistForm = formType;
  renderWorkspace();
}

function _ofChk(d, key, label, style = '') {
  const checked = d[key] ? 'checked' : '';
  return `<label class="of-checkbox-group" style="${style}"><input type="checkbox" ${checked} onchange="onFormCheckChange('${key}', this.checked)"> ${label}</label>`;
}

function _ofInput(d, key, widthPx = '100px', extraStyle = '') {
  const val = String(d[key] ?? '').replace(/"/g, '&quot;');
  return `<input type="text" class="of-dotted-input" style="width:${widthPx}; ${extraStyle}" value="${val}" onchange="onFormInputChange('${key}', this.value)">`;
}

function _renderCommonDesignRowsInteractive(d, includeFormatCustomer = true, isAkhir = false) {
  let row1 = '';
  if (includeFormatCustomer) {
    const sideTd = isAkhir
      ? `<td rowspan="15" class="of-side-cat"><div class="of-vertical-text">FILE DESAIN</div></td>`
      : '';
    row1 = `
      <tr>
        ${sideTd}
        <td class="of-label-col">Format file Customer${isAkhir ? ' :' : ''}</td>
        <td class="of-colon-col">${isAkhir ? '' : ':'}</td>
        <td>
          ${_ofChk(d, 'fmt_corel', 'Corel')}
          ${_ofChk(d, 'fmt_psd', 'PSD')}
          ${_ofChk(d, 'fmt_pdf', 'PDF')}
          ${_ofChk(d, 'fmt_ai', 'Illustrator')}
          ${_ofChk(d, 'fmt_jpeg', 'JPEG')}
          <label class="of-checkbox-group" style="margin-right:0;">
            <input type="checkbox" ${d.fmt_lainnya ? 'checked' : ''} onchange="onFormCheckChange('fmt_lainnya', this.checked)">
            Lainnya ${_ofInput(d, 'fmt_lainnya_text', '80px', 'margin-left:4px;')}
          </label>
        </td>
      </tr>
    `;
  }

  const endCls = isAkhir ? 'class="of-section-end"' : '';

  return `
    ${row1}
    <tr>
      <td class="of-label-col">Format Warna dlm File${!includeFormatCustomer || isAkhir ? ' :' : ''}</td>
      <td class="of-colon-col">${!includeFormatCustomer || isAkhir ? '' : ':'}</td>
      <td>
        ${_ofChk(d, 'warna_rgb', 'RGB', 'width:110px;')}
        ${_ofChk(d, 'warna_cmyk', 'CMYK', 'width:100px;')}
        ${_ofChk(d, 'warna_pantone', 'Pantone')}
      </td>
    </tr>
    <tr>
      <td class="of-label-col">Acuan Desain${!includeFormatCustomer || isAkhir ? ' :' : ''}</td>
      <td class="of-colon-col">${!includeFormatCustomer || isAkhir ? '' : ':'}</td>
      <td>
        ${_ofChk(d, 'acuan_file_customer', 'File Customer')}
        ${_ofChk(d, 'acuan_desain_prima', 'Desain Prima')}
        ${_ofChk(d, 'acuan_existing_customer', 'Existing/offset Customer')}
        ${_ofChk(d, 'acuan_file_lama', 'File Lama')}
      </td>
    </tr>
    <tr>
      <td class="of-label-col">Format Gambar dlm File${!includeFormatCustomer || isAkhir ? ' :' : ''}</td>
      <td class="of-colon-col">${!includeFormatCustomer || isAkhir ? '' : ':'}</td>
      <td>
        ${_ofChk(d, 'img_rgb', 'RGB', 'width:110px;')}
        ${_ofChk(d, 'img_cmyk', 'CMYK', 'width:100px;')}
        ${_ofChk(d, 'img_pantone', 'Pantone')}
      </td>
    </tr>
    <tr>
      <td class="of-label-col">Shadow</td>
      <td class="of-colon-col">:</td>
      <td>
        ${_ofChk(d, 'shadow_sudah_expand', 'Sudah di Expand')}
        ${_ofChk(d, 'shadow_belum_expand', 'Belum Di Expand')}
        ${_ofChk(d, 'shadow_100_black', '100% Black')}
        ${_ofChk(d, 'shadow_4_warna', '4 Warna')}
      </td>
    </tr>
    <tr>
      <td class="of-label-col">Gradasi</td>
      <td class="of-colon-col">:</td>
      <td>
        ${_ofChk(d, 'gradasi_sesuai', 'Sesuai File', 'width:160px;')}
        ${_ofChk(d, 'gradasi_perlu_dirubah', 'Perlu dirubah')}
      </td>
    </tr>
    <tr>
      <td class="of-label-col">Transparasi</td>
      <td class="of-colon-col">:</td>
      <td>
        ${_ofInput(d, 'transparasi_pct', '95px')} <b>%</b>
      </td>
    </tr>
    <tr>
      <td class="of-label-col">Acuan Warna</td>
      <td class="of-colon-col">:</td>
      <td>
        <div style="margin-bottom:4px;">
          ${_ofChk(d, 'acuan_warna_contoh_customer', 'Contoh Customer', 'width:115px;')}
          ${_ofChk(d, 'acuan_warna_standart_mesin', 'Standart Mesin', 'width:105px;')}
          ${_ofChk(d, 'acuan_warna_pantone_tc', 'Pantone/TC', 'width:95px;')}
          ${_ofChk(d, 'acuan_warna_acc_foto', 'ACC Foto', 'width:85px;')}
          ${_ofChk(d, 'acuan_warna_acc_datang', 'ACC Datang')}
        </div>
        <div>
          ${_ofChk(d, 'acuan_warna_print_digital', 'Print Digital', 'width:115px;')}
          ${_ofChk(d, 'acuan_warna_color_range', 'Color Range', 'width:105px;')}
          ${_ofChk(d, 'acuan_warna_cetakan_lama', 'Cetakan Lama', 'width:95px;')}
          <label class="of-checkbox-group" style="margin-right:0;">
            <input type="checkbox" ${d.acuan_warna_lainnya ? 'checked' : ''} onchange="onFormCheckChange('acuan_warna_lainnya', this.checked)">
            Lainnya ${_ofInput(d, 'acuan_warna_lainnya_text', '115px', 'margin-left:4px;')}
          </label>
        </div>
      </td>
    </tr>
    <tr>
      <td class="of-label-col">Bleed/Sisiran</td>
      <td class="of-colon-col">:</td>
      <td>
        ${_ofInput(d, 'bleed_mm', '48px')} mm
        <span style="margin-left:10px;">Jarak Konten dgn Potong/ Lipat : ${_ofInput(d, 'jarak_konten_mm', '48px')} mm</span>
        <span class="of-inline-bold" style="margin-left:30px;">Cek Typo GPT</span>
        <label class="of-checkbox-group" style="margin-left:6px;">
          <input type="checkbox" ${d.cek_typo_gpt ? 'checked' : ''} onchange="onFormCheckChange('cek_typo_gpt', this.checked)">
        </label>
      </td>
    </tr>
    <tr>
      <td class="of-label-col">Kualitas Gambar</td>
      <td class="of-colon-col">:</td>
      <td>
        ${_ofInput(d, 'dpi_value', '52px')} dpi
        <span style="margin-left:32px;">Format Gambar &nbsp;: </span>
        ${_ofChk(d, 'img_berupa_link', 'Berupa Link')}
        ${_ofChk(d, 'img_embedded_link', 'Embedded Link')}
      </td>
    </tr>
    <tr>
      <td class="of-label-col">Outline Gambar/Teks</td>
      <td class="of-colon-col">:</td>
      <td>
        ${_ofInput(d, 'outline_pt', '55px')} pt
        <span style="margin-left:30px;"></span>
        ${_ofChk(d, 'outline_expand_line', 'Expand Line')}
        ${_ofChk(d, 'outline_edited_line', 'Edited Line')}
      </td>
    </tr>
    <tr>
      <td class="of-label-col">Komposisi Hitam :</td>
      <td class="of-colon-col"></td>
      <td>
        ${_ofChk(d, 'hitam_4_warna', '4 WARNA', 'width:90px;')}
        ${_ofChk(d, 'hitam_100_black', '100 % Black', 'width:95px;')}
        ${_ofChk(d, 'hitam_rich_cyan30', '100 % Black + Cyan 30 %')}
      </td>
    </tr>
    <tr>
      <td class="of-label-col">Black Overprint :</td>
      <td class="of-colon-col"></td>
      <td>
        ${_ofChk(d, 'overprint_fill', 'Overprint Fill', 'width:95px;')}
        ${_ofChk(d, 'overprint_trapp', 'Overlap Outline (Trapp)')}
      </td>
    </tr>
    <tr>
      <td class="of-label-col">Format Font :</td>
      <td class="of-colon-col"></td>
      <td>
        ${_ofChk(d, 'font_editable', 'Editable', 'width:85px;')}
        ${_ofChk(d, 'font_convert', 'Convert', 'width:80px;')}
        <span class="of-inline-bold" style="margin-right:6px;">QR / Barcode :</span>
        ${_ofChk(d, 'barcode_jpeg_png', 'Jpeg / Png')}
        ${_ofChk(d, 'barcode_vector', 'Vector')}
      </td>
    </tr>
    <tr ${endCls}>
      <td class="of-label-col">Warna Cetak</td>
      <td class="of-colon-col">:</td>
      <td>
        ${_ofInput(d, 'warna_cetak', '130px')}
        <span style="margin-left:16px;">Kode Pantone/TC ${_ofInput(d, 'kode_pantone_tc', '230px', 'margin-left:4px;')}</span>
      </td>
    </tr>
  `;
}

function renderChecklistTab(container) {
  const formsMap = state.jobDetail?.checklist_forms || {};
  const ftype = state.activeChecklistForm || 'AWAL';
  const currentForm = formsMap[ftype] || { form_data: {} };
  const d = currentForm.form_data || {};

  const formTabsMeta = [
    { key: 'AWAL', label: '1. Checklist Awal', sub: 'File Asli Customer' },
    { key: 'ONEUP', label: '2. Checklist OneUp', sub: 'Master 1-Up' },
    { key: 'AKHIR', label: '3. Checklist Akhir', sub: 'Desain, Diecut, Prepress & Finishing' },
  ];

  const tabsHtml = formTabsMeta
    .map((m) => {
      const fObj = formsMap[m.key] || {};
      const sigCount = (fObj.desainer_name ? 1 : 0) + (fObj.reviewer_name ? 1 : 0);
      const badgeCls = sigCount === 2 ? 'badge-pass' : sigCount === 1 ? 'badge-info' : 'badge-pending';
      return `
        <button
          class="form-tab-pill ${ftype === m.key ? 'active' : ''}"
          onclick="switchChecklistForm('${m.key}')"
        >
          <span>${m.label}</span>
          <span class="badge ${badgeCls}" style="font-size:10.5px; padding:2px 8px;">${sigCount}/2 TTD</span>
        </button>
      `;
    })
    .join('');

  const previewList =
    d.preview_images && Array.isArray(d.preview_images) && d.preview_images.length > 0
      ? d.preview_images
      : d.preview_image_url
      ? [{ url: d.preview_image_url, filename: d.nama_file || '' }]
      : [];

  const previewCountBadge =
    previewList.length > 1
      ? `<span class="badge" style="background:#e0e7ff; color:#3730a3; font-size:10.5px; padding:2px 6px; margin-left:6px;">${previewList.length} Item File</span>`
      : '';

  const previewImgBlock =
    d.preview_image_url && d.show_preview_image !== false
    previewList.length > 0 && d.show_preview_image !== false
      ? `
        <div style="text-align:center; margin:8px 0 10px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:10px;">
          <img src="${d.preview_image_url}?t=${Date.now()}" alt="Preview Artwork" style="max-height:240px; max-width:100%; object-fit:contain; border-radius:4px;" />
          <div style="font-size:11px; color:#64748b; margin-top:4px;">Preview otomatis dari ${ftype === 'AWAL' ? 'File Asli Customer' : 'file tahapan'} (${d.nama_file || ''})</div>
        <div style="margin:8px 0 10px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:12px;">
          <div style="display:flex; flex-wrap:wrap; gap:12px; justify-content:center; align-items:flex-start;">
            ${previewList
              .map(
                (item, idx) => `
              <div style="flex:1; min-width:180px; max-width:280px; background:#ffffff; border:1px solid #cbd5e1; border-radius:6px; padding:8px; text-align:center; box-shadow:0 1px 3px rgba(0,0,0,0.05); box-sizing:border-box;">
                <div style="font-size:11px; font-weight:700; color:#1e293b; margin-bottom:6px; word-break:break-all; border-bottom:1px solid #f1f5f9; padding-bottom:4px;">
                  Item #${idx + 1}: ${item.filename || 'File Artwork'}
                </div>
                <img src="${item.url}?t=${Date.now()}" alt="${item.filename || 'Preview Artwork'}" style="max-height:220px; max-width:100%; object-fit:contain; border-radius:4px; background:#f8fafc;" />
              </div>
            `
              )
              .join('')}
          </div>
          <div style="font-size:11px; color:#64748b; margin-top:8px; text-align:center;">
            Preview otomatis dari ${previewList.length} file tahapan ${
          ftype === 'AWAL' ? 'File Asli Customer' : ftype === 'ONEUP' ? 'One-Up' : 'Layout'
        }
          </div>
        </div>
      `
      : '';

  let tableRowsHtml = '';
  let sheetHeaderTitle = '';

  if (ftype === 'AWAL') {
    sheetHeaderTitle = 'CHECK LIST AWAL<br>DIVISION : DESIGN';
    tableRowsHtml = `
      ${_renderCommonDesignRowsInteractive(d, true, false)}
      <tr>
        <td colspan="3" class="of-preview-box">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
            <div style="display:flex; align-items:center;">
              <span style="font-weight:bold;">PREVIEW</span>
              ${previewCountBadge}
            </div>
            ${
              previewList.length > 0
                ? `<label style="font-size:11.5px; font-weight:normal; cursor:pointer; color:#1d6ff2;">
                    <input type="checkbox" ${d.show_preview_image !== false ? 'checked' : ''} onchange="onFormCheckChange('show_preview_image', this.checked)">
                    Tampilkan Gambar Preview File Asli ${previewList.length > 1 ? `(${previewList.length} File)` : ''}
                   </label>`
                : ''
            }
          </div>
          ${previewImgBlock}
          <textarea
            class="of-textarea-notes"
            rows="3"
            placeholder="Catatan area preview (opsional)..."
            onchange="onFormInputChange('preview_notes', this.value)"
          >${d.preview_notes || ''}</textarea>

          <div style="margin-top:12px;">
            <div style="font-weight:bold; margin-bottom:5px;">CATATAN</div>
            <textarea
              class="of-textarea-notes"
              rows="3"
              placeholder="Catatan pemeriksaan Checklist Awal..."
              onchange="onFormInputChange('catatan_notes', this.value)"
            >${d.catatan_notes || ''}</textarea>
          </div>
        </td>
      </tr>
    `;
  } else if (ftype === 'ONEUP') {
    sheetHeaderTitle = 'CHECK LIST ONEUP<br>DIVISION : DESIGN';
    tableRowsHtml = `
      <tr>
        <td colspan="3" class="of-preview-box">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
            <div style="display:flex; align-items:center;">
              <span style="font-weight:bold;">PREVIEW</span>
              ${previewCountBadge}
            </div>
            ${
              previewList.length > 0
                ? `<label style="font-size:11.5px; font-weight:normal; cursor:pointer; color:#1d6ff2;">
                    <input type="checkbox" ${d.show_preview_image !== false ? 'checked' : ''} onchange="onFormCheckChange('show_preview_image', this.checked)">
                    Tampilkan Gambar Preview One-Up ${previewList.length > 1 ? `(${previewList.length} File)` : ''}
                   </label>`
                : ''
            }
          </div>
          ${previewImgBlock}
          <textarea
            class="of-textarea-notes"
            rows="3"
            placeholder="Catatan preview OneUp..."
            onchange="onFormInputChange('preview_notes', this.value)"
          >${d.preview_notes || ''}</textarea>
        </td>
      </tr>
      ${_renderCommonDesignRowsInteractive(d, false, false)}
    `;
  } else {
    sheetHeaderTitle = 'CHECK LIST AKHIR<br>DIVISION : DESIGN';
    tableRowsHtml = `
      ${_renderCommonDesignRowsInteractive(d, true, true)}
      <!-- SECTION 2: DIECUT -->
      <tr>
        <td rowspan="6" class="of-side-cat"><div class="of-vertical-text">DIECUT</div></td>
        <td class="of-label-col">Source Diecut :</td>
        <td class="of-colon-col"></td>
        <td>
          ${_ofChk(d, 'diecut_src_pacdora', 'Pacdora')}
          ${_ofChk(d, 'diecut_src_dummy_polos', 'Develop Dummy Polos')}
          ${_ofChk(d, 'diecut_src_contoh_customer', 'Contoh Customer')}
          ${_ofChk(d, 'diecut_src_file_lama', 'File Lama')}
        </td>
      </tr>
      <tr>
        <td class="of-label-col">Validasi Diecut/Pisau :</td>
        <td class="of-colon-col"></td>
        <td>
          <label class="of-checkbox-multiline"><input type="checkbox" ${d.diecut_val_dummy_polos ? 'checked' : ''} onchange="onFormCheckChange('diecut_val_dummy_polos', this.checked)"> <span>Dummy<br>Polos</span></label>
          <label class="of-checkbox-multiline"><input type="checkbox" ${d.diecut_val_dummy_digital ? 'checked' : ''} onchange="onFormCheckChange('diecut_val_dummy_digital', this.checked)"> <span>Dummy<br>Digital</span></label>
          <label class="of-checkbox-multiline"><input type="checkbox" ${d.diecut_val_existing_customer ? 'checked' : ''} onchange="onFormCheckChange('diecut_val_existing_customer', this.checked)"> <span>Existing<br>Customer</span></label>
          ${_ofChk(d, 'diecut_val_pisau_lama', 'Pisau Lama', 'vertical-align:top; margin-top:4px;')}
        </td>
      </tr>
      <tr>
        <td class="of-label-col">UkuranTerbentang/Terbuka :</td>
        <td class="of-colon-col"></td>
        <td>
          ${_ofInput(d, 'ukuran_terbentang_mm', '120px')} mm
          <span class="of-inline-bold" style="margin-left:22px;">Ukuran Dimensi Jadi :</span>
          ${_ofInput(d, 'ukuran_dimensi_jadi_mm', '120px', 'margin-left:4px;')} mm
        </td>
      </tr>
      <tr>
        <td class="of-label-col">Posisi Lidah :</td>
        <td class="of-colon-col"></td>
        <td>
          ${_ofChk(d, 'lidah_kanan', 'Kanan Desain')}
          ${_ofChk(d, 'lidah_kiri', 'Kiri Desain')}
          <span class="of-inline-bold" style="margin-left:6px;">Panjang Lidah Lem:</span>
          ${_ofInput(d, 'panjang_lidah_lem_mm', '110px', 'margin-left:4px;')} mm
        </td>
      </tr>
      <tr>
        <td class="of-label-col">Setingan Film :</td>
        <td class="of-colon-col"></td>
        <td>
          ${_ofChk(d, 'film_mirror', 'Mirror', 'width:90px;')}
          ${_ofChk(d, 'film_terbaca', 'Terbaca (Untuk Eflute)')}
        </td>
      </tr>
      <tr class="of-section-end">
        <td class="of-label-col">Pisau / Rit :</td>
        <td class="of-colon-col"></td>
        <td>
          <div style="margin-bottom:4px;">
            ${_ofChk(d, 'pisau_rit', 'Rit', 'width:90px;')}
            ${_ofChk(d, 'pisau_pisau', 'Pisau', 'width:75px;')}
            <label class="of-checkbox-group" style="width:150px;">
              <input type="checkbox" ${d.pisau_perporasi ? 'checked' : ''} onchange="onFormCheckChange('pisau_perporasi', this.checked)">
              Perporasi ${_ofInput(d, 'pisau_perporasi_text', '55px', 'margin-left:4px;')}
            </label>
            ${_ofChk(d, 'pisau_overprint_diecut', 'Overprint Diecut')}
          </div>
          <div>
            <label class="of-checkbox-group">
              <input type="checkbox" ${d.pisau_mata_ayam ? 'checked' : ''} onchange="onFormCheckChange('pisau_mata_ayam', this.checked)">
              Mata Ayam ${_ofInput(d, 'mata_ayam_mm', '40px', 'margin:0 4px;')} MM, Jumlah ${_ofInput(d, 'mata_ayam_pcs', '50px', 'margin:0 4px;')} pcs
            </label>
          </div>
        </td>
      </tr>

      <!-- SECTION 3: PRE PRESS -->
      <tr>
        <td rowspan="10" class="of-side-cat"><div class="of-vertical-text">PRE PRESS</div></td>
        <td class="of-label-col">Uk. Potong Kertas</td>
        <td class="of-colon-col">:</td>
        <td>${_ofInput(d, 'uk_potong_kertas_mm', '130px')} mm</td>
      </tr>
      <tr>
        <td class="of-label-col">Jumlah Up / Druk</td>
        <td class="of-colon-col">:</td>
        <td>${_ofInput(d, 'jumlah_up_druk', '130px')} up</td>
      </tr>
      <tr>
        <td class="of-label-col">Jarak Up</td>
        <td class="of-colon-col">:</td>
        <td>${_ofInput(d, 'jarak_up_mm', '130px')} mm</td>
      </tr>
      <tr>
        <td class="of-label-col">Jumlah Sisi/Muka :</td>
        <td class="of-colon-col"></td>
        <td>
          ${_ofChk(d, 'muka_1', '1 Muka', 'width:100px;')}
          ${_ofChk(d, 'muka_2', '2 Muka')}
        </td>
      </tr>
      <tr>
        <td class="of-label-col">Jenis Layout :</td>
        <td class="of-colon-col"></td>
        <td>
          ${_ofChk(d, 'layout_balik_kertas', 'Balik Kertas', 'width:105px;')}
          ${_ofChk(d, 'layout_lain_plat', 'Lain Plat', 'width:90px;')}
          ${_ofChk(d, 'layout_lain_unleg', 'Lain Unleg')}
        </td>
      </tr>
      <tr>
        <td class="of-label-col">Nomer Diecut :</td>
        <td class="of-colon-col"></td>
        <td>
          ${_ofChk(d, 'nomer_diecut_logo_pp', 'Logo PP')}
        </td>
      </tr>
      <tr>
        <td colspan="3">
          <span class="of-inline-bold">Register Cetak &amp; KRESS (HARUS REGISTER)</span>
          <label class="of-checkbox-group" style="margin-left:4px; margin-right:16px;"><input type="checkbox" ${d.reg_cetak_kress ? 'checked' : ''} onchange="onFormCheckChange('reg_cetak_kress', this.checked)"></label>
          <span class="of-inline-bold">SIGNATURE</span>
          <label class="of-checkbox-group" style="margin-left:4px; margin-right:16px;"><input type="checkbox" ${d.reg_signature ? 'checked' : ''} onchange="onFormCheckChange('reg_signature', this.checked)"></label>
          <span class="of-inline-bold">PANAH UNLEG</span>
          <label class="of-checkbox-group" style="margin-left:4px; margin-right:16px;"><input type="checkbox" ${d.reg_panah_unleg ? 'checked' : ''} onchange="onFormCheckChange('reg_panah_unleg', this.checked)"></label>
          <span class="of-inline-bold">PALETE WARNA BG</span>
          <label class="of-checkbox-group" style="margin-left:4px; margin-right:0;"><input type="checkbox" ${d.reg_palete_warna_bg ? 'checked' : ''} onchange="onFormCheckChange('reg_palete_warna_bg', this.checked)"></label>
        </td>
      </tr>
      <tr>
        <td class="of-label-col">Metode Layout :</td>
        <td class="of-colon-col"></td>
        <td>
          ${_ofChk(d, 'metode_manual', 'Manual', 'width:100px;')}
          ${_ofChk(d, 'metode_prinnect', 'Prinnect Software')}
        </td>
      </tr>
      <tr>
        <td class="of-label-col">Layout Cetak Mesin :</td>
        <td class="of-colon-col"></td>
        <td>
          ${_ofChk(d, 'mesin_pm', 'PM', 'width:50px;')}
          ${_ofChk(d, 'mesin_sm', 'SM', 'width:50px;')}
          ${_ofChk(d, 'mesin_cd', 'CD', 'width:50px;')}
          ${_ofChk(d, 'mesin_gto', 'GTO', 'width:60px;')}
          ${_ofChk(d, 'mesin_sord', 'SOR-D', 'width:70px;')}
          ${_ofChk(d, 'mesin_sors', 'SOR-S', 'width:70px;')}
          ${_ofChk(d, 'mesin_toko', 'TOKO')}
        </td>
      </tr>
      <tr class="of-section-end">
        <td class="of-label-col">UNLEG Mesin</td>
        <td class="of-colon-col">:</td>
        <td>
          ${_ofInput(d, 'unleg_mesin_text', '115px', 'margin-right:10px;')}
          ${_ofChk(d, 'unleg_dari_kress', 'dari kress')}
          ${_ofChk(d, 'unleg_dari_blok', 'dari blok')}
        </td>
      </tr>

      <!-- SECTION 4: FINSHING -->
      <tr>
        <td rowspan="9" class="of-side-cat"><div class="of-vertical-text">FINSHING</div></td>
        <td rowspan="2" class="of-fin-left">SPOT UV</td>
        <td colspan="2">
          <span class="of-fin-sublabel">Ketebalan garis</span> : ${_ofInput(d, 'spotuv_ketebalan', '145px')}
        </td>
      </tr>
      <tr>
        <td colspan="2">
          <span class="of-fin-sublabel">Jumlah UP / druk</span> : ${_ofInput(d, 'spotuv_jumlah_up', '145px')} up
        </td>
      </tr>
      <tr>
        <td rowspan="3" class="of-fin-left">
          <div style="margin-bottom:4px;">HOTPRINT</div>
          <div style="font-weight:normal;"><b>Kode :</b> ${_ofInput(d, 'hotprint_kode', '110px')}</div>
        </td>
        <td colspan="2">
          <span class="of-fin-sublabel">Ukuran Klise</span> : ${_ofInput(d, 'hotprint_ukuran_klise', '145px')}
        </td>
      </tr>
      <tr>
        <td colspan="2">
          <span class="of-fin-sublabel">Ketebalan garis</span> : ${_ofInput(d, 'hotprint_ketebalan', '145px')}
        </td>
      </tr>
      <tr>
        <td colspan="2">
          <span class="of-fin-sublabel">Jumlah klise/druk</span> : ${_ofInput(d, 'hotprint_jumlah_up', '145px')} up
        </td>
      </tr>
      <tr>
        <td rowspan="3" class="of-fin-left">
          <div style="display:flex; align-items:center; justify-content:space-between; width:120px; margin-bottom:8px;">
            <span>EMBOSS</span>
            <input type="checkbox" style="width:15px; height:15px; accent-color:#192857; margin:0; cursor:pointer;" ${d.emboss_check ? 'checked' : ''} onchange="onFormCheckChange('emboss_check', this.checked)">
          </div>
          <div style="display:flex; align-items:center; justify-content:space-between; width:120px;">
            <span>DEBOSS</span>
            <input type="checkbox" style="width:15px; height:15px; accent-color:#192857; margin:0; cursor:pointer;" ${d.deboss_check ? 'checked' : ''} onchange="onFormCheckChange('deboss_check', this.checked)">
          </div>
        </td>
        <td colspan="2">
          <span class="of-fin-sublabel">Ukuran Klise</span> : ${_ofInput(d, 'emboss_ukuran_klise', '145px')}
        </td>
      </tr>
      <tr>
        <td colspan="2">
          <span class="of-fin-sublabel">Ketebalan garis</span> : ${_ofInput(d, 'emboss_ketebalan', '145px')}
        </td>
      </tr>
      <tr>
        <td colspan="2">
          <span class="of-fin-sublabel">Jumlah klise/druk</span> : ${_ofInput(d, 'emboss_jumlah_up', '145px')} up
        </td>
      </tr>
      <tr class="of-section-end">
        <td class="of-fin-left">LAMINATING</td>
        <td colspan="2">
          ${_ofChk(d, 'lam_doff', 'Doff', 'width:80px;')}
          ${_ofChk(d, 'lam_glossy', 'Kilap/glossy', 'width:95px;')}
          <span class="of-inline-bold">Batas Laminating Glossy :</span>
          ${_ofInput(d, 'batas_lam_glossy_mm', '50px', 'margin-left:4px;')} mm dari rit
        </td>
      </tr>
    `;
  }

  const renderSigBox = (slotKey, label) => {
    const isDes = slotKey === 'DESAINER';
    const signedName = isDes ? currentForm.desainer_name : currentForm.reviewer_name;
    const signedRole = isDes ? currentForm.desainer_role : currentForm.reviewer_role;
    const signedAt = isDes ? currentForm.desainer_signed_at : currentForm.reviewer_signed_at;

    if (signedName) {
      return `
        <div class="of-signature-box" style="border-color:#0f8a4c; background:#f0fdf4;">
          <div style="font-size:10.5px; color:#0f8a4c; font-weight:700; margin-bottom:4px;">&#10004; TELAH DITANDATANGANI</div>
          <div class="of-sig-line">( <strong style="color:#0f172a;">${signedName}</strong> )</div>
          <div style="font-weight:bold; color:#192857;">${label}</div>
          <div style="font-size:10.5px; color:#475569; font-weight:normal; margin-top:2px;">${signedRole || ''} &bull; ${signedAt || ''}</div>
          <button
            class="btn btn-sm btn-outline"
            style="margin-top:8px; padding:3px 10px; font-size:11px;"
            onclick="signChecklistForm('${slotKey}', 'unsign')"
          >
            &#10006; Batal TTD
          </button>
        </div>
      `;
    }
    return `
      <div class="of-signature-box">
        <div class="of-sig-line">( ____________________ )</div>
        <div style="font-weight:bold; margin-bottom:8px;">${label}</div>
        <button
          class="btn btn-sm btn-primary"
          style="padding:5px 12px; font-size:11.5px;"
          onclick="signChecklistForm('${slotKey}', 'sign')"
        >
          &#9997; Tanda Tangan (${state.activeUser?.full_name || 'User'})
        </button>
      </div>
    `;
  };

  container.innerHTML = `
    <div class="comparator-toolbar">
      <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
        <span style="font-weight:700; color:var(--text-strong);">Pilih Form Checklist:</span>
        ${tabsHtml}
      </div>

      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
        <button class="btn btn-sm btn-outline" onclick="autofillCurrentChecklistForm()">
          &#8635; Auto-Fill dari File
        </button>
        <button class="btn btn-sm btn-primary" onclick="saveCurrentChecklistForm(true)">
          &#128190; Simpan Form
        </button>
        <button class="btn btn-sm btn-success" onclick="printCurrentChecklistForm()">
          &#128424; Cetak / Print Form
        </button>
      </div>
    </div>

    <div style="max-width:900px; margin:0 auto 12px; display:flex; justify-content:space-between; align-items:center; font-size:12px; color:var(--text-secondary); padding:0 6px;">
      <div>
        <span class="badge badge-auto" style="margin-right:6px;">AUTO-FILL AKTIF</span>
        Parameter warna, ukuran, bleed, resolusi DPI, font, overprint, dan preview terisi otomatis dari file yang di-upload &amp; tetap dapat diedit manual.
      </div>
      <div id="checklistSaveStatusText">
        ${currentForm.updated_at ? `Terakhir disimpan oleh <strong>${currentForm.updated_by || 'Sistem'}</strong> (${currentForm.updated_at})` : ''}
      </div>
    </div>

    <div class="official-form-wrapper">
      <div class="of-header">
        ${sheetHeaderTitle}
      </div>

      <table class="of-top-info">
        <tr>
          <td style="width: 85px; padding-left: 25px; font-weight:bold;">Customer</td>
          <td style="width: 15px; font-weight:bold;">:</td>
          <td>${_ofInput(d, 'customer', '95%')}</td>
        </tr>
        <tr>
          <td style="padding-left: 25px; font-weight:bold;">Nama File</td>
          <td style="font-weight:bold;">:</td>
          <td>${_ofInput(d, 'nama_file', '95%')}</td>
        </tr>
      </table>

      <table class="of-checklist-table">
        ${tableRowsHtml}
      </table>

      <div class="of-footer">
        <div>Telah diperiksa oleh:</div>
        <div class="of-signature-container">
          ${renderSigBox('DESAINER', 'DESAINER')}
          ${renderSigBox('REVIEWER', 'REVIEWER')}
        </div>
      </div>
    </div>
  `;
}

function _getActiveFormDataObj() {
  const ftype = state.activeChecklistForm || 'AWAL';
  if (!state.jobDetail.checklist_forms) state.jobDetail.checklist_forms = {};
  if (!state.jobDetail.checklist_forms[ftype]) {
    state.jobDetail.checklist_forms[ftype] = { form_data: {} };
  }
  return state.jobDetail.checklist_forms[ftype].form_data;
}

function onFormCheckChange(key, checked) {
  const d = _getActiveFormDataObj();
  d[key] = Boolean(checked);
  if (key === 'show_preview_image') {
    renderWorkspace();
  }
  scheduleChecklistAutoSave();
}

function onFormInputChange(key, value) {
  const d = _getActiveFormDataObj();
  d[key] = value;
  scheduleChecklistAutoSave();
}

function scheduleChecklistAutoSave() {
  const statusEl = document.getElementById('checklistSaveStatusText');
  if (statusEl) statusEl.innerHTML = '<em>Menyimpan perubahan...</em>';
  if (state.checklistSaveTimer) clearTimeout(state.checklistSaveTimer);
  state.checklistSaveTimer = setTimeout(() => {
    saveCurrentChecklistForm(false);
  }, 450);
}

async function saveCurrentChecklistForm(showNotification = true) {
  if (!state.selectedJobId || !state.jobDetail) return;
  const ftype = state.activeChecklistForm || 'AWAL';
  const d = _getActiveFormDataObj();
  try {
    const updated = await apiFetch(
      `/api/jobs/${state.selectedJobId}/checklist-forms/${ftype}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          form_data: d,
          actor_name: state.activeUser?.full_name || 'Naufal',
          actor_role: state.activeUser?.role_label || 'Operator Prepress',
        }),
      }
    );
    state.jobDetail.checklist_forms[ftype] = updated;
    const statusEl = document.getElementById('checklistSaveStatusText');
    if (statusEl) {
      statusEl.innerHTML = `Tersimpan otomatis oleh <strong>${updated.updated_by}</strong> (${updated.updated_at})`;
    }
    if (showNotification) {
      showToast(`Form <strong>Checklist ${ftype}</strong> berhasil disimpan!`, 'success');
    }
  } catch (err) {
    showToast(`Gagal menyimpan form: ${err.message}`, 'error');
  }
}

async function autofillCurrentChecklistForm() {
  if (!state.selectedJobId) return;
  const ftype = state.activeChecklistForm || 'AWAL';
  try {
    const updated = await apiFetch(
      `/api/jobs/${state.selectedJobId}/checklist-forms/${ftype}/autofill?actor_name=${encodeURIComponent(
        state.activeUser?.full_name || 'Naufal'
      )}&actor_role=${encodeURIComponent(state.activeUser?.role_label || 'Operator Prepress')}`,
      { method: 'POST' }
    );
    state.jobDetail.checklist_forms[ftype] = updated;
    showToast(
      `Form <strong>Checklist ${ftype}</strong> telah disinkronisasi otomatis dari hasil pembacaan file!`,
      'success'
    );
    renderWorkspace();
  } catch (err) {
    showToast(`Gagal menjalankan Auto-Fill: ${err.message}`, 'error');
  }
}

async function signChecklistForm(slot, action = 'sign') {
  if (!state.selectedJobId || !state.activeUser) return;
  const ftype = state.activeChecklistForm || 'AWAL';
  try {
    await saveCurrentChecklistForm(false);
    const updated = await apiFetch(
      `/api/jobs/${state.selectedJobId}/checklist-forms/${ftype}/sign`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          slot,
          action,
          actor_name: state.activeUser.full_name,
          actor_nik: state.activeUser.nik,
          actor_role: state.activeUser.role_label,
        }),
      }
    );
    state.jobDetail.checklist_forms[ftype] = updated;
    if (action === 'unsign') {
      showToast(`Tanda tangan <strong>${slot}</strong> pada Checklist ${ftype} dibatalkan.`, 'info');
    } else {
      showToast(
        `Kolom <strong>${slot}</strong> pada Checklist ${ftype} berhasil ditandatangani oleh <strong>${state.activeUser.full_name}</strong>!`,
        'success'
      );
    }
    await refreshAllData(true);
  } catch (err) {
    showToast(`Gagal menandatangani form: ${err.message}`, 'error');
  }
}

async function printCurrentChecklistForm() {
  if (!state.selectedJobId) return;
  const ftype = state.activeChecklistForm || 'AWAL';
  await saveCurrentChecklistForm(false);
  const printUrl = `/api/jobs/${state.selectedJobId}/checklist-forms/${ftype}/print`;
  window.open(printUrl, '_blank');
}

/* ============================================================================
 * TAB 4: MULTI-ROLE APPROVAL & DIGITAL SIGN-OFF MATRIX
 * ============================================================================ */
function renderApprovalTab(container) {
  const detail = state.jobDetail;
  const job = detail.job;
  const approvals = detail.approvals || [];
  const checklist = detail.checklist || [];
  const annotations = detail.annotations || [];
  const stageFiles = detail.stage_files || {};

  const appMap = {};
  for (const a of approvals) appMap[a.role_stage] = a;

  const uploadedCount = Object.keys(stageFiles).length;
  const openPins = annotations.filter((a) => a.status === 'OPEN').length;
  const failChecks = checklist.filter((c) => c.manual_status === 'FAIL').length;
  const passChecks = checklist.filter((c) => c.manual_status === 'PASS').length;

  const tiers = [
    {
      stage: 'OPERATOR',
      title: 'Tahap 1: Pemeriksaan Operator Prepress',
      desc: 'Memastikan file AW, OneUp, dan Layout telah dibuat sesuai SPK dan parameter teknis lulus preflight.',
    },
    {
      stage: 'QC',
      title: 'Tahap 2: Verifikasi QC Prepress Inspector',
      desc: 'Memverifikasi perbandingan visual File Asli vs AW vs OneUp vs Layout serta memastikan tidak ada pin masalah terbuka.',
    },
    {
      stage: 'SUPERVISOR',
      title: 'Tahap 3: Final Approval Kepala / Supervisor Prepress',
      desc: 'Otorisasi akhir untuk rilis file ke bagian Output Plate CTP / Naik Cetak Produksi.',
    },
  ];

  const cardsHtml = tiers
    .map((t) => {
      const signed = appMap[t.stage];
      const cardClass = signed
        ? signed.decision.startsWith('APPROV')
          ? 'signed-approved'
          : 'signed-rejected'
        : '';

      return `
        <div class="approval-card ${cardClass}">
          <div>
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
              <strong style="font-size:14px; color:var(--text-strong);">${t.title}</strong>
              ${signed ? statusBadgeHtml(signed.decision) : `<span class="badge badge-pending">MENUNGGU</span>`}
            </div>
            <p style="font-size:12px; color:var(--text-muted); margin-bottom:12px;">${t.desc}</p>

            ${
              signed
                ? `<div class="sig-box">
                    <div><strong>Pejabat Penandatangan:</strong> ${signed.approver_name}</div>
                    <div><strong>NIK / ID:</strong> ${signed.approver_nik} (${signed.approver_role_label})</div>
                    <div><strong>Waktu Otorisasi:</strong> ${signed.signed_at}</div>
                    <div><strong>Catatan:</strong> ${signed.notes || '-'}</div>
                    <div style="margin-top:6px; color:var(--accent); word-break:break-all;">
                      <strong>SHA-256 Digital Sign:</strong> #${signed.signature_hash}
                    </div>
                   </div>`
                : `<div class="sig-box" style="text-align:center; padding:22px 10px; color:var(--text-muted);">
                    Belum ditandatangani secara digital pada tahap <strong>${t.stage}</strong>.
                   </div>`
            }
          </div>

          <div style="border-top:1px solid var(--border); padding-top:12px;">
            <div style="font-size:11.5px; color:var(--text-secondary); margin-bottom:6px;">
              Tandatangani sebagai personel aktif: <strong>${state.activeUser.full_name} (${state.activeUser.nik})</strong>
            </div>
            <input
              type="text"
              id="appNote_${t.stage}"
              class="search-input"
              style="margin-bottom:8px; font-size:12px;"
              placeholder="Catatan approval / alasan penolakan revisi..."
              value="${signed?.notes || ''}"
            />
            <div style="display:flex; gap:6px;">
              <button class="btn btn-sm btn-success" style="flex:1;" onclick="submitApproval('${t.stage}', 'APPROVED')">
                &#10004; Approve (ACC)
              </button>
              <button class="btn btn-sm btn-warning" onclick="submitApproval('${t.stage}', 'APPROVED_WITH_NOTE')">
                ACC + Catatan
              </button>
              <button class="btn btn-sm btn-danger" onclick="submitApproval('${t.stage}', 'REJECTED')">
                &#10006; Tolak / Revisi
              </button>
            </div>
          </div>
        </div>
      `;
    })
    .join('');

  container.innerHTML = `
    <div class="panel-card" style="margin-bottom:16px;">
      <div class="panel-card-header">
        <span>Kesiapan Sebelum Approval</span>
        <a class="btn btn-sm btn-primary" href="/api/jobs/${job.id}/report.pdf" target="_blank">
          &#128196; Download Sertifikat QC PDF Resmi
        </a>
      </div>
      <div class="panel-card-body">
        <div class="stages-grid" style="margin-bottom:0;">
          <div class="param-item">
            <div class="param-label">Kelengkapan Tahap File</div>
            <div class="param-val" style="color:${uploadedCount === 4 ? 'var(--success)' : 'var(--warning)'};">
              ${uploadedCount} / 4 Tahap Diunggah
            </div>
          </div>
          <div class="param-item">
            <div class="param-label">Progress Digital Checklist</div>
            <div class="param-val" style="color:${passChecks === checklist.length ? 'var(--success)' : 'var(--accent)'};">
              ${passChecks} / ${checklist.length} Item PASS
            </div>
          </div>
          <div class="param-item">
            <div class="param-label">Parameter Gagal (FAIL)</div>
            <div class="param-val" style="color:${failChecks === 0 ? 'var(--success)' : 'var(--danger)'};">
              ${failChecks} Item FAIL
            </div>
          </div>
          <div class="param-item">
            <div class="param-label">Pin Masalah Artwork Terbuka</div>
            <div class="param-val" style="color:${openPins === 0 ? 'var(--success)' : 'var(--danger)'};">
              ${openPins} Pin OPEN
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="approval-grid">
      ${cardsHtml}
    </div>
  `;
}

async function submitApproval(roleStage, decision) {
  const noteInput = document.getElementById(`appNote_${roleStage}`);
  const notes = noteInput ? noteInput.value.trim() : '';

  try {
    const res = await apiFetch(`/api/jobs/${state.selectedJobId}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        role_stage: roleStage,
        decision,
        approver_name: state.activeUser.full_name,
        approver_nik: state.activeUser.nik,
        approver_role_label: state.activeUser.role_label,
        notes,
      }),
    });
    showToast(
      `Otorisasi <strong>${roleStage}</strong> berhasil ditandatangani oleh <strong>${state.activeUser.full_name}</strong> (${decision}). Sign Hash: #${res.signature_hash.slice(0, 10)}`,
      decision === 'REJECTED' ? 'warn' : 'success'
    );
    await refreshAllData(true);
  } catch (err) {
    showToast(err.message, 'error');
  }
}

/* ============================================================================
 * TAB 5: COMPLETE IMMUTABLE AUDIT TRAIL & REVISION HISTORY
 * ============================================================================ */
function renderAuditTab(container) {
  const logs = state.jobDetail?.audit_logs || [];
  const fileHistory = state.jobDetail?.file_history || [];

  const rowsHtml = logs
    .map(
      (lg) => `
        <div style="display:grid; grid-template-columns: 155px 210px 140px 1fr; gap:12px; padding:10px 14px; border-bottom:1px solid var(--border); font-size:12px; align-items:center;">
          <div style="font-family:monospace; color:var(--text-muted);">${lg.created_at}</div>
          <div>
            <strong style="color:var(--text-strong);">${lg.actor_name}</strong>
            <div style="font-size:11px; color:var(--text-muted);">${lg.actor_role}</div>
          </div>
          <div>
            <span class="badge badge-info">${lg.action_type}</span>
            ${lg.target_stage ? `<span class="badge badge-auto">${lg.target_stage}</span>` : ''}
          </div>
          <div style="color:var(--text-secondary);">
            ${lg.summary}
          </div>
        </div>
      `
    )
    .join('');

  const filesHtml = fileHistory
    .map(
      (f) => `
        <div style="display:flex; justify-content:space-between; align-items:center; padding:8px 12px; border-bottom:1px solid var(--border); font-size:12px;">
          <div>
            <span class="badge badge-info">${f.stage} v${f.version}</span>
            <strong style="color:var(--text-strong); margin-left:6px;">${f.filename}</strong>
            <span style="color:var(--text-muted); margin-left:8px;">(${(f.file_size_bytes / 1024).toFixed(1)} KB)</span>
          </div>
          <div style="display:flex; align-items:center; gap:12px;">
            <span style="color:var(--text-muted);">Diunggah oleh <strong>${f.uploaded_by}</strong> pada ${f.uploaded_at}</span>
            ${statusBadgeHtml(f.auto_status)}
            <a class="btn btn-sm btn-outline" href="${f.stored_path}" target="_blank">Download File</a>
          </div>
        </div>
      `
    )
    .join('');

  container.innerHTML = `
    <div class="panel-card" style="margin-bottom:16px;">
      <div class="panel-card-header">
        <span>Riwayat Versi File (${fileHistory.length})</span>
      </div>
      <div>${filesHtml}</div>
    </div>

    <div class="panel-card">
      <div class="panel-card-header">
        <span>Jejak Audit (siapa, kapan, apa)</span>
        <span class="badge badge-info">${logs.length} Aktivitas Tercatat</span>
      </div>
      <div>${rowsHtml}</div>
    </div>
  `;
}

/* ============================================================================
 * MODALS: CREATE JOB, SPK CAMERA / SCREENSHOT SCANNER & CUSTOMIZE TEMPLATES
 * ============================================================================ */
let spkCameraStream = null;

document.addEventListener('paste', async (event) => {
  const modal = document.getElementById('createJobModal');
  if (!modal || !modal.classList.contains('open')) return;
  const items = event.clipboardData?.items || [];
  for (const item of items) {
    if (item.type && item.type.startsWith('image/')) {
      event.preventDefault();
      const blob = item.getAsFile();
      if (blob) {
        await uploadAndScanSpkBlob(blob, 'screenshot_paste_spk.png');
      }
      return;
    }
  }
});

function openCreateJobModal() {
  const nextNum = `JOB-2026-${1001 + state.jobs.length}`;
  document.getElementById('njJobNumber').value = nextNum;
  state.pendingSpkChecklistOverrides = null;
  const resPanel = document.getElementById('spkScanResultPanel');
  if (resPanel) {
    resPanel.style.display = 'none';
    resPanel.innerHTML = '';
  }
  document.querySelectorAll('#createJobModal .spk-autofilled').forEach((el) => {
    el.classList.remove('spk-autofilled');
  });
  document.getElementById('createJobModal').classList.add('open');
}

function closeCreateJobModal() {
  stopSpkCamera();
  closeModal('createJobModal');
}

function closeModal(id) {
  document.getElementById(id).classList.remove('open');
}

async function toggleSpkCamera() {
  const camBox = document.getElementById('spkCameraContainer');
  if (!camBox) return;
  if (camBox.style.display !== 'none') {
    stopSpkCamera();
    return;
  }
  try {
    spkCameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    const videoEl = document.getElementById('spkCameraVideo');
    videoEl.srcObject = spkCameraStream;
    camBox.style.display = 'block';
    showToast('Kamera aktif! Arahkan kertas SPK ke kamera lalu klik Ambil Foto.', 'info');
  } catch (err) {
    showToast(
      `Tidak dapat mengakses kamera (${err.message}). Anda tetap dapat menggunakan Paste Screenshot (Ctrl+V) atau Upload Gambar SPK.`,
      'warn'
    );
  }
}

function stopSpkCamera() {
  if (spkCameraStream) {
    spkCameraStream.getTracks().forEach((t) => t.stop());
    spkCameraStream = null;
  }
  const videoEl = document.getElementById('spkCameraVideo');
  if (videoEl) videoEl.srcObject = null;
  const camBox = document.getElementById('spkCameraContainer');
  if (camBox) camBox.style.display = 'none';
}

async function captureSpkCameraFrame() {
  const videoEl = document.getElementById('spkCameraVideo');
  if (!videoEl || !spkCameraStream) return;
  const canvas = document.createElement('canvas');
  canvas.width = videoEl.videoWidth || 1280;
  canvas.height = videoEl.videoHeight || 720;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
  stopSpkCamera();

  canvas.toBlob(async (blob) => {
    if (blob) {
      await uploadAndScanSpkBlob(blob, 'camera_capture_spk.png');
    }
  }, 'image/png');
}

async function handleSpkFileSelected(event) {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file) return;
  await uploadAndScanSpkBlob(file, file.name);
}

async function uploadAndScanSpkBlob(fileOrBlob, filename = 'spk_scan.png') {
  const resPanel = document.getElementById('spkScanResultPanel');
  if (resPanel) {
    resPanel.style.display = 'block';
    resPanel.innerHTML = `
      <div style="padding:14px; text-align:center; background:#fff; border-radius:10px; border:1px solid var(--border); color:var(--accent); font-weight:600;">
        &#8987; Sedang memindai &amp; mengekstrak data Kertas SPK...
      </div>
    `;
  }

  const formData = new FormData();
  formData.append('file', fileOrBlob, filename);
  formData.append('actor_name', state.activeUser?.full_name || 'Naufal');
  formData.append('actor_role', state.activeUser?.role_label || 'Operator Prepress');

  try {
    const data = await apiFetch('/api/spk/scan', {
      method: 'POST',
      body: formData,
    });
    applySpkScanResultToForm(data);
    showToast(
      `Kertas SPK berhasil dibaca! Seluruh kolom Job &amp; 3 Form Checklist telah diisi otomatis.`,
      'success'
    );
  } catch (err) {
    if (resPanel) resPanel.style.display = 'none';
    showToast(`Gagal membaca SPK: ${err.message}`, 'error');
  }
}

async function scanSampleSpk(sampleType = 'odoo') {
  const resPanel = document.getElementById('spkScanResultPanel');
  if (resPanel) {
    resPanel.style.display = 'block';
    resPanel.innerHTML = `
      <div style="padding:14px; text-align:center; background:#fff; border-radius:10px; border:1px solid var(--border); color:var(--accent); font-weight:600;">
        &#8987; Memindai contoh SPK ${sampleType === 'manual' ? 'Manual (No. Job 15638)' : 'Production Order (WH/MO/05661)'}...
      </div>
    `;
  }
  try {
    const data = await apiFetch(`/api/spk/scan-sample?sample_type=${encodeURIComponent(sampleType)}`, { method: 'POST' });
    applySpkScanResultToForm(data);
    showToast(
      `Contoh SPK <strong>${data.job_number}</strong> berhasil diekstrak otomatis ke seluruh kolom!`,
      'success'
    );
  } catch (err) {
    if (resPanel) resPanel.style.display = 'none';
    showToast(`Gagal memuat contoh SPK: ${err.message}`, 'error');
  }
}

function applySpkScanResultToForm(data) {
  const setAndHighlight = (id, val) => {
    const el = document.getElementById(id);
    if (!el || val === undefined || val === null) return;
    el.value = val;
    el.classList.add('spk-autofilled');
  };

  setAndHighlight('njJobNumber', data.job_number);
  setAndHighlight('njItemCode', data.item_code);
  setAndHighlight('njProductName', data.product_name);
  setAndHighlight('njCustomerName', data.customer_name);
  setAndHighlight('njPackageType', data.package_type);
  setAndHighlight('njPrintMethod', data.print_method);
  setAndHighlight('njTargetW', data.target_width_mm);
  setAndHighlight('njTargetH', data.target_height_mm);
  setAndHighlight('njLayoutW', data.layout_width_mm);
  setAndHighlight('njLayoutH', data.layout_height_mm);
  setAndHighlight('njBleed', data.required_bleed_mm);
  setAndHighlight('njMinDpi', data.min_dpi);
  setAndHighlight('njColorSpec', data.color_spec);
  setAndHighlight('njExpectedNup', data.expected_nup);
  setAndHighlight('njNotes', data.notes);

  state.pendingSpkChecklistOverrides = data.checklist_overrides || null;

  const meta = data.spk_meta || {};
  const resPanel = document.getElementById('spkScanResultPanel');
  if (resPanel) {
    resPanel.style.display = 'block';
    resPanel.innerHTML = `
      <div class="spk-result-card">
        <div>
          ${
            data.spk_image_url
              ? `<img src="${data.spk_image_url}?t=${Date.now()}" alt="Preview SPK" class="spk-result-thumb" />`
              : ''
          }
        </div>
        <div>
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
            <div>
              <span class="badge badge-pass">&#10004; BERHASIL DIEKSTRAK DARI SPK</span>
              <strong style="color:var(--text-strong); margin-left:6px;">${data.job_number}</strong>
            </div>
            <span style="font-size:11px; color:var(--text-muted);">Otomatis diteruskan ke Checklist Awal, OneUp &amp; Akhir</span>
          </div>
          <div class="spk-meta-chips">
            <div class="spk-meta-item"><strong>Pelanggan:</strong> ${data.customer_name || '-'}</div>
            <div class="spk-meta-item"><strong>Quantity:</strong> ${meta.quantity || '-'}</div>
            <div class="spk-meta-item"><strong>Uk. Jadi (cm &rarr; mm):</strong> ${meta.uk_jadi_raw || '-'}</div>
            <div class="spk-meta-item"><strong>Uk. Potong (cm &rarr; mm):</strong> ${meta.uk_potong_raw || '-'}</div>
            <div class="spk-meta-item"><strong>Kertas / Plano:</strong> ${meta.kertas_plano || '-'}</div>
            <div class="spk-meta-item"><strong>Mesin &amp; Cetak:</strong> ${meta.mesin || '-'}</div>
            <div class="spk-meta-item"><strong>Finishing:</strong> ${meta.finishing || '-'}</div>
            <div class="spk-meta-item"><strong>Deadline / PPIC:</strong> ${meta.deadline || '-'} (${meta.ppic || '-'})</div>
          </div>
        </div>
      </div>
    `;
  }
}

async function submitCreateJob() {
  const payload = {
    job_number: document.getElementById('njJobNumber').value.trim(),
    product_name: document.getElementById('njProductName').value.trim(),
    customer_name: document.getElementById('njCustomerName').value.trim(),
    item_code: document.getElementById('njItemCode').value.trim(),
    package_type: document.getElementById('njPackageType').value,
    print_method: document.getElementById('njPrintMethod').value,
    target_width_mm: parseFloat(document.getElementById('njTargetW').value) || 120,
    target_height_mm: parseFloat(document.getElementById('njTargetH').value) || 180,
    layout_width_mm: parseFloat(document.getElementById('njLayoutW')?.value) || 290,
    layout_height_mm: parseFloat(document.getElementById('njLayoutH')?.value) || 410,
    required_bleed_mm: parseFloat(document.getElementById('njBleed').value) || 3.0,
    min_dpi: parseInt(document.getElementById('njMinDpi').value, 10) || 300,
    color_spec: document.getElementById('njColorSpec').value.trim(),
    expected_nup: parseInt(document.getElementById('njExpectedNup').value, 10) || 4,
    notes: document.getElementById('njNotes').value.trim(),
    created_by: state.activeUser.full_name,
    created_role: state.activeUser.role_label,
    generate_demo_files: document.getElementById('njGenerateDemo').checked,
    demo_has_defect: document.getElementById('njDemoDefect').checked,
    checklist_overrides: state.pendingSpkChecklistOverrides || null,
  };

  if (!payload.job_number || !payload.product_name) {
    showToast('Nomor Job dan Nama Produk wajib diisi.', 'warn');
    return;
  }

  try {
    closeCreateJobModal();
    showToast(`Membuat Job <strong>${payload.job_number}</strong> &amp; menjalankan preflight...`, 'info');
    const created = await apiFetch('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    state.selectedJobId = created.id;
    state.pendingSpkChecklistOverrides = null;
    await refreshAllData(true);
    showToast(`Job <strong>${payload.job_number}</strong> berhasil dibuat!`, 'success');
  } catch (err) {
    showToast(`Gagal membuat job: ${err.message}`, 'error');
  }
}

async function openEditJobModal(event, jobId) {
  if (event) event.stopPropagation();
  const targetId = jobId || state.selectedJobId;
  if (!targetId) return;

  let job = state.jobDetail?.job?.id === targetId ? state.jobDetail.job : null;
  if (!job) {
    job = state.jobs.find((j) => j.id === targetId);
  }
  if (!job) return;

  document.getElementById('ejJobId').value = job.id;
  document.getElementById('ejJobNumber').value = job.job_number || '';
  document.getElementById('ejItemCode').value = job.item_code || '';
  document.getElementById('ejProductName').value = job.product_name || '';
  document.getElementById('ejCustomerName').value = job.customer_name || '';
  document.getElementById('ejPackageType').value = job.package_type || 'Folding Carton / Box';
  const rawMethod = (job.print_method || '').toLowerCase();
  document.getElementById('ejPrintMethod').value = rawMethod.includes('digital') ? 'Cetak Digital' : 'Cetak Offset';
  document.getElementById('ejRevisionNo').value = job.revision_no || 1;
  document.getElementById('ejExpectedNup').value = job.expected_nup || 4;
  document.getElementById('ejTargetW').value = job.target_width_mm || 120;
  document.getElementById('ejTargetH').value = job.target_height_mm || 180;
  document.getElementById('ejBleed').value = job.required_bleed_mm ?? 3.0;
  document.getElementById('ejMinDpi').value = job.min_dpi || 300;
  document.getElementById('ejLayoutW').value = job.layout_width_mm || 290;
  document.getElementById('ejLayoutH').value = job.layout_height_mm || 410;
  document.getElementById('ejColorSpec').value = job.color_spec || '';
  document.getElementById('ejNotes').value = job.notes || '';

  document.getElementById('editJobModal').classList.add('open');
}

async function submitEditJob() {
  const jobId = parseInt(document.getElementById('ejJobId').value, 10);
  if (!jobId) return;

  const payload = {
    job_number: document.getElementById('ejJobNumber').value.trim(),
    product_name: document.getElementById('ejProductName').value.trim(),
    customer_name: document.getElementById('ejCustomerName').value.trim(),
    item_code: document.getElementById('ejItemCode').value.trim(),
    package_type: document.getElementById('ejPackageType').value,
    print_method: document.getElementById('ejPrintMethod').value,
    revision_no: parseInt(document.getElementById('ejRevisionNo').value, 10) || 1,
    target_width_mm: parseFloat(document.getElementById('ejTargetW').value) || 120,
    target_height_mm: parseFloat(document.getElementById('ejTargetH').value) || 180,
    required_bleed_mm: parseFloat(document.getElementById('ejBleed').value) || 3.0,
    min_dpi: parseInt(document.getElementById('ejMinDpi').value, 10) || 300,
    color_spec: document.getElementById('ejColorSpec').value.trim(),
    expected_nup: parseInt(document.getElementById('ejExpectedNup').value, 10) || 4,
    layout_width_mm: parseFloat(document.getElementById('ejLayoutW').value) || 290,
    layout_height_mm: parseFloat(document.getElementById('ejLayoutH').value) || 410,
    notes: document.getElementById('ejNotes').value.trim(),
    actor_name: state.activeUser.full_name,
    actor_role: state.activeUser.role_label,
  };

  if (!payload.job_number || !payload.product_name) {
    showToast('Nomor Job dan Nama Produk wajib diisi.', 'warn');
    return;
  }

  try {
    closeModal('editJobModal');
    await apiFetch(`/api/jobs/${jobId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    state.selectedJobId = jobId;
    await refreshAllData(true);
    showToast(
      `Job <strong>${payload.job_number}</strong> berhasil diperbarui &amp; parameter checklist telah dievaluasi ulang!`,
      'success'
    );
  } catch (err) {
    showToast(`Gagal memperbarui job: ${err.message}`, 'error');
  }
}

async function confirmDeleteJob(event, jobId, jobNumber) {
  if (event) event.stopPropagation();
  const ok = window.confirm(
    `Apakah Anda yakin ingin menghapus Job "${jobNumber}" beserta seluruh file tahap, pin masalah, dan riwayat pemeriksaannya?`
  );
  if (!ok) return;

  try {
    await apiFetch(
      `/api/jobs/${jobId}?actor_name=${encodeURIComponent(
        state.activeUser.full_name
      )}&actor_role=${encodeURIComponent(state.activeUser.role_label)}`,
      { method: 'DELETE' }
    );
    closeModal('editJobModal');
    if (state.selectedJobId === jobId) {
      state.selectedJobId = null;
    }
    showToast(`Job <strong>${jobNumber}</strong> berhasil dihapus.`, 'info');
    await refreshAllData(true);
  } catch (err) {
    showToast(`Gagal menghapus job: ${err.message}`, 'error');
  }
}

async function deleteJobFromModal() {
  const jobId = parseInt(document.getElementById('ejJobId').value, 10);
  const jobNum = document.getElementById('ejJobNumber').value.trim() || `ID #${jobId}`;
  if (!jobId) return;
  await confirmDeleteJob(null, jobId, jobNum);
}

async function openTemplateModal() {
  const templates = await apiFetch('/api/templates');
  const box = document.getElementById('templateListBox');
  box.innerHTML = templates
    .map(
      (t) => `
        <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 8px; border-bottom:1px solid var(--border); font-size:12px;">
          <div>
            <strong style="color:var(--accent); font-family:monospace;">${t.code}</strong>
            <span class="badge badge-info" style="margin:0 6px;">${t.stage_target}</span>
            <strong style="color:var(--text-strong);">${t.title}</strong>
          </div>
          <span class="badge ${t.is_auto ? 'badge-auto' : 'badge-pending'}">${t.is_auto ? 'AUTO' : 'MANUAL'}</span>
        </div>
      `
    )
    .join('');
  document.getElementById('templateModal').classList.add('open');
}

async function submitNewTemplate() {
  const code = document.getElementById('tplCode').value.trim();
  const category = document.getElementById('tplCategory').value;
  const stage_target = document.getElementById('tplStage').value;
  const title = document.getElementById('tplTitle').value.trim();
  const description = document.getElementById('tplDesc').value.trim();

  if (!code || !title) {
    showToast('Kode Item dan Judul Parameter wajib diisi.', 'warn');
    return;
  }

  try {
    await apiFetch('/api/templates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, category, stage_target, title, description, mandatory: true }),
    });
    showToast(`Item standar <strong>${code}</strong> ditambahkan ke Master Template!`, 'success');
    await openTemplateModal();
  } catch (err) {
    showToast(err.message, 'error');
  }
}
