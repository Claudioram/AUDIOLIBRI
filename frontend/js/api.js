/**
 * Thin fetch wrapper that includes credentials (httpOnly cookie) and
 * redirects to /index.html on 401.
 */
async function api(endpoint, options = {}) {
  const res = await fetch(endpoint, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });

  if (res.status === 401) {
    window.location = '/index.html';
    return null;
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.clone().json();
      detail = body.detail || detail;
    } catch (_) {}
    throw new Error(`API error ${res.status}: ${detail}`);
  }

  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return res.json();
  }
  return res;  // caller handles non-JSON responses
}

/**
 * Upload a file with FormData (no Content-Type header so browser sets multipart boundary).
 */
async function apiUpload(endpoint, formData) {
  const res = await fetch(endpoint, {
    method: 'POST',
    credentials: 'include',
    body: formData,
  });

  if (res.status === 401) {
    window.location = '/index.html';
    return null;
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.clone().json();
      detail = body.detail || detail;
    } catch (_) {}
    throw new Error(`Upload error ${res.status}: ${detail}`);
  }

  return res.json();
}
