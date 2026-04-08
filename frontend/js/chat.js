export function initChatUi(onSendMessage) {
    const chatHistory = document.getElementById('chatHistory');
    const userInput = document.getElementById('userInput');
    const sendMessageBtn = document.getElementById('sendMessageBtn');
    const errorMessage = document.getElementById('errorMessage');
    const errorText = document.getElementById('errorText');
    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    const exampleBtns = document.querySelectorAll('.example-btn');
    const resetChatBtn = document.getElementById('resetChatBtn');

    // Timer state for the in-progress generation
    let timerStart = 0;
    let timerInterval = null;

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

    // Reset Context (uses the stylized modal)
    if (resetChatBtn) {
        resetChatBtn.addEventListener('click', async () => {
            const { confirmDialog } = await import('./modal.js?v=1');
            const ok = await confirmDialog({
                title: 'Reset chat?',
                message: 'This will refresh the page and clear the current conversation.',
                confirmText: 'Reset',
                cancelText: 'Cancel',
                icon: 'fa-arrows-rotate',
                danger: true,
            });
            if (ok) window.location.reload();
        });
    }

    function appendUserMessage(text) {
        const bubble = document.createElement('div');
        bubble.className = 'chat-bubble user';
        bubble.textContent = text;
        chatHistory.appendChild(bubble);
        scrollToBottom();
    }

    function appendAiMessage(text, options = {}) {
        const bubble = document.createElement('div');
        bubble.className = 'chat-bubble ai';

        if (typeof marked !== 'undefined') {
            bubble.innerHTML = marked.parse(text);
        } else {
            bubble.textContent = text;
            bubble.style.whiteSpace = 'pre-wrap';
        }

        if (options.openResultButton) {
            const btn = document.createElement('button');
            btn.className = 'example-btn';
            btn.innerHTML = `<i class="fas fa-external-link-alt"></i> ${options.openResultButton.text}`;
            btn.style.marginTop = '16px';
            btn.style.display = 'inline-flex';
            btn.style.alignItems = 'center';
            btn.style.gap = '8px';
            btn.onclick = options.openResultButton.onClick;
            bubble.appendChild(btn);
        }

        chatHistory.appendChild(bubble);

        // Optional duration caption directly below the bubble
        if (typeof options.elapsedMs === 'number') {
            const caption = document.createElement('div');
            caption.className = 'bubble-caption';
            caption.innerHTML = `<i class="far fa-clock"></i> Generated in ${formatDuration(options.elapsedMs)}`;
            chatHistory.appendChild(caption);
        }

        // Optional inline "Approve / Yes" badge attached to this bubble.
        // We render it only on bot replies that come back with status CHATTING
        // (i.e. plan still being negotiated). The caller decides.
        if (options.showApproveButton) {
            const row = document.createElement('div');
            row.className = 'inline-approve-row';
            const btn = document.createElement('button');
            btn.className = 'inline-approve-btn';
            btn.innerHTML = '<i class="fas fa-check"></i> Yes / Approve plan';
            btn.addEventListener('click', () => {
                btn.disabled = true;
                userInput.value = 'approved';
                submitMessage();
            });
            row.appendChild(btn);
            chatHistory.appendChild(row);
        }

        scrollToBottom();
    }

    function formatDuration(ms) {
        if (ms < 1000) return `${ms} ms`;
        const s = ms / 1000;
        if (s < 60) return `${s.toFixed(1)} s`;
        const m = Math.floor(s / 60);
        const rem = (s - m * 60).toFixed(1);
        return `${m} m ${rem} s`;
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

        // Live timer caption right below the typing dots
        const caption = document.createElement('div');
        caption.className = 'bubble-caption';
        caption.id = 'liveTimerCaption';
        caption.innerHTML = '<i class="far fa-clock"></i> Generating… <span class="live-timer" id="liveTimerValue">0.0s</span>';
        chatHistory.appendChild(caption);

        timerStart = performance.now();
        const valueEl = document.getElementById('liveTimerValue');
        timerInterval = setInterval(() => {
            if (!valueEl) return;
            const elapsed = (performance.now() - timerStart) / 1000;
            valueEl.textContent = `${elapsed.toFixed(1)}s`;
        }, 100);

        scrollToBottom();
    }

    function removeTypingIndicator() {
        const indicator = document.getElementById('typingIndicator');
        if (indicator) indicator.remove();
        const caption = document.getElementById('liveTimerCaption');
        if (caption) caption.remove();
        if (timerInterval) {
            clearInterval(timerInterval);
            timerInterval = null;
        }
        const elapsedMs = timerStart ? Math.round(performance.now() - timerStart) : 0;
        timerStart = 0;
        return elapsedMs;
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

    function clearHistory(welcomeText) {
        chatHistory.innerHTML = '';
        if (welcomeText) {
            const bubble = document.createElement('div');
            bubble.className = 'chat-bubble ai mt-auto';
            bubble.textContent = welcomeText;
            chatHistory.appendChild(bubble);
        }
    }

    function loadMessages(messages) {
        chatHistory.innerHTML = '';
        for (const m of messages) {
            if (m.role === 'user') {
                appendUserMessage(m.content);
            } else {
                appendAiMessage(m.content);
            }
        }
        scrollToBottom();
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
        scrollToBottom,
        clearHistory,
        loadMessages,
    };
}
