const BASE_PATH = (typeof window !== 'undefined' && window.IZS_BASE_PATH && !window.IZS_BASE_PATH.includes('{{')) ? window.IZS_BASE_PATH : '';

// API base path. Defaults to "<base>/api", or "http://localhost:8080" when running frontend standalone on a dev port.
const API_BASE = (typeof window !== 'undefined' && window.IZS_API_BASE) || 
    (typeof window !== 'undefined' && (window.location.port === '9000' || window.location.port === '3000' || window.location.port === '5500') ? 'http://localhost:8080' : (BASE_PATH || ''));

function redirectToLogin() {
    window.location.href = BASE_PATH + '/login.html';
}

async function apiFetch(path, options = {}) {
    const res = await fetch(`${API_BASE}${path}`, {
        credentials: 'same-origin',
        ...options,
    });
    if (res.status === 401) {
        redirectToLogin();
        throw new Error('Unauthorized');
    }
    return res;
}

export async function checkSession() {
    try {
        const res = await fetch(`${API_BASE}/auth/me`, {
            credentials: 'same-origin',
            // Never let the browser cache the auth check — otherwise a stale
            // 401 from before the login flow can make login appear to fail
            // on the first try.
            cache: 'no-store',
        });
        if (!res.ok) {
            redirectToLogin();
            return null;
        }
        return await res.json();
    } catch (err) {
        redirectToLogin();
        return null;
    }
}

export async function logout() {
    try {
        await fetch(`${API_BASE}/auth/logout`, {
            method: 'POST',
            credentials: 'same-origin',
        });
    } catch (e) { /* ignore */ }
    redirectToLogin();
}

export async function listConversations() {
    try {
        const res = await apiFetch('/conversations');
        if (!res.ok) return [];
        return await res.json();
    } catch (e) {
        return [];
    }
}

export async function getConversation(id) {
    const res = await apiFetch(`/conversations/${id}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
}

export async function deleteConversation(id) {
    const res = await apiFetch(`/conversations/${id}`, { method: 'DELETE' });
    return res.ok;
}

export async function deleteAllConversations() {
    const res = await apiFetch('/conversations', { method: 'DELETE' });
    return res.ok;
}

export async function renameConversation(id, title) {
    const res = await apiFetch(`/conversations/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
    });
    if (!res.ok) return null;
    return await res.json();
}

export async function fetchSystemInfo() {
    try {
        const res = await apiFetch('/system-info');
        if (!res.ok) return null;
        return await res.json();
    } catch (e) {
        return null;
    }
}

export async function validatePipeline(nextflowCode) {
    try {
        const res = await apiFetch('/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nextflow_code: nextflowCode }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    } catch (e) {
        return { success: false, errors: [e.message] };
    }
}

export function showToast(message, icon = 'fa-check') {
    let toast = document.querySelector('.toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.className = 'toast';
        document.body.appendChild(toast);
    }
    toast.innerHTML = `<i class="fas ${icon}"></i> ${message}`;
    toast.classList.add('visible');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => toast.classList.remove('visible'), 2000);
}

export async function sendChatMessage(sessionId, message, options = {}) {
    try {
        const payload = {
            session_id: sessionId,
            message: message || '',
            action: options.action || null,
            execution_mode: options.execution_mode || 'interactive',
            generate_diagrams: options.generate_diagrams !== false,
        };

        const response = await apiFetch('/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            throw new Error(`HTTP error: ${response.status} ${response.statusText}`);
        }

        const data = await response.json();
        return data;
    } catch (error) {
        console.error('API call error:', error);
        return { status: 'failed', error: error.message };
    }
}

export async function publishPipeline(payload) {
    try {
        const res = await apiFetch('/publish', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        return data;
    } catch (e) {
        return { error: e.message };
    }
}
