// =========================================================================
// Content Finder
// =========================================================================
(function () {
    const cfg = document.getElementById('page-config').dataset;
    const hasProcessedData     = cfg.hasProcessedData === '1';
    const hasUsedContentFinder = cfg.hasUsedContentFinder === '1';
    const hasSeenWritePostPoll = cfg.hasSeenWritePostPoll === '1';

    let cfPollTimer = null;

    // -- Coach marks for first-time Content Finder users -------------------
    if (hasProcessedData && !hasUsedContentFinder) {
        // Coach mark 1: "Select a template" → dismiss overlay and focus dropdown
        document.getElementById('selectTemplateBtn').addEventListener('click', function() {
            document.getElementById('getContentOverlay').style.display = 'none';
            var select = document.getElementById('cfPostSelect');
            var focusTarget = select.parentElement.querySelector('.fancy-select-btn') || select;
            focusTarget.scrollIntoView({ behavior: 'smooth', block: 'center' });
            focusTarget.focus();
        });

        // Show coach mark 2 after user selects a template post (one-time)
        document.getElementById('cfPostSelect').addEventListener('change', function showRunCoach() {
            if (this.value) {
                document.getElementById('runSearchOverlay').style.display = 'flex';
                this.removeEventListener('change', showRunCoach);
            }
        });

        // Coach mark 2: "Close" dismiss
        document.getElementById('closeSearchCoachBtn').addEventListener('click', function() {
            document.getElementById('runSearchOverlay').style.display = 'none';
        });
    }

    // -- Load processed posts into dropdown --------------------------------
    (async function loadContentFinderPosts() {
        const select = document.getElementById('cfPostSelect');
        mountFancySelect(select);
        try {
            const resp = await fetch('/insights/content-finder/posts/');
            const data = await resp.json();
            if (data.success && data.posts.length > 0) {
                data.posts.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.post_id;
                    opt.textContent = p.title;
                    if (p.publish_date_iso) opt.dataset.date = p.publish_date_iso;
                    select.appendChild(opt);
                });
            }
        } catch (err) {
            console.error('Error loading content finder posts:', err);
        }
    })();

    // -- Post selection change ---------------------------------------------
    document.getElementById('cfPostSelect').addEventListener('change', async function() {
        const postId = this.value;
        const runBtn = document.getElementById('cfRunBtn');
        const postOptions = document.getElementById('cfPostOptions');

        runBtn.disabled = !postId;
        postOptions.style.display = postId ? 'block' : 'none';

        // Hide results from previous run
        document.getElementById('cfResults').style.display = 'none';
        document.getElementById('cfResultsAccordion').innerHTML = '';
    });

    // -- Two-stage polling: stop on awaiting_feedback, show plan modal;
    //    resume after confirm.
    function startContentFinderPolling(taskId) {
        if (cfPollTimer) clearInterval(cfPollTimer);
        cfPollTimer = setInterval(async () => {
            try {
                const resp = await fetch(`/insights/content-finder/status/${taskId}/`);
                const data = await resp.json();

                if (data.credits_used !== undefined) {
                    updateCreditsDisplay(data.credits_used, data.credits_quota);
                }

                if (data.status === 'awaiting_feedback') {
                    clearInterval(cfPollTimer);
                    cfPollTimer = null;
                    ProgressBar.complete({ containerId: 'cfProgressContainer', success: true, message: 'Plan ready' });
                    showPlanOverlay(taskId, data.plan_text || '');
                } else if (data.status === 'complete') {
                    clearInterval(cfPollTimer);
                    cfPollTimer = null;
                    ProgressBar.complete({ containerId: 'cfProgressContainer', success: true, message: 'Done!' });
                    document.getElementById('cfRunBtn').disabled = false;
                    renderContentFinderResults(data.result_data || {});
                    if (typeof DevPanel !== 'undefined' && data.dev_panel) {
                        DevPanel.show(data.dev_panel, 'Content Finder');
                    }
                    if (!hasSeenWritePostPoll) {
                        var contentFoundOverlay = document.getElementById('contentFoundOverlay');
                        if (contentFoundOverlay) contentFoundOverlay.style.display = 'flex';
                        var nextBtn = document.getElementById('nextFeatureContainer');
                        if (nextBtn) nextBtn.style.display = '';
                    }
                } else if (data.status === 'error') {
                    clearInterval(cfPollTimer);
                    cfPollTimer = null;
                    ProgressBar.complete({ containerId: 'cfProgressContainer', success: false, message: 'Error' });
                    document.getElementById('cfRunBtn').disabled = false;
                    showToast(GENERIC_ERROR_TOAST, 'danger');
                }
            } catch (err) {
                console.error('Content finder polling error:', err);
            }
        }, 3000);
    }

    function showPlanOverlay(taskId, planText) {
        const overlay = document.getElementById('cfPlanOverlay');
        const body = document.getElementById('cfPlanBody');
        const feedback = document.getElementById('cfPlanFeedback');
        const confirmBtn = document.getElementById('cfPlanConfirmBtn');
        const cancelBtn = document.getElementById('cfPlanCancelBtn');

        if (typeof marked !== 'undefined' && marked.parse && typeof DOMPurify !== 'undefined') {
            body.innerHTML = DOMPurify.sanitize(marked.parse(planText));
            body.style.whiteSpace = 'normal';
        } else {
            body.textContent = planText;
        }
        feedback.value = '';

        overlay.style.display = 'flex';

        const onConfirm = async () => {
            confirmBtn.disabled = true;
            cancelBtn.disabled = true;
            try {
                const data = await postJSON(`/insights/content-finder/confirm-plan/${taskId}/`, {
                    feedback: feedback.value || '',
                });
                if (!data.success) {
                    showToast(data.error || GENERIC_ERROR_TOAST, 'danger');
                    confirmBtn.disabled = false;
                    cancelBtn.disabled = false;
                    return;
                }
                overlay.style.display = 'none';
                ProgressBar.start({
                    containerId: 'cfProgressContainer',
                    subtext: 'Searching...',
                });
                startContentFinderPolling(taskId);
            } catch (err) {
                console.error('Error confirming plan:', err);
                showToast(GENERIC_ERROR_TOAST, 'danger');
                confirmBtn.disabled = false;
                cancelBtn.disabled = false;
            } finally {
                confirmBtn.removeEventListener('click', onConfirm);
                cancelBtn.removeEventListener('click', onCancel);
            }
        };

        const onCancel = () => {
            overlay.style.display = 'none';
            document.getElementById('cfRunBtn').disabled = false;
            confirmBtn.removeEventListener('click', onConfirm);
            cancelBtn.removeEventListener('click', onCancel);
        };

        confirmBtn.addEventListener('click', onConfirm);
        cancelBtn.addEventListener('click', onCancel);
    }

    // -- Run button --------------------------------------------------------
    document.getElementById('cfRunBtn').addEventListener('click', async function() {
        const postId = document.getElementById('cfPostSelect').value;
        if (!postId) return;

        const runBtn = this;
        runBtn.disabled = true;

        document.getElementById('cfResults').style.display = 'none';
        document.getElementById('cfResultsAccordion').innerHTML = '';

        try {
            const data = await postJSON('/insights/content-finder/run/', {
                post_id: postId,
            });

            if (!data.success) {
                showToast(data.error || GENERIC_ERROR_TOAST, 'danger');
                runBtn.disabled = false;
                return;
            }

            ProgressBar.start({
                containerId: 'cfProgressContainer',
                subtext: 'Building search plan...',
            });
            startContentFinderPolling(data.task_id);
        } catch (err) {
            console.error('Error starting content finder:', err);
            showToast(GENERIC_ERROR_TOAST, 'danger');
            runBtn.disabled = false;
        }
    });

    function renderContentFinderResults(resultData) {
        const accordion = document.getElementById('cfResultsAccordion');
        accordion.innerHTML = '';

        // Backend emits an ordered list of {section, links} so dispatch order
        // is preserved (JSONB would have dropped dict key order).
        const sections = Array.isArray(resultData) ? resultData : [];
        if (sections.length === 0) {
            accordion.innerHTML = '<p class="text-muted">No content recommendations were generated. All sections were either dismissed or had no historical link data.</p>';
            document.getElementById('cfResults').style.display = 'block';
            return;
        }

        sections.forEach((entry, idx) => {
            const sectionName = entry.section;
            const links = entry.links || [];
            const collapseId = `cfCollapse_${idx}`;
            const headerId = `cfHeader_${idx}`;
            const isFirst = idx === 0;

            let linksHtml = '';
            links.forEach(link => {
                const displayUrl = link.url && link.url.length > 60
                    ? escapeHtml(link.url.substring(0, 57)) + '...'
                    : escapeHtml(link.url);

                linksHtml += `
                    <div class="mb-3 pb-3 border-bottom d-flex align-items-center">
                        <div class="flex-grow-1">
                            <div class="fw-semibold">
                                <a href="${escapeHtml(link.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(link.title)}</a>
                            </div>
                            <div class="text-muted small">${escapeHtml(link.source)} &middot; ${escapeHtml(link.date)}</div>
                            <div class="mt-1">${escapeHtml(link.description)}</div>
                            <div class="mt-1 fst-italic text-muted small">${escapeHtml(link.relevance)}</div>
                        </div>
                        <!--
                        <div class="d-flex flex-column align-items-center ms-3" style="min-width:32px; gap:0.5rem;">
                            <button class="btn btn-sm p-0 cf-fb-btn" data-fb="up" data-url="${escapeHtml(link.url)}"
                                    data-title="${escapeHtml(link.title)}" data-source="${escapeHtml(link.source)}"
                                    data-date="${escapeHtml(link.date)}" data-desc="${escapeHtml(link.description)}"
                                    data-relevance="${escapeHtml(link.relevance)}"
                                    title="Thumbs up" style="color:#198754;"><span class="material-symbols-outlined" style="font-size:1.4rem;">thumb_up</span></button>
                            <button class="btn btn-sm p-0 cf-fb-btn" data-fb="down" data-url="${escapeHtml(link.url)}"
                                    data-title="${escapeHtml(link.title)}" data-source="${escapeHtml(link.source)}"
                                    data-date="${escapeHtml(link.date)}" data-desc="${escapeHtml(link.description)}"
                                    data-relevance="${escapeHtml(link.relevance)}"
                                    title="Thumbs down" style="color:#dc3545;"><span class="material-symbols-outlined" style="font-size:1.4rem;">thumb_down</span></button>
                        </div>
                        -->
                    </div>
                `;
            });

            accordion.innerHTML += `
                <div class="accordion-item">
                    <h2 class="accordion-header" id="${headerId}">
                        <button class="accordion-button ${isFirst ? '' : 'collapsed'}" type="button"
                                data-bs-toggle="collapse" data-bs-target="#${collapseId}"
                                aria-expanded="${isFirst}" aria-controls="${collapseId}">
                            ${escapeHtml(sectionName)} &mdash; ${links.length} link${links.length !== 1 ? 's' : ''}
                        </button>
                    </h2>
                    <div id="${collapseId}" class="accordion-collapse collapse ${isFirst ? 'show' : ''}"
                         aria-labelledby="${headerId}" data-bs-parent="#cfResultsAccordion">
                        <div class="accordion-body">
                            ${linksHtml}
                        </div>
                    </div>
                </div>
            `;
        });

        document.getElementById('cfResults').style.display = 'block';

        // Attach feedback button handlers
        document.querySelectorAll('.cf-fb-btn').forEach(btn => {
            btn.addEventListener('click', async function() {
                const fb = this.dataset.fb;
                const url = this.dataset.url;
                const row = this.closest('.d-flex.align-items-start');
                const siblingBtn = row.querySelector(`.cf-fb-btn[data-fb="${fb === 'up' ? 'down' : 'up'}"]`);

                // Determine if toggling off (clicking already-active button)
                const alreadyActive = this.classList.contains('cf-fb-active');

                try {
                    // If toggling off, we still send the opposite isn't great —
                    // we just re-send the same value (the server does update_or_create).
                    // But there's no "remove" action, so clicking the active one
                    // is a no-op visually; the DB keeps the last vote.
                    if (alreadyActive) return;

                    const resp = await postJSON(cfg.urlSubmitContentSearchFeedback, {
                        url: url,
                        feedback: fb,
                        title: this.dataset.title,
                        source: this.dataset.source,
                        date: this.dataset.date,
                        description: this.dataset.desc,
                        relevance: this.dataset.relevance,
                    });

                    if (resp.success) {
                        this.classList.add('cf-fb-active');
                        this.style.opacity = '1';
                        siblingBtn.classList.remove('cf-fb-active');
                        siblingBtn.style.opacity = '0.4';
                    } else {
                        showToast(GENERIC_ERROR_TOAST, 'danger');
                    }
                } catch {
                    showToast(GENERIC_ERROR_TOAST, 'danger');
                }
            });
        });
    }
})();
