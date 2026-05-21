/**
 * LLM Dev Panel — local-only floating window that displays detailed
 * LLM call information (prompts, tokens, costs, timing) after workflows.
 */
const DevPanel = (function () {
    'use strict';

    let _panelEl = null;
    let _data = null;
    let _workflowName = '';
    let _minimized = false;

    // --- helpers ---

    function esc(str) {
        const d = document.createElement('div');
        d.textContent = str;
        return d.innerHTML;
    }

    function fmt$(amount) {
        return '$' + amount.toFixed(4);
    }

    function fmtTime(seconds) {
        return seconds.toFixed(1) + 's';
    }

    function fmtTokens(n) {
        return n.toLocaleString();
    }

    // --- panel lifecycle ---

    function _createPanel() {
        if (_panelEl) _panelEl.remove();

        const el = document.createElement('div');
        el.id = 'devPanel';
        el.style.cssText =
            'position:fixed;top:20px;right:20px;width:420px;max-height:80vh;' +
            'overflow-y:auto;background:#1e1e1e;color:#d4d4d4;border:1px solid #444;' +
            'border-radius:8px;z-index:10001;font-family:monospace;font-size:12px;' +
            'box-shadow:0 4px 24px rgba(0,0,0,.4);display:flex;flex-direction:column;';
        document.body.appendChild(el);
        _panelEl = el;
        return el;
    }

    function _renderHeader() {
        const total = _data.totals;
        return `
        <div id="devPanelHeader" style="display:flex;align-items:center;justify-content:space-between;
            padding:8px 12px;background:#2d2d2d;border-bottom:1px solid #444;cursor:pointer;
            border-radius:8px 8px 0 0;flex-shrink:0;user-select:none;">
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="color:#569cd6;font-weight:bold;">LLM Dev Panel</span>
                <span style="color:#888;">|</span>
                <span style="color:#dcdcaa;">${esc(_workflowName)}</span>
                <span style="color:#888;">|</span>
                <span style="color:#4ec9b0;">${fmt$(total.total_cost)}</span>
                <span style="color:#888;">|</span>
                <span style="color:#9cdcfe;">${fmtTime(total.wall_clock_seconds)}</span>
            </div>
            <div style="display:flex;gap:6px;">
                <button id="devPanelMin" title="Minimize / Maximize"
                    style="background:none;border:none;color:#d4d4d4;cursor:pointer;font-size:14px;padding:0 4px;">_</button>
                <button id="devPanelClose" title="Close"
                    style="background:none;border:none;color:#d4d4d4;cursor:pointer;font-size:14px;padding:0 4px;">&times;</button>
            </div>
        </div>`;
    }

    function _providerChip(provider) {
        // Subtle colored pill so OpenAI vs Anthropic calls are scannable at a glance.
        if (!provider) return '';
        const colors = { openai: '#10a37f', anthropic: '#cc785c' };
        const bg = colors[provider] || '#666';
        return '<span style="background:' + bg + ';color:#fff;border-radius:3px;'
            + 'padding:0 4px;font-size:10px;margin-right:4px;text-transform:uppercase;">'
            + esc(provider) + '</span>';
    }

    function _fellBackBanner(extra) {
        if (!extra || !extra.fell_back_from) return '';
        const from = esc(extra.fell_back_from);
        const fromProv = extra.fell_back_from_provider
            ? ' (' + esc(extra.fell_back_from_provider) + ')' : '';
        return '<div style="background:#3a2a1a;color:#ffb86c;border-left:3px solid #ffb86c;'
            + 'padding:4px 8px;margin-bottom:4px;font-size:11px;">'
            + '↩︎ fell back from ' + from + fromProv
            + '</div>';
    }

    function _renderCall(call, idx) {
        const id = 'dpc_' + idx;
        const extra = call.extra_info || {};
        return `
        <div style="border-bottom:1px solid #333;padding:8px 12px;">
            <div class="dp-toggle" data-target="${id}" style="cursor:pointer;display:flex;justify-content:space-between;align-items:center;">
                <span>
                    <span style="color:#569cd6;">[${idx + 1}]</span>
                    ${_providerChip(call.provider)}
                    <span style="color:#dcdcaa;">${esc(call.function_name)}</span>
                    <span style="color:#888;">(${esc(call.model)})</span>
                </span>
                <span>
                    <span style="color:#4ec9b0;">${fmt$(call.total_cost)}</span>
                    <span style="color:#888;">|</span>
                    <span style="color:#9cdcfe;">${fmtTime(call.runtime_seconds)}</span>
                    <span style="color:#888;margin-left:4px;">&#9660;</span>
                </span>
            </div>
            <div id="${id}" style="display:none;margin-top:6px;">
                ${_fellBackBanner(extra)}
                <div style="margin-bottom:4px;"><b style="color:#c586c0;">Provider:</b> ${esc(call.provider || '—')} &nbsp; <b style="color:#c586c0;">Model:</b> ${esc(call.model)}</div>
                ${_collapsible(id + '_inputs', 'Input Messages', _renderInputMessages(call, id))}
                <div style="margin-bottom:4px;"><b style="color:#c586c0;">Runtime:</b> ${fmtTime(call.runtime_seconds)}</div>
                ${_collapsible(id + '_out', 'Output', _pre(call.output_text))}
                ${_collapsible(id + '_in_usage', 'Input Usage & Cost', _usageBlock(call.input_usage, 'input'))}
                ${_collapsible(id + '_out_usage', 'Output Usage & Cost', _usageBlock(call.output_usage, 'output'))}
                <div style="margin-top:4px;"><b style="color:#4ec9b0;">Total Cost:</b> ${fmt$(call.total_cost)}</div>
            </div>
        </div>`;
    }

    function _renderInputMessages(call, idBase) {
        // Back-compat with old dev-panel JSON dumps that stored system/user separately.
        if (Array.isArray(call.input_messages)) {
            const items = call.input_messages;
            if (!items.length) return '<span style="color:#666;">(empty)</span>';
            return items.map(function (item, i) {
                const label = (item.label || 'item');
                return '<div style="margin-bottom:6px;">' +
                    '<div style="color:#dcdcaa;font-size:11px;margin-bottom:2px;">' +
                    '[' + (i + 1) + '] ' + esc(label) + '</div>' +
                    _pre(item.text || '') +
                    '</div>';
            }).join('');
        }
        return _pre(
            (call.system_prompt ? '[system]\n' + call.system_prompt + '\n\n' : '') +
            (call.user_prompt ? '[user]\n' + call.user_prompt : '')
        );
    }

    function _renderTotals() {
        const t = _data.totals;
        return `
        <div style="border-top:2px solid #569cd6;padding:8px 12px;">
            <div class="dp-toggle" data-target="dp_totals" style="cursor:pointer;display:flex;justify-content:space-between;align-items:center;">
                <span style="color:#569cd6;font-weight:bold;">Totals (${_data.calls.length} call${_data.calls.length !== 1 ? 's' : ''})</span>
                <span style="color:#888;">&#9660;</span>
            </div>
            <div id="dp_totals" style="display:block;margin-top:6px;">
                <div style="margin-bottom:4px;"><b style="color:#c586c0;">Overall Runtime:</b> ${fmtTime(t.wall_clock_seconds)}</div>
                ${_collapsible('dp_tot_in', 'Input Usage & Cost', _usageBlock(t.input_usage, 'input'))}
                ${_collapsible('dp_tot_out', 'Output Usage & Cost', _usageBlock(t.output_usage, 'output'))}
                <div style="margin-top:4px;font-size:13px;"><b style="color:#4ec9b0;">Total Cost:</b> ${fmt$(t.total_cost)}</div>
            </div>
        </div>`;
    }

    function _renderFooter() {
        return `
        <div style="padding:8px 12px;border-top:1px solid #444;flex-shrink:0;">
            <button id="devPanelDownload"
                style="background:#569cd6;color:#1e1e1e;border:none;border-radius:4px;
                padding:4px 12px;cursor:pointer;font-size:12px;font-family:monospace;">
                Download JSON
            </button>
        </div>`;
    }

    // --- reusable sub-components ---

    function _collapsible(id, label, content) {
        return `
        <div style="margin-bottom:4px;">
            <span class="dp-toggle" data-target="${id}" style="cursor:pointer;color:#569cd6;text-decoration:underline;">
                ${esc(label)} &#9660;
            </span>
            <div id="${id}" style="display:none;margin-top:2px;">${content}</div>
        </div>`;
    }

    function _pre(text) {
        if (!text) return '<span style="color:#666;">(empty)</span>';
        return `<pre style="white-space:pre-wrap;word-break:break-word;max-height:300px;overflow-y:auto;
            background:#111;padding:6px;border-radius:4px;margin:2px 0;font-size:11px;">${esc(text)}</pre>`;
    }

    function _usageBlock(usage, type) {
        if (type === 'input') {
            // Anthropic-only: cache_creation_tokens (cache writes) — render
            // only when nonzero so OpenAI rows don't get a confusing 0 line.
            const cacheCreate = usage.cache_creation_tokens || 0;
            const cacheCreateRow = cacheCreate
                ? '<div><span style="color:#9cdcfe;">Cache write:</span> ' + fmtTokens(cacheCreate) + '</div>'
                : '';
            return `
            <div style="padding-left:12px;">
                <div><span style="color:#9cdcfe;">New:</span> ${fmtTokens(usage.new_tokens)}</div>
                <div><span style="color:#9cdcfe;">Cached:</span> ${fmtTokens(usage.cached_tokens)}</div>
                ${cacheCreateRow}
                <div><span style="color:#9cdcfe;">Total:</span> ${fmtTokens(usage.total_tokens)}</div>
                <div><span style="color:#4ec9b0;">Cost:</span> ${fmt$(usage.cost)}</div>
            </div>`;
        }
        return `
        <div style="padding-left:12px;">
            <div><span style="color:#9cdcfe;">Reasoning:</span> ${fmtTokens(usage.reasoning_tokens)}</div>
            <div><span style="color:#9cdcfe;">Response:</span> ${fmtTokens(usage.response_tokens)}</div>
            <div><span style="color:#9cdcfe;">Total:</span> ${fmtTokens(usage.total_tokens)}</div>
            <div><span style="color:#4ec9b0;">Cost:</span> ${fmt$(usage.cost)}</div>
        </div>`;
    }

    // --- event wiring ---

    function _bindEvents() {
        // Toggle collapse/expand for any element with class dp-toggle
        _panelEl.addEventListener('click', function (e) {
            const toggle = e.target.closest('.dp-toggle');
            if (toggle) {
                const targetId = toggle.getAttribute('data-target');
                const targetEl = document.getElementById(targetId);
                if (targetEl) {
                    const isHidden = targetEl.style.display === 'none';
                    targetEl.style.display = isHidden ? 'block' : 'none';
                    // Swap arrow indicator on the toggle itself
                    const arrow = toggle.querySelector('span:last-child');
                    if (arrow && (arrow.innerHTML === '&#9660;' || arrow.textContent === '\u25BC' || arrow.textContent === '\u25B2')) {
                        arrow.textContent = isHidden ? '\u25B2' : '\u25BC';
                    }
                }
            }
        });

        // Minimize / maximize
        document.getElementById('devPanelMin').addEventListener('click', function (e) {
            e.stopPropagation();
            _minimized = !_minimized;
            const body = document.getElementById('devPanelBody');
            const footer = document.getElementById('devPanelFooter');
            if (body) body.style.display = _minimized ? 'none' : 'block';
            if (footer) footer.style.display = _minimized ? 'none' : 'block';
            this.textContent = _minimized ? '\u25A1' : '_';
            // Adjust border-radius when minimized
            _panelEl.style.borderRadius = _minimized ? '8px' : '8px';
        });

        // Close
        document.getElementById('devPanelClose').addEventListener('click', function (e) {
            e.stopPropagation();
            _panelEl.remove();
            _panelEl = null;
        });

        // Download JSON
        document.getElementById('devPanelDownload').addEventListener('click', function () {
            const blob = new Blob([JSON.stringify(_data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = _workflowName.toLowerCase().replace(/\s+/g, '_') + '_llm_report.json';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        });
    }

    // --- public API ---

    function show(data, workflowName) {
        _data = data;
        _workflowName = workflowName || 'Workflow';
        _minimized = false;

        const panel = _createPanel();

        let bodyHtml = '';
        data.calls.forEach(function (call, idx) {
            bodyHtml += _renderCall(call, idx);
        });
        bodyHtml += _renderTotals();

        panel.innerHTML =
            _renderHeader() +
            '<div id="devPanelBody" style="overflow-y:auto;flex:1;">' + bodyHtml + '</div>' +
            '<div id="devPanelFooter">' + _renderFooter() + '</div>';

        _bindEvents();
    }

    return { show: show };
})();
