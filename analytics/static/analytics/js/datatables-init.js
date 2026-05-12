// =========================================================================
// Shared state (also referenced by CSV download handlers below)
// =========================================================================
let allItems = [];
let allLinkItems = [];

// =========================================================================
// Truncate text helper (also used by Content Finder / Link Data Table)
// =========================================================================
function truncateText(text, maxLength) {
    if (!text) return '';
    if (text.length <= maxLength) return escapeHtml(text);
    const short = text.substring(0, maxLength);
    const safeShort = escapeHtml(short);
    const safeFull = escapeHtml(text);
    return '<span class="truncated-text" style="display:inline-flex;align-items:flex-start;gap:4px;">' +
        '<a href="#" class="text-toggle text-muted" onclick="toggleText(event, this)" style="font-size:0.75em;text-decoration:none;flex-shrink:0;line-height:1.5;">&#9654;</a>' +
        '<span>' +
            '<span class="text-short">' + safeShort + '</span>' +
            '<span class="text-full" style="display:none;">' + safeFull + '</span>' +
        '</span>' +
        '</span>';
}

function toggleText(e, el) {
    e.preventDefault();
    const wrapper = el.closest('.truncated-text');
    const short = wrapper.querySelector('.text-short');
    const full = wrapper.querySelector('.text-full');
    if (short.style.display !== 'none') {
        short.style.display = 'none';
        full.style.display = 'inline';
        el.innerHTML = '&#9660;';
    } else {
        short.style.display = 'inline';
        full.style.display = 'none';
        el.innerHTML = '&#9654;';
    }
}

// =========================================================================
// HTML escape helper (also used by Content Finder rendering)
// =========================================================================
function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// =========================================================================
// Data Table
// =========================================================================
function renderTable(items) {
    if ($.fn.DataTable.isDataTable('#sectionDataTable')) {
        $('#sectionDataTable').DataTable().destroy();
    }

    const tbody = document.getElementById('sectionDataTableBody');
    tbody.innerHTML = '';

    items.forEach(item => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${escapeHtml(item.post_title)}</td>
            <td data-order="${escapeHtml(item.post_date_sortable)}">${escapeHtml(item.post_date_display)}</td>
            <td>${escapeHtml(item.section_name)}</td>
            <td>${escapeHtml(item.section_title || '')}</td>
            <td class="text-end">${item.start_line}</td>
            <td class="text-end">${item.end_line}</td>
        `;
        tbody.appendChild(row);
    });

    const table = $('#sectionDataTable').DataTable({
        order: [[1, 'desc']],
        paging: false,
        scrollY: '500px',
        scrollCollapse: true
    });

    setTimeout(() => { table.columns.adjust().draw(); }, 0);
}

// =========================================================================
// Link Data Table
// =========================================================================
function renderLinkTable(items) {
    if ($.fn.DataTable.isDataTable('#linkDataTable')) {
        $('#linkDataTable').DataTable().destroy();
    }

    const tbody = document.getElementById('linkDataTableBody');
    tbody.innerHTML = '';

    items.forEach(item => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${escapeHtml(item.post_title)}</td>
            <td data-order="${escapeHtml(item.post_date_sortable)}">${escapeHtml(item.post_date_display)}</td>
            <td>${escapeHtml(item.section_name)}</td>
            <td>${truncateText(item.raw_url, 60)}</td>
            <td>${truncateText(item.description, 80)}</td>
            <td class="text-end">${item.rank_in_section}</td>
            <td class="text-end">${item.mean_ctr}</td>
            <td class="text-end">${item.mean_clicks}</td>
        `;
        tbody.appendChild(row);
    });

    const table = $('#linkDataTable').DataTable({
        order: [[1, 'desc']],
        paging: false,
        scrollY: '500px',
        scrollCollapse: true
    });

    setTimeout(() => { table.columns.adjust().draw(); }, 0);
}

// =========================================================================
// Initialize: load section + link data if the user has processed posts.
// The runtime check replaces the original `{% if has_processed_data %}`
// server-side conditional — the loader is now always shipped, but only runs
// when the page-config flag is set.
// =========================================================================
(function () {
    const cfg = document.getElementById('page-config').dataset;
    if (cfg.hasProcessedData !== '1') return;
    (async function loadData() {
        try {
            const [sectionResp, linkResp] = await Promise.all([
                fetch('/insights/load-processed-data/'),
                fetch('/insights/load-link-data/')
            ]);
            const sectionData = await sectionResp.json();
            const linkData = await linkResp.json();

            if (sectionData.success) {
                allItems = sectionData.items;
                renderTable(allItems);
            } else {
                console.error('Error loading section data:', sectionData.error);
            }

            if (linkData.success) {
                allLinkItems = linkData.items;
                renderLinkTable(allLinkItems);
            } else {
                console.error('Error loading link data:', linkData.error);
            }
        } catch (error) {
            console.error('Error loading data:', error);
        }
    })();
})();

// =========================================================================
// Download CSVs (helper in static/analytics/js/csv.js)
// =========================================================================
document.getElementById('downloadCsvBtn')?.addEventListener('click', function() {
    if (!allItems || allItems.length === 0) {
        showToast('No data to download.', 'warning');
        return;
    }
    downloadCsv(
        'section_data.csv',
        ['Post Title', 'Post Date', 'Section Name', 'Section Title', 'Start Line', 'End Line'],
        allItems.map(item => [
            item.post_title || '',
            item.post_date_display || '',
            item.section_name || '',
            item.section_title || '',
            item.start_line || 0,
            item.end_line || 0,
        ]),
    );
});

document.getElementById('downloadLinkCsvBtn')?.addEventListener('click', function() {
    if (!allLinkItems || allLinkItems.length === 0) {
        showToast('No link data to download.', 'warning');
        return;
    }
    downloadCsv(
        'link_data.csv',
        ['Post Title', 'Post Date', 'Section', 'URL', 'Description', 'Rank in Section', 'Mean CTR (%)', 'Mean Clicks'],
        allLinkItems.map(item => [
            item.post_title || '',
            item.post_date_display || '',
            item.section_name || '',
            item.raw_url || '',
            item.description || '',
            item.rank_in_section || 0,
            item.mean_ctr || 0,
            item.mean_clicks || 0,
        ]),
    );
});
