// Base path is set by the inline script in <head> based on window.location.
// Empty string at site root, "/llm" when served under a sub-path proxy, etc.
const BASE_PATH = (typeof window !== 'undefined' && window.IZS_BASE_PATH) || '';

// API base path. Defaults to "<base>/api". Override at runtime by setting
// window.IZS_API_BASE before the module loads.
const API_BASE = (typeof window !== 'undefined' && window.IZS_API_BASE) || (BASE_PATH + '/api');

function redirectToLogin() {
    window.location.href = BASE_PATH + '/login';
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
        const res = await fetch(`${API_BASE}/auth/me`, { credentials: 'same-origin' });
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

export async function renameConversation(id, title) {
    const res = await apiFetch(`/conversations/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
    });
    if (!res.ok) return null;
    return await res.json();
}

export async function sendChatMessage(sessionId, message) {
    try {
        const payload = {
            session_id: sessionId,
            message: message
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
