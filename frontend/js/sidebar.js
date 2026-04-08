import {
    listConversations,
    getConversation,
    deleteConversation,
} from './api.js?v=7';
import { confirmDialog } from './modal.js?v=1';

export function initSidebar({ onSelect, onNewChat }) {
    const listEl = document.getElementById('conversationsList');
    const emptyEl = document.getElementById('sidebarEmpty');
    const newChatBtn = document.getElementById('newChatBtn');
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('sidebar');

    let activeId = null;

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    if (newChatBtn) {
        newChatBtn.addEventListener('click', () => {
            activeId = null;
            highlightActive();
            onNewChat();
        });
    }

    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', () => {
            sidebar.classList.toggle('collapsed');
        });
    }

    function highlightActive() {
        listEl.querySelectorAll('.conv-item').forEach((el) => {
            el.classList.toggle('active', Number(el.dataset.id) === activeId);
        });
    }

    function renderList(conversations) {
        // remove existing items but keep the empty state element
        listEl.querySelectorAll('.conv-item').forEach((el) => el.remove());

        if (!conversations || conversations.length === 0) {
            emptyEl.style.display = 'block';
            return;
        }
        emptyEl.style.display = 'none';

        for (const conv of conversations) {
            const item = document.createElement('div');
            item.className = 'conv-item';
            item.dataset.id = conv.id;
            if (conv.id === activeId) item.classList.add('active');

            const icon = document.createElement('i');
            icon.className = 'fas fa-message conv-icon';

            const title = document.createElement('span');
            title.className = 'conv-title';
            title.textContent = conv.title || 'Untitled chat';
            title.title = title.textContent;

            const del = document.createElement('button');
            del.className = 'conv-delete';
            del.title = 'Delete conversation';
            del.innerHTML = '<i class="fas fa-trash"></i>';
            del.addEventListener('click', async (e) => {
                e.stopPropagation();
                const confirmed = await confirmDialog({
                    title: 'Delete conversation?',
                    message: `<strong>${escapeHtml(conv.title || 'Untitled chat')}</strong> and all its messages will be permanently removed. This cannot be undone.`,
                    confirmText: 'Delete',
                    cancelText: 'Cancel',
                    danger: true,
                    icon: 'fa-trash',
                });
                if (!confirmed) return;
                const ok = await deleteConversation(conv.id);
                if (ok) {
                    if (activeId === conv.id) {
                        activeId = null;
                        onNewChat();
                    }
                    await refresh();
                }
            });

            item.appendChild(icon);
            item.appendChild(title);
            item.appendChild(del);

            item.addEventListener('click', async () => {
                try {
                    const detail = await getConversation(conv.id);
                    activeId = conv.id;
                    highlightActive();
                    onSelect(detail);
                } catch (err) {
                    console.error('Failed to load conversation', err);
                }
            });

            listEl.appendChild(item);
        }
    }

    async function refresh() {
        const conversations = await listConversations();
        renderList(conversations);
    }

    function setActive(id) {
        activeId = id;
        highlightActive();
    }

    return { refresh, setActive };
}
