import { sendChatMessage } from './api.js?v=6';
import { initChatUi } from './chat.js?v=5';
import { initResultsUi } from './results.js?v=4';

// Generate session ID once per page load
const generateSessionId = () => {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
        return crypto.randomUUID();
    }
    return Math.random().toString(36).substring(2, 15);
};

const sessionId = generateSessionId();

const resultsContainer = document.getElementById('resultsContainer');
const chatSection = document.getElementById('chatSection');
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
        const response = await sendChatMessage(sessionId, text);
        chatUi.removeTypingIndicator();
        
        if (response.status === 'failed') {
            chatUi.appendErrorMessage(response.error || 'An unknown error occurred');
            chatUi.setStatus('error', 'API Error');
            return;
        }
        
        if (response.status === 'CHATTING') {
            chatUi.appendAiMessage(response.reply);
            chatUi.setStatus('active', 'Ready');
        } else if (response.status === 'APPROVED') {
            chatUi.appendAiMessage(response.reply || 'Pipeline generated successfully!', {
                text: 'Open Pipeline Result',
                onClick: () => {
                    resultsContainer.classList.add('open');
                }
            });
            
            // Render Nextflow and Mermaid
            resultsUi.renderNextflow(response.nextflow_code);
            resultsUi.renderMermaid(response.mermaid_code);
            
            chatUi.setStatus('active', 'Pipeline Generated');
        }
    } catch (error) {
        chatUi.removeTypingIndicator();
        chatUi.appendErrorMessage('Failed to connect to Bioinformatics Pipeline Assistant: ' + error.message);
        chatUi.setStatus('error', 'Connection failed');
    }
};

const chatUi = initChatUi(handleSendMessage);
chatUi.setStatus('', 'Ready');

console.log('IZS AI chat generator loaded with session ID:', sessionId);
