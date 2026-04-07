export function initChatUi(onSendMessage) {
    const chatHistory = document.getElementById('chatHistory');
    const userInput = document.getElementById('userInput');
    const sendMessageBtn = document.getElementById('sendMessageBtn');
    const errorMessage = document.getElementById('errorMessage');
    const errorText = document.getElementById('errorText');
    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    const exampleBtns = document.querySelectorAll('.example-btn');
    const quickYesBtn = document.getElementById('quickYesBtn');
    const resetChatBtn = document.getElementById('resetChatBtn');

    // Initialization
    sendMessageBtn.disabled = true;

    // Auto-resize textarea
    userInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
        sendMessageBtn.disabled = !this.value.trim();
    });

    // Handle submit
    const submitMessage = () => {
        const text = userInput.value.trim();
        if (!text) return;
        
        userInput.value = '';
        userInput.style.height = 'auto';
        sendMessageBtn.disabled = true;
        hideError();
        
        appendUserMessage(text);
        onSendMessage(text);
    };

    sendMessageBtn.addEventListener('click', submitMessage);

    userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            submitMessage();
        }
    });

    // Examples
    exampleBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const exampleText = btn.getAttribute('data-example');
            userInput.value = exampleText;
            userInput.focus();
            userInput.dispatchEvent(new Event('input'));
        });
    });

    // Quick Actions
    if (quickYesBtn) {
        quickYesBtn.addEventListener('click', () => {
            userInput.value = 'approved';
            submitMessage();
        });
    }

    // Reset Context
    if (resetChatBtn) {
        resetChatBtn.addEventListener('click', () => {
            if (confirm('Are you sure you want to reset the chat context? This will refresh the page.')) {
                window.location.reload();
            }
        });
    }

    function appendUserMessage(text) {
        const bubble = document.createElement('div');
        bubble.className = 'chat-bubble user';
        bubble.textContent = text;
        chatHistory.appendChild(bubble);
        scrollToBottom();
    }

    function appendAiMessage(text, buttonConfig = null) {
        const bubble = document.createElement('div');
        bubble.className = 'chat-bubble ai';
        
        
        if (typeof marked !== 'undefined') {
            bubble.innerHTML = marked.parse(text);
        } else {
            bubble.textContent = text;
            bubble.style.whiteSpace = 'pre-wrap';
        }
        
        if (buttonConfig) {
            const btn = document.createElement('button');
            btn.className = 'example-btn';
            btn.innerHTML = `<i class="fas fa-external-link-alt"></i> ${buttonConfig.text}`;
            btn.style.marginTop = '16px';
            btn.style.display = 'inline-flex';
            btn.style.alignItems = 'center';
            btn.style.gap = '8px';
            btn.onclick = buttonConfig.onClick;
            bubble.appendChild(btn);
        }
        
        chatHistory.appendChild(bubble);
        scrollToBottom();
    }

    function appendErrorMessage(text) {
        const bubble = document.createElement('div');
        bubble.className = 'chat-bubble error';
        bubble.innerHTML = `<i class="fas fa-exclamation-triangle"></i> ${text}`;
        chatHistory.appendChild(bubble);
        scrollToBottom();
    }

    function showTypingIndicator() {
        const bubble = document.createElement('div');
        bubble.className = 'chat-bubble ai typing-indicator';
        bubble.id = 'typingIndicator';
        bubble.innerHTML = `
            <div class="loading-dots">
                <span></span><span></span><span></span>
            </div>
        `;
        chatHistory.appendChild(bubble);
        scrollToBottom();
    }

    function removeTypingIndicator() {
        const indicator = document.getElementById('typingIndicator');
        if (indicator) indicator.remove();
    }

    function setStatus(type, message) {
        statusDot.className = 'status-dot';
        if (type) statusDot.classList.add(type);
        statusText.textContent = message;
    }

    function showError(message) {
        errorText.textContent = message;
        errorMessage.style.display = 'block';
    }

    function hideError() {
        errorMessage.style.display = 'none';
    }

    function scrollToBottom() {
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }
    
    // Initial scroll
    scrollToBottom();

    return {
        appendUserMessage,
        appendAiMessage,
        appendErrorMessage,
        showTypingIndicator,
        removeTypingIndicator,
        setStatus,
        showError,
        hideError,
        scrollToBottom
    };
}
