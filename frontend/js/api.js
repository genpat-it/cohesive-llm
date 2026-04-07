// API base path. Same-origin via reverse proxy by default.
// Override at runtime with window.IZS_API_BASE if needed.
const API_BASE = (typeof window !== 'undefined' && window.IZS_API_BASE) || '/api';

export async function sendChatMessage(sessionId, message) {
    try {
        const payload = {
            session_id: sessionId,
            message: message
        };

        const response = await fetch(`${API_BASE}/chat`, {
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
