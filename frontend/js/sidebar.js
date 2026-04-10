import {
    listConversations,
    getConversation,
    deleteConversation,
    deleteAllConversations,
    renameConversation,
} from './api.js?v=11';
import { confirmDialog, promptDialog } from './modal.js?v=2';

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

    // Delete-all button (injected after the New Chat button)
    const deleteAllBtn = document.createElement('button');
    deleteAllBtn.className = 'delete-all-btn';
    deleteAllBtn.innerHTML = '<i class="fas fa-trash"></i> <span>Delete all</span>';
    deleteAllBtn.title = 'Delete all conversations';
    deleteAllBtn.addEventListener('click', async () => {
        const confirmed = await confirmDialog({
            title: 'Delete all conversations?',
            message: 'All conversations and their messages will be permanently removed. This cannot be undone.',
            confirmText: 'Delete all',
            cancelText: 'Cancel',
            danger: true,
            icon: 'fa-trash',
        });
        if (!confirmed) return;
        const ok = await deleteAllConversations();
        if (ok) {
            activeId = null;
            onNewChat();
            await refresh();
        }
    });
    const sidebarHeader = document.querySelector('.sidebar-header');
    if (sidebarHeader) sidebarHeader.appendChild(deleteAllBtn);

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
            icon.className = conv.drawing_id ? 'fas fa-project-diagram conv-icon' : 'fas fa-message conv-icon';

            const titleWrap = document.createElement('div');
            titleWrap.className = 'conv-title-wrap';

            const title = document.createElement('span');
            title.className = 'conv-title';
            title.textContent = conv.title || 'Untitled chat';
            title.title = title.textContent;

            const date = document.createElement('span');
            date.className = 'conv-date';
            const d = new Date(conv.created_at);
            date.textContent = d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });

            titleWrap.appendChild(title);
            titleWrap.appendChild(date);

            const actions = document.createElement('div');
            actions.className = 'conv-actions';

            const rename = document.createElement('button');
            rename.className = 'conv-action-btn rename';
            rename.title = 'Rename conversation';
            rename.innerHTML = '<i class="fas fa-pen"></i>';
            rename.addEventListener('click', async (e) => {
                e.stopPropagation();
                const newTitle = await promptDialog({
                    title: 'Rename conversation',
                    message: 'Enter a new label for this chat:',
                    placeholder: 'My pipeline draft',
                    initialValue: conv.title || '',
                    confirmText: 'Save',
                    icon: 'fa-pen',
                    maxLength: 255,
                });
                if (!newTitle) return;
                const updated = await renameConversation(conv.id, newTitle);
                if (updated) await refresh();
            });

            const del = document.createElement('button');
            del.className = 'conv-action-btn delete';
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

            if (conv.drawing_id) {
                const openDrawer = document.createElement('a');
                openDrawer.className = 'conv-action-btn rename';
                openDrawer.title = 'Open in Drawer';
                openDrawer.href = `${window.IZS_BASE_PATH || ''}/drawer?drawing=${conv.drawing_id}`;
                openDrawer.innerHTML = '<i class="fas fa-project-diagram"></i>';
                openDrawer.addEventListener('click', (e) => e.stopPropagation());
                actions.appendChild(openDrawer);
            }
            actions.appendChild(rename);
            actions.appendChild(del);

            item.appendChild(icon);
            item.appendChild(titleWrap);
            item.appendChild(actions);

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
