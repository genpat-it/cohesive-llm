// Lightweight stylized confirm/alert modal — drop-in replacement for window.confirm.
// Returns a promise that resolves to true (confirmed) or false (cancelled).

let modalRoot = null;

function ensureRoot() {
    if (modalRoot) return modalRoot;
    modalRoot = document.createElement('div');
    modalRoot.className = 'modal-root';
    document.body.appendChild(modalRoot);
    return modalRoot;
}

export function confirmDialog({
    title = 'Confirm',
    message = 'Are you sure?',
    confirmText = 'Confirm',
    cancelText = 'Cancel',
    danger = false,
    icon = null,
} = {}) {
    return new Promise((resolve) => {
        const root = ensureRoot();

        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';

        const card = document.createElement('div');
        card.className = 'modal-card';

        const iconHtml = icon
            ? `<div class="modal-icon ${danger ? 'danger' : ''}"><i class="fas ${icon}"></i></div>`
            : '';

        card.innerHTML = `
            ${iconHtml}
            <div class="modal-body">
                <h3 class="modal-title">${title}</h3>
                <p class="modal-message">${message}</p>
            </div>
            <div class="modal-actions">
                <button class="modal-btn modal-btn-cancel" type="button">${cancelText}</button>
                <button class="modal-btn ${danger ? 'modal-btn-danger' : 'modal-btn-primary'}" type="button">${confirmText}</button>
            </div>
        `;

        overlay.appendChild(card);
        root.appendChild(overlay);

        // Animate in
        requestAnimationFrame(() => overlay.classList.add('visible'));

        const cancelBtn = card.querySelector('.modal-btn-cancel');
        const confirmBtn = card.querySelector('.modal-btn-cancel + .modal-btn');

        const cleanup = (result) => {
            overlay.classList.remove('visible');
            setTimeout(() => {
                overlay.remove();
                document.removeEventListener('keydown', onKey);
            }, 180);
            resolve(result);
        };

        const onKey = (e) => {
            if (e.key === 'Escape') cleanup(false);
            if (e.key === 'Enter') cleanup(true);
        };

        cancelBtn.addEventListener('click', () => cleanup(false));
        confirmBtn.addEventListener('click', () => cleanup(true));
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) cleanup(false);
        });
        document.addEventListener('keydown', onKey);

        // Focus the safer (cancel) button by default for destructive ops,
        // the confirm one otherwise.
        setTimeout(() => (danger ? cancelBtn : confirmBtn).focus(), 50);
    });
}
