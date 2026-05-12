// =========================================================================
// FancySelect — custom dropdown that wraps a native <select>, rendering
// each option as "{title} ........ {relative date}" with a muted date.
// Backed by the real <select> so existing .value / change listeners work.
// =========================================================================
function formatRelativeDate(isoDate) {
    if (!isoDate) return '';
    const then = new Date(isoDate + 'T00:00:00');
    if (isNaN(then.getTime())) return '';
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const days = Math.round((today - then) / 86400000);
    if (days < 0) return 'Scheduled';
    if (days === 0) return 'Today';
    if (days === 1) return 'Yesterday';
    if (days < 7) return `${days}d ago`;
    if (days < 30) return `${Math.round(days / 7)}w ago`;
    if (days < 365) return `${Math.round(days / 30)}mo ago`;
    return `${Math.round(days / 365)}y ago`;
}

const STATUS_COLORS = {
    'Draft': '#cc9a00',
    'Scheduled': '#0d6efd',
    'Published': '#198754',
};

function mountFancySelect(selectEl) {
    if (!selectEl || selectEl.dataset.fancyMounted) return;
    selectEl.dataset.fancyMounted = '1';

    const wrap = document.createElement('div');
    wrap.className = 'fancy-select';
    selectEl.parentNode.insertBefore(wrap, selectEl);
    wrap.appendChild(selectEl);

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'form-select fancy-select-btn';
    btn.setAttribute('aria-haspopup', 'listbox');
    btn.setAttribute('aria-expanded', 'false');
    const label = document.createElement('span');
    label.className = 'fancy-select-label';
    btn.appendChild(label);
    wrap.appendChild(btn);

    const menu = document.createElement('div');
    menu.className = 'fancy-select-menu';
    menu.hidden = true;
    const scroll = document.createElement('div');
    scroll.className = 'fancy-select-menu-scroll';
    scroll.setAttribute('role', 'listbox');
    menu.appendChild(scroll);
    wrap.appendChild(menu);

    function syncLabel() {
        label.innerHTML = '';
        const opt = selectEl.selectedOptions[0];
        if (!opt || !opt.value) {
            label.textContent = selectEl.options[0] ? selectEl.options[0].textContent : '';
            btn.classList.add('is-placeholder');
            return;
        }
        btn.classList.remove('is-placeholder');

        const status = opt.dataset.status;
        if (status) {
            const titleSpan = document.createElement('span');
            titleSpan.textContent = opt.textContent;
            label.appendChild(titleSpan);
            const statusSpan = document.createElement('span');
            statusSpan.textContent = ' · ' + status;
            statusSpan.style.color = STATUS_COLORS[status] || '';
            statusSpan.style.fontWeight = '600';
            label.appendChild(statusSpan);
        } else {
            label.textContent = opt.textContent;
        }
    }

    function rebuildMenu() {
        scroll.innerHTML = '';
        const realOptions = Array.from(selectEl.options).filter(o => o.value);
        if (realOptions.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'fancy-select-empty';
            empty.textContent = 'No posts available';
            scroll.appendChild(empty);
            return;
        }
        realOptions.forEach((opt, idx) => {
            const item = document.createElement('div');
            item.className = 'fancy-select-item';
            item.setAttribute('role', 'option');
            item.dataset.value = opt.value;
            if (opt.value === selectEl.value) item.classList.add('is-active');

            const status = opt.dataset.status;
            const iso = opt.dataset.date;

            // Title + colored status badge sit together on the left so the
            // status abuts the title rather than the date.
            const titleGroup = document.createElement('span');
            titleGroup.className = 'fancy-select-item-titlegroup';

            const title = document.createElement('span');
            title.className = 'fancy-select-item-title';
            title.textContent = opt.textContent;
            titleGroup.appendChild(title);

            if (status) {
                const statusSpan = document.createElement('span');
                statusSpan.className = 'fancy-select-item-status';
                statusSpan.textContent = '· ' + status;
                statusSpan.style.color = STATUS_COLORS[status] || '';
                titleGroup.appendChild(statusSpan);
            }
            item.appendChild(titleGroup);

            if (iso) {
                const rel = formatRelativeDate(iso);
                const meta = document.createElement('span');
                meta.className = 'fancy-select-item-meta';
                // Prefix "Published " on the first item only when there's
                // no status badge already conveying it (i.e. the
                // template-post dropdown).
                meta.textContent = (idx === 0 && !status) ? `Published ${rel}` : rel;
                meta.title = new Date(iso + 'T00:00:00').toLocaleDateString('en-US',
                    { year: 'numeric', month: 'short', day: 'numeric' });
                item.appendChild(meta);
            }

            item.addEventListener('click', () => {
                selectEl.value = opt.value;
                selectEl.dispatchEvent(new Event('change', { bubbles: true }));
                close();
            });
            scroll.appendChild(item);
        });
    }

    function open() {
        rebuildMenu();
        menu.hidden = false;
        btn.setAttribute('aria-expanded', 'true');
        document.addEventListener('mousedown', onDocDown, true);
        document.addEventListener('keydown', onKeyDown);
    }
    function close() {
        menu.hidden = true;
        btn.setAttribute('aria-expanded', 'false');
        document.removeEventListener('mousedown', onDocDown, true);
        document.removeEventListener('keydown', onKeyDown);
    }
    function toggle() { menu.hidden ? open() : close(); }
    function onDocDown(e) { if (!wrap.contains(e.target)) close(); }
    function onKeyDown(e) { if (e.key === 'Escape') close(); }

    btn.addEventListener('click', toggle);

    // Re-render when the underlying <select> changes (programmatic repopulation)
    const mo = new MutationObserver(() => { syncLabel(); if (!menu.hidden) rebuildMenu(); });
    mo.observe(selectEl, { childList: true, subtree: true, attributes: true, attributeFilter: ['value'] });
    selectEl.addEventListener('change', syncLabel);

    syncLabel();
}
