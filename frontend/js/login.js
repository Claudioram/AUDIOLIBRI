document.getElementById('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();

  const password = document.getElementById('password').value;
  const submitBtn = document.getElementById('submitBtn');
  const errorMsg = document.getElementById('errorMsg');

  submitBtn.disabled = true;
  submitBtn.textContent = 'Accesso in corso…';
  errorMsg.style.display = 'none';

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });

    if (res.ok) {
      window.location = '/app.html';
    } else if (res.status === 401) {
      errorMsg.textContent = 'Password errata. Riprova.';
      errorMsg.style.display = 'block';
    } else if (res.status === 429) {
      errorMsg.textContent = 'Troppi tentativi. Riprova tra 15 minuti.';
      errorMsg.style.display = 'block';
    } else {
      errorMsg.textContent = 'Errore del server. Riprova più tardi.';
      errorMsg.style.display = 'block';
    }
  } catch (err) {
    errorMsg.textContent = 'Errore di rete. Controlla la connessione.';
    errorMsg.style.display = 'block';
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Accedi';
  }
});
