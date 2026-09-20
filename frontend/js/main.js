import { sendChatMessage, checkSession, logout, fetchSystemInfo } from './api.js?v=25';
import { initChatUi } from './chat.js?v=25';
import { initResultsUi } from './results.js?v=25';
import { initSidebar } from './sidebar.js?v=25';

// Auth guard: redirect to /login.html if no valid session.
// The <html> element has the `auth-pending` class set very early in <head>,
// which keeps the body invisible until we know the user is authenticated.
const currentUser = await checkSession();
if (!currentUser) {
    // checkSession already triggered the redirect; keep the body hidden.
    throw new Error('Not authenticated');
}
document.documentElement.classList.remove('auth-pending');

// Wire logout button
const logoutBtn = document.getElementById('logoutBtn');
if (logoutBtn) {
    logoutBtn.addEventListener('click', (e) => {
        e.preventDefault();
        logout();
    });
}

// Show username
const userLabel = document.getElementById('userLabel');
if (userLabel) {
    userLabel.textContent = currentUser.username;
}

// --- Session/conversation state ---
const generateSessionId = () => {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
        return crypto.randomUUID();
    }
    return Math.random().toString(36).substring(2, 15);
};

let currentSessionId = generateSessionId();
let currentConversationId = null;
const WELCOME = "Welcome! I am your Bioinformatics Pipeline Assistant. Describe the pipeline you'd like to build, or choose one of the examples above.";

const resultsContainer = document.getElementById('resultsContainer');
const closeResultsBtn = document.getElementById('closeResultsBtn');

// Initialize UI Modules
const resultsUi = initResultsUi();

if (closeResultsBtn) {
    closeResultsBtn.addEventListener('click', () => {
        resultsContainer.classList.remove('open');
    });
}

const handleSendMessage = async (text) => {
    chatUi.showTypingIndicator();
    chatUi.setStatus('active', 'Thinking...');

    try {
        if (text) window.__lastUserQuery = text;
        const response = await sendChatMessage(currentSessionId, text);
        const elapsedMs = chatUi.removeTypingIndicator();

        if (response.status === 'failed') {
            chatUi.appendErrorMessage(response.error || 'An unknown error occurred');
            chatUi.setStatus('error', 'API Error');
            return;
        }

        // Track the conversation id returned by the backend so the sidebar can highlight it
        if (response.conversation_id && response.conversation_id !== currentConversationId) {
            currentConversationId = response.conversation_id;
            sidebar.setActive(currentConversationId);
        }

        if (response.status === 'CHATTING') {
            const showApprove = Boolean(response.has_plan && response.selected_components && response.selected_components.length > 0);
            chatUi.appendAiMessage(response.reply, {
                elapsedMs,
                showApproveButton: showApprove,
                onApprove: async (btn) => {
                    chatUi.showTypingIndicator();
                    chatUi.setStatus('active', 'Building Pipeline...');
                    try {
                        const proceedRes = await sendChatMessage(currentSessionId, '', { action: 'approve' });
                        const proceedElapsedMs = chatUi.removeTypingIndicator();

                        if (proceedRes.status === 'failed') {
                            if (btn) {
                                btn.classList.add('failed');
                                btn.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Build Failed';
                            }
                            chatUi.appendErrorMessage(proceedRes.error || 'Pipeline generation failed');
                            chatUi.setStatus('error', 'Generation Error');
                            return;
                        }

                        if (proceedRes.status === 'APPROVED') {
                            if (btn) {
                                btn.classList.remove('failed');
                                btn.classList.add('done');
                                btn.innerHTML = '<i class="fas fa-check-circle"></i> Pipeline Built';
                            }
                            chatUi.appendAiMessage(proceedRes.reply || 'Pipeline generated and validated successfully!', {
                                elapsedMs: proceedElapsedMs,
                                openResultButton: {
                                    text: 'Open Pipeline Result',
                                    onClick: () => { resultsContainer.classList.add('open'); },
                                },
                            });
                            window.__lastComponents = (proceedRes.selected_components || []); window.__lastUserQuery = window.__lastUserQuery || '';
            if (proceedRes.nextflow_code) resultsUi.renderNextflow(proceedRes.nextflow_code);
                            if (proceedRes.mermaid_code) resultsUi.renderMermaid(proceedRes.mermaid_code);
                            resultsContainer.classList.add('open');
                            chatUi.setStatus('active', 'Pipeline Generated');
                        } else {
                            if (btn) {
                                btn.classList.add('done');
                                btn.innerHTML = '<i class="fas fa-check"></i> Plan Processed';
                            }
                            chatUi.appendAiMessage(proceedRes.reply, { elapsedMs: proceedElapsedMs });
                            chatUi.setStatus('active', 'Ready');
                        }
                        sidebar.refresh();
                    } catch (err) {
                        if (btn) {
                            btn.classList.add('failed');
                            btn.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Build Failed';
                        }
                        chatUi.removeTypingIndicator();
                        chatUi.appendErrorMessage('Failed to build pipeline: ' + err.message);
                        chatUi.setStatus('error', 'Build failed');
                    }
                },
            });
            chatUi.setStatus('active', 'Ready');
        } else if (response.status === 'APPROVED') {
            chatUi.appendAiMessage(response.reply || 'Pipeline generated successfully!', {
                elapsedMs,
                openResultButton: {
                    text: 'Open Pipeline Result',
                    onClick: () => { resultsContainer.classList.add('open'); },
                },
            });
            window.__lastComponents = (response.selected_components || []); window.__lastUserQuery = window.__lastUserQuery || '';
            if (response.nextflow_code) resultsUi.renderNextflow(response.nextflow_code);
            if (response.mermaid_code) resultsUi.renderMermaid(response.mermaid_code);
            resultsContainer.classList.add('open');
            chatUi.setStatus('active', 'Pipeline Generated');
        }

        // Refresh sidebar so a brand-new conversation appears (or title updates)
        sidebar.refresh();
    } catch (error) {
        chatUi.removeTypingIndicator();
        chatUi.appendErrorMessage('Failed to connect to Bioinformatics Pipeline Assistant: ' + error.message);
        chatUi.setStatus('error', 'Connection failed');
    }
};

const chatUi = initChatUi(handleSendMessage);
chatUi.setStatus('', 'Ready');

// --- Sidebar wiring ---
const sidebar = initSidebar({
    onNewChat: () => {
        currentSessionId = generateSessionId();
        currentConversationId = null;
        chatUi.clearHistory(WELCOME);
        chatUi.setStatus('', 'Ready');
        resultsContainer.classList.remove('open');
        const base = window.IZS_BASE_PATH || '';
        window.history.replaceState(null, '', `${base}/`);
    },
    onSelect: (conv) => {
        currentSessionId = conv.session_id;
        currentConversationId = conv.id;
        // Update URL
        const base = window.IZS_BASE_PATH || '';
        window.history.replaceState(null, '', `${base}/?chat=${conv.session_id}`);
        chatUi.loadMessages(conv.messages || [], {
            onOpenResults: (msg) => {
                if (msg.nextflow_code) resultsUi.renderNextflow(msg.nextflow_code);
                if (msg.mermaid_code) resultsUi.renderMermaid(msg.mermaid_code);
                resultsContainer.classList.add('open');
            },
        });
        chatUi.setStatus('active', 'Loaded');
        resultsContainer.classList.remove('open');
    },
});

await sidebar.refresh();

// Auto-load chat from URL param ?chat=SESSION_ID
const urlParams = new URLSearchParams(window.location.search);
const chatParam = urlParams.get('chat');
if (chatParam) {
    // Find the conversation by session_id and load it
    const { listConversations: listConvs, getConversation: getConv } = await import('./api.js?v=25');
    const convs = await listConvs();
    const match = convs.find(c => c.session_id === chatParam);
    if (match) {
        const detail = await getConv(match.id);
        currentSessionId = match.session_id;
        currentConversationId = match.id;
        sidebar.setActive(match.id);
        chatUi.loadMessages(detail.messages || [], {
            onOpenResults: (msg) => {
                if (msg.nextflow_code) resultsUi.renderNextflow(msg.nextflow_code);
                if (msg.mermaid_code) resultsUi.renderMermaid(msg.mermaid_code);
                resultsContainer.classList.add('open');
            },
        });
    }
}

// --- System stats dashboard ---
function barColor(pct) {
    if (pct < 60) return 'green';
    if (pct < 85) return 'yellow';
    return 'red';
}

function renderBar(pct) {
    const color = barColor(pct);
    return `<span class="stat-bar"><span class="stat-bar-fill ${color}" style="width:${pct}%"></span></span>`;
}

async function refreshStats() {
    const el = document.getElementById('systemStats');
    if (!el) return;
    try {
        const info = await fetchSystemInfo();
        if (info && typeof info === 'object') {
            window.__llmModel = info.llm_model || '';
            window.__activePlugin = info.active_plugin || '';
        }
        if (!info || typeof info !== 'object') {
            el.innerHTML = '';
            return;
        }

        let html = '';

        // 1. Model Chip (auto-hide if missing)
        if (info.llm_model && typeof info.llm_model === 'string') {
            const maxLen = (info.llm_server?.max_model_len && !isNaN(info.llm_server.max_model_len))
                ? ` (${Math.round(info.llm_server.max_model_len / 1024)}k)`
                : '';
            html += `<span class="stat-chip model-chip" title="Active Model: ${info.llm_model}${maxLen}"><i class="fas fa-microchip"></i>${info.llm_model}${maxLen}</span>`;
        }

        // 2. Physical GPU (auto-hide if null/error/missing)
        if (info.gpu && typeof info.gpu === 'object' && info.gpu.name && !isNaN(info.gpu.vram_used_mb) && !isNaN(info.gpu.vram_total_mb) && info.gpu.vram_total_mb > 0) {
            const g = info.gpu;
            const vramPct = Math.min(100, Math.max(0, Math.round(g.vram_used_mb / g.vram_total_mb * 100)));
            html += `<span class="stat-chip" title="GPU: ${g.name}"><i class="fas fa-bolt"></i>${g.name}</span>`;
            html += `<span class="stat-chip" title="VRAM: ${g.vram_used_mb} / ${g.vram_total_mb} MB (${vramPct}%)"><i class="fas fa-microchip"></i>VRAM ${(g.vram_used_mb/1024).toFixed(1)}/${(g.vram_total_mb/1024).toFixed(1)} GB ${renderBar(vramPct)}</span>`;
            if (g.temperature_c !== undefined && g.temperature_c !== null && !isNaN(g.temperature_c)) {
                html += `<span class="stat-chip"><i class="fas fa-thermometer-half"></i>${g.temperature_c}&deg;C</span>`;
            }
        }

        // 3. System RAM (auto-hide if null/error/missing)
        if (info.ram && typeof info.ram === 'object' && !isNaN(info.ram.used_mb) && !isNaN(info.ram.total_mb) && info.ram.total_mb > 0) {
            const r = info.ram;
            const pct = typeof r.percent === 'number' && !isNaN(r.percent) ? r.percent : Math.round(r.used_mb / r.total_mb * 100);
            const usedGb = (r.used_mb / 1024).toFixed(1);
            const totalGb = (r.total_mb / 1024).toFixed(1);
            html += `<span class="stat-chip" title="System RAM: ${r.used_mb} MB / ${r.total_mb} MB (${pct}%)"><i class="fas fa-memory"></i>RAM ${usedGb}/${totalGb} GB ${renderBar(pct)}</span>`;
        }

        // 4. Framework Git Commit (auto-hide if missing)
        if (info.framework && typeof info.framework === 'object' && info.framework.commit) {
            const f = info.framework;
            const commitUrl = f.repo_url ? `${f.repo_url}/commit/${f.commit}` : '#';
            html += `<a href="${commitUrl}" target="_blank" class="stat-chip" style="text-decoration:none; cursor:pointer;" title="ngsmanager framework commit"><i class="fab fa-github"></i>ngsmanager@${f.commit}</a>`;
        }

        el.innerHTML = html;
    } catch (err) {
        // Auto-hide silently on error
        el.innerHTML = '';
    }
}

refreshStats();
setInterval(refreshStats, 10000);

console.log('IZS AI chat generator loaded for user:', currentUser.username);


// --- Esempi a comparsa -------------------------------------------------
// Sono un punto di partenza: restano aperti su una conversazione vuota e si
// richiudono da soli al primo messaggio, quando lo spazio serve alla chat.
(() => {
    const toggle = document.getElementById('examplesToggle');
    const box = document.getElementById('examplesContainer');
    if (!toggle || !box) return;

    const count = box.querySelectorAll('.example-btn').length;
    const badge = toggle.querySelector('.examples-count');
    if (badge) badge.textContent = count;

    function setOpen(open, remember = true) {
        box.classList.toggle('collapsed', !open);
        toggle.classList.toggle('collapsed', !open);
        if (remember) {
            try { localStorage.setItem('izs_examples', open ? 'open' : 'closed'); } catch (e) {}
        }
    }

    toggle.addEventListener('click', () => setOpen(box.classList.contains('collapsed')));

    let pref = 'open';
    try { pref = localStorage.getItem('izs_examples') || 'open'; } catch (e) {}
    const hasConversation = !!document.querySelector('#chatHistory .message-user, #chatHistory .user-message');
    setOpen(pref !== 'closed' && !hasConversation, false);

    // al primo invio si tolgono di mezzo, senza sovrascrivere la scelta esplicita
    const send = document.getElementById('sendMessageBtn');
    if (send) send.addEventListener('click', () => setOpen(false, false), { once: true });
})();
