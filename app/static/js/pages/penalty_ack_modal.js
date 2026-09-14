(function () {
    const overlay = document.getElementById('penalty-ack-overlay');
    const bodyEl = document.getElementById('penalty-ack-body');
    const button = document.getElementById('penalty-ack-button');
    if (!overlay || !bodyEl || !button) return;

    const lang = overlay.dataset.lang || 'ja';
    const pendingUrl = overlay.dataset.pendingUrl;
    const ackUrl = overlay.dataset.ackUrl;
    const title = lang === 'ja' ? '管理者からの警告' : 'Warning from an administrator';
    const confirmLabel = lang === 'ja' ? '確認しました' : 'I understand';
    const emptyText = lang === 'ja' ? '警告内容を確認してください。' : 'Please review the warning.';

    const titleEl = overlay.querySelector('.penalty-ack-modal__title');
    if (titleEl) titleEl.textContent = title;
    button.textContent = confirmLabel;

    overlay.addEventListener('click', (event) => {
        event.stopPropagation();
    });

    function decrementBoardNotificationBadges(ackedCount) {
        const decrement = Math.max(0, Number(ackedCount) || 0);
        if (!decrement) return;
        document.querySelectorAll('[data-tab-id="board"] .nav-tab-badge, .notifications-badge').forEach((el) => {
            const current = parseInt(String(el.textContent || '').replace(/[^\d]/g, ''), 10);
            if (!Number.isFinite(current) || current <= decrement) {
                el.remove();
                return;
            }
            const next = current - decrement;
            el.textContent = next >= 100 ? '99+' : String(next);
        });
    }

    async function loadPending() {
        try {
            const response = await fetch(pendingUrl, { credentials: 'same-origin' });
            if (!response.ok) return;
            const data = await response.json();
            const items = data.items || [];
            if (!items.length) return;
            overlay.dataset.pendingCount = String(items.length);
            bodyEl.innerHTML = items.map((item) => {
                const text = (item.warning_text || emptyText).replace(/</g, '&lt;').replace(/>/g, '&gt;');
                return `<section class="penalty-ack-modal__item"><pre class="penalty-ack-modal__text">${text}</pre></section>`;
            }).join('');
            overlay.hidden = false;
            document.body.classList.add('modal-open');
            overlay.setAttribute('data-open', '1');
            button.focus();
        } catch (error) {
            console.error('Failed to load penalty warnings', error);
        }
    }

    button.addEventListener('click', async () => {
        button.disabled = true;
        try {
            const response = await fetch(ackUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: '{}',
            });
            if (!response.ok) throw new Error('ack failed');
            overlay.hidden = true;
            overlay.removeAttribute('data-open');
            document.body.classList.remove('modal-open');
            decrementBoardNotificationBadges(overlay.dataset.pendingCount);
        } catch (error) {
            button.disabled = false;
            alert(lang === 'ja' ? '確認の送信に失敗しました。時間をおいて再度お試しください。' : 'Failed to confirm. Please try again later.');
        }
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', loadPending);
    } else {
        loadPending();
    }
})();
