const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

async function studioFetch(url, options) {
  try {
    return await fetch(url, options);
  } catch {
    throw new Error("Cannot reach the studio server. Check your connection and that the backend is running, then try again.");
  }
}

function sessionExpired(response, token) {
  if (response.status === 401 && token) {
    window.dispatchEvent(new CustomEvent("studio:session-expired", { detail: token }));
  }
}

export async function api(path, { token, method = "GET", body, credentials = "omit" } = {}) {
  const response = await studioFetch(`${API_URL}${path}`, {
    method,
    credentials,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  sessionExpired(response, token);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      message = Array.isArray(payload.detail)
        ? payload.detail.map((item) => item.msg).join(", ")
        : payload.detail || message;
    } catch {
      // Keep the safe status message when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

export async function apiUpload(path, { token, file, fields = {} } = {}) {
  const form = new FormData();
  form.append("file", file);
  Object.entries(fields).forEach(([key, value]) => form.append(key, value));
  const response = await studioFetch(`${API_URL}${path}`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  sessionExpired(response, token);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      message = Array.isArray(payload.detail)
        ? payload.detail.map((item) => item.msg).join(", ")
        : payload.detail || message;
    } catch { /* Keep the HTTP fallback. */ }
    throw new Error(message);
  }
  return response.json();
}

export async function apiFile(path, token) {
  const response = await studioFetch(`${API_URL}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  sessionExpired(response, token);
  if (!response.ok) throw new Error(`Image could not load (${response.status})`);
  return response.blob();
}
