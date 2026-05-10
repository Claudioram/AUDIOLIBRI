// ── Auth guard ──────────────────────────────────────────────────────────────
(async () => {
  const me = await api('/api/auth/me').catch(() => null);
  if (!me) return; // api() already redirected to login
})();

// ── Logout ──────────────────────────────────────────────────────────────────
document.getElementById('logoutBtn').addEventListener('click', async () => {
  await api('/api/auth/logout', { method: 'POST' });
  window.location = '/index.html';
});

// ── Upload handling ─────────────────────────────────────────────────────────
const uploadBtn  = document.getElementById('uploadBtn');
const dropZone   = document.getElementById('dropZone');
const fileInput  = document.getElementById('fileInput');
const uploadProgress = document.getElementById('uploadProgress');
const uploadBar  = document.getElementById('uploadBar');
const uploadStatus = document.getElementById('uploadStatus');

uploadBtn.addEventListener('click', () => {
  dropZone.style.display = dropZone.style.display === 'none' ? 'block' : 'none';
});

dropZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) _uploadFile(fileInput.files[0]);
});

dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file && file.type === 'application/pdf') _uploadFile(file);
});

async function _uploadFile(file) {
  dropZone.style.display = 'none';
  uploadProgress.style.display = 'block';
  uploadBar.style.width = '10%';
  uploadStatus.textContent = `Caricamento di ${file.name}…`;

  try {
    const form = new FormData();
    form.append('file', file);
    const book = await apiUpload('/api/books/upload', form);
    uploadBar.style.width = '100%';
    uploadStatus.textContent = 'Analisi in corso…';
    setTimeout(() => { uploadProgress.style.display = 'none'; }, 1500);
    _renderBook(book, true);
  } catch (err) {
    uploadStatus.textContent = `Errore: ${err.message}`;
    uploadBar.style.background = 'var(--danger)';
  }
}

// ── Book list ────────────────────────────────────────────────────────────────
const bookGrid = document.getElementById('bookGrid');
const emptyState = document.getElementById('emptyState');

async function _loadBooks() {
  const books = await api('/api/books');
  if (!books) return;

  // Remove stale cards (keep upload progress)
  bookGrid.querySelectorAll('.book-card').forEach(n => n.remove());
  emptyState.style.display = books.length ? 'none' : 'block';

  books.forEach(b => _renderBook(b, false));
}

function _renderBook(book, prepend = false) {
  // Remove existing card for this book (to update it)
  const existing = bookGrid.querySelector(`[data-book-id="${book.id}"]`);
  if (existing) existing.remove();

  emptyState.style.display = 'none';

  const card = document.createElement('div');
  card.className = 'card book-card';
  card.dataset.bookId = book.id;

  const statusLabel = {
    uploading: 'Caricamento',
    parsing: 'Analisi PDF',
    ready_to_generate: 'Pronto',
    generating: 'Generazione audio',
    completed: 'Completato',
    failed: 'Errore',
  }[book.status] || book.status;

  card.innerHTML = `
    <div class="book-meta" style="flex:1; min-width:0">
      <h3>${_esc(book.title)}</h3>
      <p>${_esc(book.author || 'Autore sconosciuto')} &nbsp;·&nbsp; ${book.total_pages} pagine</p>
      ${book.total_chapters ? `<p style="margin-top:4px; font-size:12px; color:var(--muted)">${book.total_chapters} capitoli</p>` : ''}
      ${book.error_message ? `<p style="color:var(--danger);font-size:13px;margin-top:4px">${_esc(book.error_message)}</p>` : ''}
    </div>
    <div class="book-actions">
      <span class="badge badge-${book.status}">${statusLabel}</span>
    </div>
  `;

  card.addEventListener('click', () => {
    window.location = `/book.html?id=${book.id}`;
  });

  if (prepend) {
    bookGrid.insertBefore(card, bookGrid.firstChild);
  } else {
    bookGrid.appendChild(card);
  }
}

// ── Polling ──────────────────────────────────────────────────────────────────
_loadBooks();
setInterval(_loadBooks, 5000);

// ── Helpers ──────────────────────────────────────────────────────────────────
function _esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
