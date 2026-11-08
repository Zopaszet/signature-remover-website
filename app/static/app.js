/* =========================================================
   Pembersih Karakter Tak Terlihat — app.js
   Vanilla JS. Calls relative endpoints: /api/health,
   /api/inspect, /api/clean. No frameworks, no external deps.
   ========================================================= */
'use strict';

(function () {
  // ---- constants -------------------------------------------------
  var OPTION_IDS = [
    'strip_ai_tells', 'nfkc', 'normalize_spaces', 'strip_emoji_glue',
    'strip_bidi', 'aggressive_homoglyphs', 'deep_scrub', 'verify'
  ];
  // deai_style is numeric (0|1|2), sent separately from the boolean keys.
  var DEAI_DEFAULT = 1;
  var MAX_BYTES = 25 * 1024 * 1024; // 25 MiB raw, per contract

  var KIND_LABELS = {
    text: 'Teks', image: 'Gambar', container: 'Dokumen',
    av: 'Audio/Video', unknown: 'Tidak dikenal'
  };

  var HIT_KIND_LABELS = {
    strip: 'dihapus', bidi: 'arah (bidi)', tag_chars: 'tag',
    variation_selector: 'variasi', zwj_family: 'perekat (ZWJ)',
    private_use: 'private use', noncharacter: 'noncharacter',
    reserved_ignorable: 'ignorable', space: 'spasi',
    confusable: 'homoglyph', other_cf: 'format'
  };

  var CONF_LABELS = { probable: 'cukup kuat', informational: 'informasi' };

  // ---- state -----------------------------------------------------
  var state = {
    mode: 'text',
    file: null,
    busy: false,
    lastResult: null,
    txtName: 'hasil.txt'
  };

  // ---- dom -------------------------------------------------------
  var $ = function (id) { return document.getElementById(id); };

  var els = {};

  function cacheDom() {
    els.tabs = [$('tab-text'), $('tab-file')];
    els.panels = { text: $('panel-text'), file: $('panel-file') };
    els.textarea = $('inputText');
    els.charCount = $('charCount');
    els.dropzone = $('dropzone');
    els.fileInput = $('fileInput');
    els.fileChip = $('fileChip');
    els.fileName = $('fileName');
    els.fileMeta = $('fileMeta');
    els.fileClear = $('fileClear');

    els.cleanBtn = $('cleanBtn');
    els.cleanLabel = els.cleanBtn.querySelector('.btn-label');
    els.inspectBtn = $('inspectBtn');
    els.resetBtn = $('resetBtn');

    els.deaiSelect = $('opt-deai_style');

    els.status = $('statusRegion');
    els.empty = $('emptyState');

    els.inspectResult = $('inspectResult');
    els.inspectHeadline = $('inspectHeadline');
    els.inspectBadge = $('inspectBadge');
    els.inspectSummary = $('inspectSummary');
    els.inspectMarks = $('inspectMarks');
    els.inspectReport = $('inspectReport');

    els.error = $('errorRegion');
    els.errorTitle = $('errorTitle');
    els.errorDetail = $('errorDetail');
    els.errorReport = $('errorReport');
    els.errorReportBody = $('errorReportBody');

    els.resultText = $('resultText');
    els.textSummary = $('textSummary');
    els.textDeaiNote = $('textDeaiNote');
    els.textStats = $('textStats');
    els.textVerify = $('textVerify');
    els.cleanedText = $('cleanedText');
    els.textOutputMeta = $('textOutputMeta');
    els.textMarks = $('textMarks');
    els.textReportBefore = $('textReportBefore');
    els.textReportAfter = $('textReportAfter');
    els.copyBtn = $('copyBtn');
    els.downloadTxtBtn = $('downloadTxtBtn');

    els.resultFile = $('resultFile');
    els.fileKindChip = $('fileKindChip');
    els.fileHeadline = $('fileHeadline');
    els.fileChangedBadge = $('fileChangedBadge');
    els.fileSummary = $('fileSummary');
    els.fileKind = $('fileKind');
    els.fileDownloadName = $('fileDownloadName');
    els.fileSize = $('fileSize');
    els.fileFindings = $('fileFindings');
    els.downloadFileBtn = $('downloadFileBtn');
    els.fileReportBefore = $('fileReportBefore');
    els.fileReportAfter = $('fileReportAfter');

    els.health = $('health');
    els.healthText = $('healthText');
  }

  // ---- small helpers --------------------------------------------
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text != null) { n.textContent = String(text); }
    return n;
  }

  function setText(node, text) {
    node.textContent = text == null ? '' : String(text);
  }

  function show(node) { if (node) { node.hidden = false; } }
  function hide(node) { if (node) { node.hidden = true; } }

  function formatBytes(n) {
    if (n == null || isNaN(n)) { return '—'; }
    var v = Number(n);
    var units = ['B', 'KB', 'MB', 'GB'];
    var i = 0;
    while (v >= 1024 && i < units.length - 1) { v = v / 1024; i += 1; }
    var shown = (v < 10 && i > 0) ? v.toFixed(1) : String(Math.round(v));
    return shown + ' ' + units[i];
  }

  function baseName(p) {
    if (typeof p !== 'string') { return p; }
    var s = p.replace(/\\/g, '/');
    var i = s.lastIndexOf('/');
    return i >= 0 ? s.slice(i + 1) : s;
  }

  // Scrub host paths before showing raw reports (defensive, matches contract).
  function scrub(value, key) {
    if (Array.isArray(value)) { return value.map(function (v) { return scrub(v); }); }
    if (value && typeof value === 'object') {
      var out = {};
      Object.keys(value).forEach(function (k) { out[k] = scrub(value[k], k); });
      return out;
    }
    if (typeof value === 'string' &&
        (key === 'path' || key === 'input' || key === 'output')) {
      return baseName(value);
    }
    return value;
  }

  function pretty(value) {
    if (value === undefined) { return 'Tidak tersedia.'; }
    if (value === null) { return 'Tidak tersedia.'; }
    if (typeof value === 'object' && Object.keys(value).length === 0) {
      return 'Tidak ada data.';
    }
    try {
      return JSON.stringify(scrub(value), null, 2);
    } catch (e) {
      return String(value);
    }
  }

  function readOptions() {
    var opts = {};
    OPTION_IDS.forEach(function (id) {
      var input = $('opt-' + id);
      opts[id] = !!(input && input.checked);
    });
    opts.deai_style = readDeaiStyle();
    return opts;
  }

  // Numeric 0..2. Missing or unparsable falls back to 0 (Mati).
  function readDeaiStyle() {
    var sel = els.deaiSelect || $('opt-deai_style');
    if (!sel) { return 0; }
    var v = parseInt(String(sel.value), 10);
    if (isNaN(v)) { return 0; }
    if (v < 0) { return 0; }
    if (v > 2) { return 2; }
    return v;
  }

  function resetDeaiStyle() {
    if (!els.deaiSelect) { return; }
    els.deaiSelect.value = String(DEAI_DEFAULT);
  }

  // ---- health ----------------------------------------------------
  function setHealth(stateName, text) {
    els.health.setAttribute('data-state', stateName);
    setText(els.healthText, text);
  }

  function checkHealth() {
    fetch('/api/health', { headers: { 'Accept': 'application/json' } })
      .then(function (res) {
        if (!res.ok) { throw new Error('bad status'); }
        return res.json();
      })
      .then(function (data) {
        if (data && data.ok && data.repo === false) {
          setHealth('warn', 'Layanan aktif, tetapi repo pembersih tidak ditemukan');
        } else if (data && data.ok) {
          var ver = data.version ? ' · v' + data.version : '';
          setHealth('ok', 'Layanan siap' + ver);
        } else {
          setHealth('warn', 'Status layanan tidak diketahui');
        }
      })
      .catch(function () {
        setHealth('down', 'Layanan tidak terjangkau — jalankan server lebih dulu');
      });
  }

  // ---- status / error -------------------------------------------
  function setStatus(text, isOk) {
    els.status.classList.toggle('is-ok', !!isOk);
    setText(els.status, text || '');
  }

  function clearError() {
    hide(els.error);
    setText(els.errorDetail, '');
    hide(els.errorReport);
    setText(els.errorReportBody, '');
  }

  function showError(title, detail, reportBefore) {
    setText(els.errorTitle, title || 'Terjadi kesalahan');
    setText(els.errorDetail, detail || '');
    if (reportBefore && typeof reportBefore === 'object' &&
        Object.keys(reportBefore).length > 0) {
      setText(els.errorReportBody, pretty(reportBefore));
      show(els.errorReport);
    } else {
      hide(els.errorReport);
    }
    show(els.error);
  }

  function sentence(text) {
    var s = String(text == null ? '' : text).trim();
    if (!s) { return ''; }
    if (!/[.!?]$/.test(s)) { s += '.'; }
    return s;
  }

  function httpMessage(status) {
    if (status === 400) { return 'Permintaan tidak valid.'; }
    if (status === 413) { return 'Input terlalu besar (maksimum 25 MB).'; }
    if (status === 422) { return 'Format file tidak dikenali.'; }
    if (status === 500) { return 'Terjadi kesalahan di server.'; }
    if (status === 0) { return 'Tidak bisa menghubungi server.'; }
    return 'Permintaan gagal (HTTP ' + status + ').';
  }

  // ---- networking -----------------------------------------------
  function postJSON(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify(body)
    }).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (e) { data = null; }
        }
        if (!res.ok) {
          var msg = (data && data.error) ? data.error : httpMessage(res.status);
          var err = new Error(msg);
          err.status = res.status;
          err.payload = data;
          throw err;
        }
        if (data && data.ok === false) {
          var e2 = new Error(data.error || 'Operasi gagal.');
          e2.status = res.status;
          e2.payload = data;
          throw e2;
        }
        if (!data) {
          var e3 = new Error('Respons server tidak dapat dibaca.');
          e3.status = res.status;
          throw e3;
        }
        return data;
      });
    }).catch(function (err) {
      if (err && err.status === undefined) {
        var off = new Error('Tidak bisa menghubungi server. Pastikan server berjalan.');
        off.status = 0;
        throw off;
      }
      throw err;
    });
  }

  function bytesToBase64(bytes) {
    var bin = '';
    var chunk = 0x8000;
    for (var i = 0; i < bytes.length; i += chunk) {
      var slice = bytes.subarray(i, i + chunk);
      bin += String.fromCharCode.apply(null, slice);
    }
    return btoa(bin);
  }

  function base64ToBytes(b64) {
    var bin = atob(b64);
    var out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i += 1) { out[i] = bin.charCodeAt(i); }
    return out;
  }

  function downloadBlob(name, mime, payload) {
    var blob = (payload instanceof Uint8Array)
      ? new Blob([payload], { type: mime })
      : new Blob([payload], { type: mime });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = name || 'unduhan';
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  // ---- busy ------------------------------------------------------
  function setBusy(busy) {
    state.busy = busy;
    els.cleanBtn.disabled = busy;
    els.cleanBtn.classList.toggle('is-busy', busy);
    els.cleanBtn.setAttribute('aria-busy', busy ? 'true' : 'false');
    if (els.inspectBtn) { els.inspectBtn.disabled = busy; }
    if (els.cleanLabel) { els.cleanLabel.textContent = busy ? 'Membersihkan…' : 'Bersihkan'; }
    if (busy) { setStatus('Memproses… mohon tunggu.'); }
  }

  // ---- mode + input ---------------------------------------------
  function selectTab(mode, focus) {
    state.mode = mode;
    els.tabs.forEach(function (tab) {
      var isActive = tab.id === ('tab-' + mode);
      tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
      tab.tabIndex = isActive ? 0 : -1;
      if (isActive && focus) { tab.focus(); }
    });
    Object.keys(els.panels).forEach(function (key) {
      els.panels[key].hidden = (key !== mode);
    });
  }

  function onTabKeydown(ev) {
    var idx = els.tabs.indexOf(ev.currentTarget);
    var last = els.tabs.length - 1;
    var next = null;
    if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') {
      next = idx >= last ? 0 : idx + 1;
    } else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') {
      next = idx <= 0 ? last : idx - 1;
    } else if (ev.key === 'Home') {
      next = 0;
    } else if (ev.key === 'End') {
      next = last;
    }
    if (next == null) { return; }
    ev.preventDefault();
    selectTab(els.tabs[next].id.replace('tab-', ''), true);
  }

  function updateCharCount() {
    var n = els.textarea.value.length;
    setText(els.charCount, n.toLocaleString('id-ID') + ' karakter');
  }

  function updateFileChip() {
    if (!state.file) {
      hide(els.fileChip);
      return;
    }
    setText(els.fileName, state.file.name);
    setText(els.fileMeta, formatBytes(state.file.size) + ' · ' +
      (state.file.type || 'tipe tidak diketahui'));
    show(els.fileChip);
  }

  function pickFile(file) {
    if (!file) { return; }
    if (file.size > MAX_BYTES) {
      state.file = null;
      updateFileChip();
      showError('File terlalu besar',
        'Ukuran ' + formatBytes(file.size) + ' melebihi batas 25 MB.');
      return;
    }
    state.file = file;
    clearError();
    updateFileChip();
    setStatus('File siap: ' + file.name);
  }

  // ---- marks rendering ------------------------------------------
  function marksShell(title, count) {
    var wrap = el('div');
    var head = el('p', 'marks-head');
    head.appendChild(el('span', null, title));
    if (count != null) { head.appendChild(el('span', 'marks-count', count)); }
    wrap.appendChild(head);
    return wrap;
  }

  function buildHitTable(hits) {
    var table = el('table', 'marks-table');
    var thead = el('thead');
    var hr = el('tr');
    ['Karakter', 'Kode', 'Jenis', 'Keyakinan', 'Jumlah'].forEach(function (h) {
      hr.appendChild(el('th', null, h));
    });
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = el('tbody');
    hits.forEach(function (hit) {
      var tr = el('tr');
      var tdLabel = el('td');
      tdLabel.appendChild(el('span', null, hit.label || 'Karakter tak dikenal'));
      tr.appendChild(tdLabel);

      var tdCp = el('td');
      tdCp.appendChild(el('span', 'cp', hit.codepoint || '—'));
      tr.appendChild(tdCp);

      tr.appendChild(el('td', null, HIT_KIND_LABELS[hit.kind] || hit.kind || '—'));

      var conf = el('td');
      conf.appendChild(el('span', 'chip', CONF_LABELS[hit.confidence] || hit.confidence || '—'));
      tr.appendChild(conf);

      tr.appendChild(el('td', 'num', (hit.count != null ? hit.count : '—')));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  // Renders a report_before (text hits or file findings) into a container.
  function renderMarks(container, reportBefore) {
    container.textContent = '';

    if (!reportBefore || typeof reportBefore !== 'object' ||
        Object.keys(reportBefore).length === 0) {
      container.appendChild(el('p', 'marks-note', 'Tidak ada laporan sebelum pembersihan.'));
      return;
    }

    var hits = Array.isArray(reportBefore.hits) ? reportBefore.hits : null;
    if (hits && hits.length > 0) {
      container.appendChild(marksShell('Penanda terdeteksi sebelum pembersihan',
        hits.length + ' jenis'));
      container.appendChild(buildHitTable(hits));
      return;
    }

    // File-style reports: findings / C2PA / AI metadata / layer_a_total.
    var items = [];
    if (reportBefore.has_c2pa) { items.push('Metadata C2PA ditemukan.'); }
    if (reportBefore.has_ai_metadata) { items.push('Metadata penanda AI ditemukan.'); }
    if (typeof reportBefore.layer_a_total === 'number' && reportBefore.layer_a_total > 0) {
      items.push(reportBefore.layer_a_total + ' karier teks tak terlihat pada isi dokumen.');
    }
    var deep = reportBefore.deep_scrub;
    if (deep && typeof deep.total === 'number' && deep.total > 0) {
      items.push('Deep scrub: ' + deep.total + ' karakter tersembunyi sisa lapisan pertama.');
    }
    if (Array.isArray(reportBefore.findings)) {
      reportBefore.findings.forEach(function (f) { items.push(String(f)); });
    }

    if (items.length > 0) {
      container.appendChild(marksShell('Ditemukan sebelum pembersihan', items.length + ' butir'));
      var ul = el('ul', 'marks-list');
      items.forEach(function (t) { ul.appendChild(el('li', null, t)); });
      container.appendChild(ul);
      return;
    }

    var summary = reportBefore.summary;
    if (summary) {
      container.appendChild(el('p', 'marks-note', summary));
      return;
    }

    container.appendChild(el('p', 'marks-note',
      'Tidak ada penanda tak terlihat yang terdeteksi sebelum pembersihan.'));
  }

  // ---- text result ----------------------------------------------
  function statCard(label, value, opts) {
    var card = el('div', 'stat');
    card.appendChild(el('span', 'stat-label', label));
    var val = el('span', 'stat-value' + (opts && opts.cls ? ' ' + opts.cls : ''),
      value);
    card.appendChild(val);
    if (opts && opts.sub) { card.appendChild(el('span', 'stat-sub', opts.sub)); }
    return card;
  }

  function renderTextResult(data) {
    state.lastResult = data;
    hide(els.error);
    hide(els.resultFile);
    hide(els.empty);
    show(els.resultText);

    var report = (data.report && typeof data.report === 'object') ? data.report : {};

    setText(els.textSummary, data.summary || 'Pembersihan selesai.');

    var stats = report.stats || null;
    var before = stats ? stats.input_length : state.lastInputLength;
    var after = stats ? stats.output_length
      : (data.content_length != null ? data.content_length
        : (data.cleaned ? data.cleaned.length : 0));
    var removed = stats ? (stats.removed_count || 0) : 0;
    var replaced = stats ? (stats.replaced_count || 0) : 0;

    els.textStats.textContent = '';
    els.textStats.appendChild(statCard('Sebelum', before != null ? before.toLocaleString('id-ID') : '—', { sub: 'karakter' }));
    els.textStats.appendChild(statCard('Sesudah', after != null ? after.toLocaleString('id-ID') : '—', { sub: 'karakter' }));
    els.textStats.appendChild(statCard('Dihapus', removed.toLocaleString('id-ID'), { cls: 'accent', sub: 'karakter tak terlihat' }));
    els.textStats.appendChild(statCard('Diganti', replaced.toLocaleString('id-ID'), { cls: 'warn', sub: 'spasi / homoglyph' }));

    var deep = report.deep_scrub;
    if (deep && typeof deep.total === 'number' && deep.total > 0) {
      els.textStats.appendChild(statCard('Lapisan 2', deep.total.toLocaleString('id-ID'),
        { cls: 'accent', sub: 'karakter tersembunyi' }));
    }

    renderDeaiNote(report.deai_style);
    renderVerify(report.verify);

    var cleaned = (data.cleaned != null) ? data.cleaned : '';
    setText(els.cleanedText, cleaned);
    setText(els.textOutputMeta, cleaned.length.toLocaleString('id-ID') + ' karakter');

    renderMarks(els.textMarks, data.report_before);

    setText(els.textReportBefore, pretty(data.report_before));
    setText(els.textReportAfter, pretty(data.report));

    state.txtName = data.download_name || 'hasil.txt';
  }

  function renderDeaiNote(deai) {
    var note = els.textDeaiNote;
    if (!note) { return; }
    note.textContent = '';
    var total = (deai && typeof deai.total === 'number') ? deai.total : 0;
    if (total <= 0) {
      hide(note);
      return;
    }
    note.appendChild(el('strong', null, total.toLocaleString('id-ID')));
    note.appendChild(el('span', null, 'frasa dinetralkan'));
    show(note);
  }

  function renderVerify(verify) {
    var box = els.textVerify;
    if (!box) { return; }
    box.textContent = '';
    box.classList.remove('is-pass', 'is-warn');

    if (!verify || typeof verify !== 'object') {
      hide(box);
      return;
    }

    var clean = verify.clean === true;
    box.classList.add(clean ? 'is-pass' : 'is-warn');

    if (clean) {
      box.appendChild(el('span', null, 'Terverifikasi bersih · 0 residu tak terlihat'));
      show(box);
      return;
    }

    var residual = (typeof verify.residual === 'number') ? verify.residual : 0;
    box.appendChild(el('span', null,
      'Sisa ' + residual.toLocaleString('id-ID') + ' karakter tak terlihat'));

    var samples = Array.isArray(verify.samples) ? verify.samples : [];
    if (samples.length > 0) {
      var list = el('span', 'verify-samples');
      samples.forEach(function (s) {
        if (!s || !s.codepoint) { return; }
        var label = String(s.codepoint);
        if (typeof s.count === 'number') { label += ' ×' + s.count.toLocaleString('id-ID'); }
        list.appendChild(el('span', 'verify-sample', label));
      });
      if (list.childNodes.length > 0) { box.appendChild(list); }
    }

    show(box);
  }

  // ---- file result ----------------------------------------------
  function renderFileResult(data) {
    state.lastResult = data;
    hide(els.error);
    hide(els.resultText);
    hide(els.empty);
    show(els.resultFile);

    setText(els.fileKindChip, KIND_LABELS[data.kind] || 'File');
    setText(els.fileKind, KIND_LABELS[data.kind] || (data.kind || '—'));
    setText(els.fileSummary, data.summary || 'Selesai.');
    setText(els.fileDownloadName, data.download_name || '—');

    var size = (data.content_length != null) ? data.content_length
      : (state.file ? state.file.size : null);
    setText(els.fileSize, formatBytes(size));

    // changed / unchanged badge
    var changed = data.changed !== false;
    els.fileChangedBadge.hidden = false;
    els.fileChangedBadge.classList.toggle('is-changed', changed);
    els.fileChangedBadge.classList.toggle('is-unchanged', !changed);
    setText(els.fileChangedBadge, changed ? 'Berubah' : 'Tidak ada perubahan');

    setText(els.fileHeadline, changed
      ? 'File sudah dibersihkan'
      : 'Tidak ada yang perlu diubah');

    // Download button availability
    var hasBytes = typeof data.cleaned_base64 === 'string' && data.cleaned_base64.length > 0;
    var hasText = typeof data.cleaned === 'string' && data.cleaned.length > 0;
    els.downloadFileBtn.disabled = !(hasBytes || hasText);
    if (!(hasBytes || hasText)) {
      els.downloadFileBtn.textContent = 'Tidak ada hasil';
    } else {
      els.downloadFileBtn.textContent = 'Unduh hasil';
    }

    renderMarks(els.fileFindings, data.report_before);

    setText(els.fileReportBefore, pretty(data.report_before));
    setText(els.fileReportAfter, pretty(data.report));
  }

  // ---- actions ---------------------------------------------------
  function ensureTextHasContent() {
    var text = els.textarea.value;
    if (!text || text.length === 0) {
      showError('Teks masih kosong', 'Tempel teks terlebih dahulu, lalu klik Bersihkan.');
      els.textarea.focus();
      return null;
    }
    state.lastInputLength = text.length;
    return { text: text, options: readOptions() };
  }

  function buildFileBody() {
    if (!state.file) {
      showError('Belum ada file', 'Pilih file terlebih dahulu.');
      els.dropzone.focus();
      return null;
    }
    return state.file.arrayBuffer().then(function (buf) {
      var bytes = new Uint8Array(buf);
      return {
        filename: state.file.name,
        file_base64: bytesToBase64(bytes),
        options: readOptions()
      };
    });
  }

  function runClean() {
    if (state.busy) { return; }
    clearError();
    hide(els.empty);
    hide(els.inspectResult);

    var bodyPromise;
    if (state.mode === 'text') {
      var tb = ensureTextHasContent();
      if (!tb) { return; }
      bodyPromise = Promise.resolve(tb);
    } else {
      bodyPromise = buildFileBody();
      if (!bodyPromise) { return; }
    }

    setBusy(true);
    setStatus('Memproses… mohon tunggu.');

    bodyPromise
      .then(function (body) { return postJSON('/api/clean', body); })
      .then(function (data) {
        if (state.mode === 'text') { renderTextResult(data); }
        else { renderFileResult(data); }
        var summ = data.summary || 'Selesai.';
        setStatus(summ, true);
      })
      .catch(function (err) {
        handleCleanError(err);
      })
      .then(function () { setBusy(false); });
  }

  function handleCleanError(err) {
    var payload = err && err.payload;
    var status = err && err.status;
    var reportBefore = payload && payload.report_before ? payload.report_before : null;

    if (status === 0) {
      showError('Server tidak terjangkau', err.message);
    } else if (status === 422) {
      showError('Format tidak dikenali',
        sentence(err.message || 'Format file tidak dikenali.') +
        ' Jika isinya berupa teks, coba mode Teks.',
        reportBefore);
    } else if (status === 413) {
      showError('Input terlalu besar', 'Ukuran melebihi batas 25 MB.');
    } else if (status === 400) {
      showError('Permintaan tidak valid', sentence(err.message || httpMessage(400)));
    } else {
      showError('Pembersihan gagal', sentence(err.message || 'Terjadi kesalahan.'), reportBefore);
    }
    setStatus('');
  }

  function renderInspect(data) {
    hide(els.empty);
    show(els.inspectResult);

    var suspicious = !!(data && data.suspicious);
    setText(els.inspectHeadline, suspicious
      ? 'Penanda tak terlihat terdeteksi'
      : 'Tidak ada penanda tak terlihat');

    els.inspectBadge.hidden = false;
    els.inspectBadge.classList.toggle('is-changed', suspicious);
    els.inspectBadge.classList.toggle('is-unchanged', !suspicious);
    setText(els.inspectBadge, suspicious ? 'Ada penanda' : 'Bersih');

    setText(els.inspectSummary, (data && data.summary) || 'Pemeriksaan selesai.');
    renderMarks(els.inspectMarks, data && data.report);
    setText(els.inspectReport, pretty(data && data.report));
  }

  function runInspect() {
    if (state.busy) { return; }
    clearError();

    var bodyPromise;
    if (state.mode === 'text') {
      var tb = ensureTextHasContent();
      if (!tb) { return; }
      bodyPromise = Promise.resolve(tb);
    } else {
      bodyPromise = buildFileBody();
      if (!bodyPromise) { return; }
    }

    setBusy(true);
    setStatus('Memeriksa penanda…');

    bodyPromise
      .then(function (body) { return postJSON('/api/inspect', body); })
      .then(function (data) {
        renderInspect(data);
        var s = data && data.summary ? data.summary : 'Pemeriksaan selesai.';
        setStatus(s, !(data && data.suspicious));
      })
      .catch(function (err) {
        if (err && err.status === 0) {
          showError('Server tidak terjangkau', err.message);
        } else {
          showError('Pemeriksaan gagal', err.message || 'Terjadi kesalahan.');
        }
      })
      .then(function () { setBusy(false); });
  }

  function resetAll() {
    els.textarea.value = '';
    updateCharCount();
    resetDeaiStyle();
    state.file = null;
    updateFileChip();
    if (els.fileInput) { els.fileInput.value = ''; }
    state.lastResult = null;
    state.lastInputLength = 0;
    hide(els.resultText);
    hide(els.resultFile);
    hide(els.inspectResult);
    show(els.empty);
    clearError();
    setStatus('');
    if (state.mode === 'text') { els.textarea.focus(); }
    else { els.dropzone.focus(); }
  }

  function copyCleaned() {
    var text = els.cleanedText.textContent || '';
    var done = function () { setStatus('Teks bersih disalin ke papan klip.', true); };
    var fail = function () { setStatus('Gagal menyalin. Pilih teks secara manual.', false); };

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function () {
        legacyCopy(text) ? done() : fail();
      });
      return;
    }
    legacyCopy(text) ? done() : fail();
  }

  function legacyCopy(text) {
    try {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.top = '-1000px';
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch (e) {
      return false;
    }
  }

  function downloadTxt() {
    var data = state.lastResult;
    if (!data) { return; }
    var name = state.txtName || 'hasil.txt';
    downloadBlob(name, 'text/plain;charset=utf-8', data.cleaned != null ? data.cleaned : '');
    setStatus('Mengunduh ' + name + '.', true);
  }

  function downloadFile() {
    var data = state.lastResult;
    if (!data) { return; }
    var name = data.download_name || 'hasil';
    var mime = data.mime || 'application/octet-stream';
    try {
      if (typeof data.cleaned_base64 === 'string' && data.cleaned_base64.length > 0) {
        downloadBlob(name, mime, base64ToBytes(data.cleaned_base64));
        setStatus('Mengunduh ' + name + '.', true);
      } else if (typeof data.cleaned === 'string') {
        downloadBlob(name, mime.indexOf('text') === 0 ? mime : 'text/plain;charset=utf-8', data.cleaned);
        setStatus('Mengunduh ' + name + '.', true);
      } else {
        showError('Tidak ada hasil', 'Server tidak mengembalikan isi file untuk diunduh.');
      }
    } catch (e) {
      showError('Gagal menyiapkan unduhan', 'Isi file tidak dapat didekode.');
    }
  }

  // ---- wiring ----------------------------------------------------
  function wire() {
    els.tabs.forEach(function (tab, i) {
      tab.addEventListener('click', function () {
        selectTab(i === 0 ? 'text' : 'file', false);
      });
      tab.addEventListener('keydown', onTabKeydown);
    });

    els.textarea.addEventListener('input', function () {
      updateCharCount();
      if (!els.error.hidden) { clearError(); }
    });

    // dropzone: keyboard + drag + click
    els.dropzone.addEventListener('click', function () { els.fileInput.click(); });
    els.dropzone.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Spacebar') {
        ev.preventDefault();
        els.fileInput.click();
      }
    });
    els.fileInput.addEventListener('change', function () {
      pickFile(els.fileInput.files && els.fileInput.files[0]);
    });
    els.fileClear.addEventListener('click', function (ev) {
      ev.stopPropagation();
      state.file = null;
      els.fileInput.value = '';
      updateFileChip();
      els.dropzone.focus();
    });

    ['dragenter', 'dragover'].forEach(function (type) {
      els.dropzone.addEventListener(type, function (ev) {
        ev.preventDefault();
        els.dropzone.classList.add('is-drag');
      });
    });
    ['dragleave', 'drop'].forEach(function (type) {
      els.dropzone.addEventListener(type, function (ev) {
        ev.preventDefault();
        if (type === 'dragleave' && els.dropzone.contains(ev.relatedTarget)) { return; }
        els.dropzone.classList.remove('is-drag');
      });
    });
    els.dropzone.addEventListener('drop', function (ev) {
      var dt = ev.dataTransfer;
      if (dt && dt.files && dt.files.length) { pickFile(dt.files[0]); }
    });

    // Prevent the browser from navigating when a file misses the dropzone.
    window.addEventListener('dragover', function (ev) { ev.preventDefault(); });
    window.addEventListener('drop', function (ev) { ev.preventDefault(); });

    els.cleanBtn.addEventListener('click', runClean);
    els.inspectBtn.addEventListener('click', runInspect);
    els.resetBtn.addEventListener('click', resetAll);
    els.copyBtn.addEventListener('click', copyCleaned);
    els.downloadTxtBtn.addEventListener('click', downloadTxt);
    els.downloadFileBtn.addEventListener('click', downloadFile);

    // Ctrl/Cmd+Enter runs clean from the textarea.
    els.textarea.addEventListener('keydown', function (ev) {
      if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') {
        ev.preventDefault();
        runClean();
      }
    });
  }

  // ---- init ------------------------------------------------------
  function init() {
    cacheDom();
    wire();
    updateCharCount();
    selectTab('text', false);
    checkHealth();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Expose a tiny API for debugging / future wiring (no UI dependency).
  window.WatermarksUI = {
    inspect: runInspect,
    clean: runClean,
    reset: resetAll,
    getState: function () { return state; }
  };
})();
