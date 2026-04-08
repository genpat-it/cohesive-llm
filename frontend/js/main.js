import { sendChatMessage, checkSession, logout } from './api.js?v=9';
import { initChatUi } from './chat.js?v=8';
import { initResultsUi } from './results.js?v=4';
import { initSidebar } from './sidebar.js?v=2';

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
            chatUi.appendAiMessage(response.reply, {
                elapsedMs,
                showApproveButton: true,
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
            resultsUi.renderNextflow(response.nextflow_code);
            resultsUi.renderMermaid(response.mermaid_code);
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
    },
    onSelect: (conv) => {
        currentSessionId = conv.session_id;
        currentConversationId = conv.id;
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

console.log('IZS AI chat generator loaded for user:', currentUser.username);
