// ── Auth guard ──────────────────────────────────────────────────────────────
(async () => {
  const me = await api('/api/auth/me').catch(() => null);
  if (!me) return;
})();

// ── Logout ──────────────────────────────────────────────────────────────────
document.getElementById('logoutBtn').addEventListener('click', async () => {
  await api('/api/auth/logout', { method: 'POST' });
  window.location = '/index.html';
});

// ── Book ID from URL ─────────────────────────────────────────────────────────
const bookId = new URLSearchParams(window.location.search).get('id');
if (!bookId) { window.location = '/app.html'; }

// ── DOM refs ─────────────────────────────────────────────────────────────────
const bookTitle   = document.getElementById('bookTitle');
const bookMeta    = document.getElementById('bookMeta');
const generateBtn = document.getElementById('generateBtn');
const downloadBtn = document.getElementById('downloadBtn');
const deleteBtn   = document.getElementById('deleteBtn');
const chapterList = document.getElementById('chapterList');

// ── Load book ────────────────────────────────────────────────────────────────
let _book = null;

async function loadBook() {
  const book = await api(`/api/books/${bookId}`);
  if (!book) return;
  _book = book;

  bookTitle.textContent = book.title;
  document.title = `${book.title} — Audiolibri`;

  bookMeta.innerHTML = `
    <div class="item"><strong>Autore</strong> ${_esc(book.author || 'Sconosciuto')}</div>
    <div class="item"><strong>Pagine</strong> ${book.total_pages}</div>
    <div class="item"><strong>Capitoli</strong> ${book.total_chapters}</div>
    <div class="item"><span class="badge badge-${book.status}">${_statusLabel(book.status)}</span></div>
  `;

  generateBtn.style.display = book.status === 'ready_to_generate' ? 'inline-flex' : 'none';
  downloadBtn.style.display = book.status === 'completed' ? 'inline-flex' : 'none';
  downloadBtn.href = `/api/books/${bookId}/m4b`;

  _renderChapters(book.chapters || []);
}

function _renderChapters(chapters) {
  if (!chapters.length) {
    chapterList.innerHTML = '<div class="empty-state"><p>Nessun capitolo trovato.</p></div>';
    return;
  }

  chapterList.innerHTML = '';
  chapters.forEach(ch => {
    const item = document.createElement('div');
    item.className = 'chapter-item';
    item.dataset.chapterId = ch.id;

    const duration = ch.duration_seconds
      ? _formatDuration(ch.duration_seconds)
      : '';

    item.innerHTML = `
      <span class="chapter-num">${ch.order}</span>
      <div class="chapter-info">
        <h4>${_esc(ch.title)}</h4>
        <span>${ch.char_count.toLocaleString('it-IT')} caratteri${duration ? ' · ' + duration : ''}</span>
        ${ch.error_message ? `<br><span style="color:var(--danger);font-size:12px">${_esc(ch.error_message)}</span>` : ''}
      </div>
      <span class="badge badge-${ch.status}">${_chapterStatusLabel(ch.status)}</span>
      ${ch.status === 'completed' ? `
        <button class="btn btn-secondary btn-sm" onclick="playChapter('${ch.id}', '${_esc(ch.title).replace(/'/g, "\\'")}')">▶ Play</button>
      ` : ''}
      <button class="btn btn-secondary btn-sm" onclick="_openEditModal('${ch.id}', '${_esc(ch.title).replace(/'/g, "\\'")}')">Modifica</button>
      ${ch.status === 'completed' || ch.status === 'failed' ? `
        <button class="btn btn-secondary btn-sm" onclick="_regenerateChapter('${ch.id}')">Rigenera</button>
      ` : ''}
    `;

    chapterList.appendChild(item);
  });
}

// ── Generate ─────────────────────────────────────────────────────────────────
generateBtn.addEventListener('click', async () => {
  generateBtn.disabled = true;
  generateBtn.textContent = 'Avvio generazione…';
  try {
    await api(`/api/books/${bookId}/generate`, { method: 'POST' });
    await loadBook();
  } catch (err) {
    alert(`Errore: ${err.message}`);
    generateBtn.disabled = false;
    generateBtn.textContent = 'Genera audiolibro';
  }
});

// ── Delete ───────────────────────────────────────────────────────────────────
deleteBtn.addEventListener('click', async () => {
  if (!confirm('Eliminare questo libro e tutti i file audio?')) return;
  await api(`/api/books/${bookId}`, { method: 'DELETE' });
  window.location = '/app.html';
});

// ── Regenerate single chapter ─────────────────────────────────────────────────
async function _regenerateChapter(chapterId) {
  await api(`/api/books/${bookId}/regenerate/${chapterId}`, { method: 'POST' });
  await loadBook();
}

// ── Text edit modal ───────────────────────────────────────────────────────────
const textModal       = document.getElementById('textModal');
const modalChapterTitle = document.getElementById('modalChapterTitle');
const modalTextarea   = document.getElementById('modalTextarea');
const modalCancel     = document.getElementById('modalCancel');
const modalSave       = document.getElementById('modalSave');
let _editingChapterId = null;

async function _openEditModal(chapterId, title) {
  _editingChapterId = chapterId;
  modalChapterTitle.textContent = title;
  modalTextarea.value = 'Caricamento…';
  textModal.classList.add('open');

  const data = await api(`/api/chapters/${chapterId}/text`);
  if (data) modalTextarea.value = data.text_content;
}

modalCancel.addEventListener('click', () => textModal.classList.remove('open'));
textModal.addEventListener('click', (e) => {
  if (e.target === textModal) textModal.classList.remove('open');
});

modalSave.addEventListener('click', async () => {
  modalSave.disabled = true;
  modalSave.textContent = 'Salvataggio…';
  try {
    await api(`/api/chapters/${_editingChapterId}/text`, {
      method: 'PUT',
      body: JSON.stringify({ text_content: modalTextarea.value }),
    });
    textModal.classList.remove('open');
    await loadBook();
  } catch (err) {
    alert(`Errore: ${err.message}`);
  } finally {
    modalSave.disabled = false;
    modalSave.textContent = 'Salva';
  }
});

// ── Polling ───────────────────────────────────────────────────────────────────
loadBook();
setInterval(() => {
  if (_book && ['parsing', 'generating'].includes(_book.status)) {
    loadBook();
  }
}, 5000);

// ── Helpers ───────────────────────────────────────────────────────────────────
function _statusLabel(s) {
  return { uploading:'Caricamento', parsing:'Analisi', ready_to_generate:'Pronto',
           generating:'Generazione', completed:'Completato', failed:'Errore' }[s] || s;
}
function _chapterStatusLabel(s) {
  return { pending:'In attesa', generating:'Generazione', completed:'Pronto', failed:'Errore' }[s] || s;
}
function _formatDuration(secs) {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`;
}
function _esc(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
