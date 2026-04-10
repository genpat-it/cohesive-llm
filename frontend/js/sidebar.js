import {
    listConversations,
    getConversation,
    deleteConversation,
    deleteAllConversations,
    renameConversation,
    showToast,
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

    function miniPipelineSvg(nodes) {
        if (!nodes || nodes.length === 0) return '';
        const W = 240, H = 36;
        const boxW = 44, boxH = 20, gap = 6;
        const totalW = nodes.length * (boxW + gap) - gap;
        const startX = Math.max(4, (W - totalW) / 2);

        let svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" style="width:100%;height:36px;display:block;margin:4px 0;">`;

        nodes.forEach((n, i) => {
            const x = startX + i * (boxW + gap);
            const y = (H - boxH) / 2;
            const label = (n.tool || n.id.split('__').pop() || '?').slice(0, 7);

            // Box
            svg += `<rect x="${x}" y="${y}" width="${boxW}" height="${boxH}" rx="4" fill="#e6f2f8" stroke="#01679c" stroke-width="1"/>`;
            // Label
            svg += `<text x="${x + boxW/2}" y="${y + boxH/2 + 4}" text-anchor="middle" font-size="7" font-family="Inter,sans-serif" fill="#01679c" font-weight="500">${label}</text>`;
            // Arrow to next
            if (i < nodes.length - 1) {
                const ax = x + boxW + 1;
                const ay = H / 2;
                svg += `<line x1="${ax}" y1="${ay}" x2="${ax + gap - 2}" y2="${ay}" stroke="#01679c" stroke-width="1.2" marker-end="url(#arr)"/>`;
            }
        });

        svg += `<defs><marker id="arr" markerWidth="5" markerHeight="4" refX="4" refY="2" orient="auto"><path d="M0,0 L5,2 L0,4 Z" fill="#01679c"/></marker></defs>`;
        svg += '</svg>';
        return svg;
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

            // Mini pipeline preview for drawer conversations
            if (conv.drawing_nodes && conv.drawing_nodes.length > 0) {
                const preview = document.createElement('div');
                preview.className = 'conv-preview';
                preview.innerHTML = miniPipelineSvg(conv.drawing_nodes);
                titleWrap.appendChild(preview);
            }

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

            const copyLink = document.createElement('button');
            copyLink.className = 'conv-action-btn rename';
            copyLink.title = 'Copy link';
            copyLink.innerHTML = '<i class="fas fa-link"></i>';
            copyLink.addEventListener('click', async (e) => {
                e.stopPropagation();
                const base = window.IZS_BASE_PATH || '';
                const url = conv.drawing_id
                    ? `${window.location.origin}${base}/drawer?drawing=${conv.drawing_id}`
                    : `${window.location.origin}${base}/?chat=${conv.session_id}`;
                await navigator.clipboard.writeText(url);
                showToast('Link copied to clipboard', 'fa-link');
                // Update browser URL
                window.history.replaceState(null, '', url.replace(window.location.origin, ''));
            });
            actions.appendChild(copyLink);
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
