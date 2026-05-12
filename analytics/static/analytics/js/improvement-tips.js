// =========================================================================
// Improvement Tips
// =========================================================================
(function () {
    const cfg = document.getElementById('page-config').dataset;
    const hasProcessedData      = cfg.hasProcessedData === '1';
    const hasUsedPostImprovement = cfg.hasUsedPostImprovement === '1';

    // -- Coach marks for first-time Improvement-Tips users -----------------
    if (hasProcessedData && !hasUsedPostImprovement) {
        // Review Your Draft: "Continue" dismiss
        document.getElementById('reviewDraftContinueBtn').addEventListener('click', function() {
            document.getElementById('reviewDraftOverlay').style.display = 'none';
        });

        // Review Improvement Tips: dismiss coach and stop flash on download click
        document.getElementById('reviewTipsDismissBtn').addEventListener('click', function() {
            document.getElementById('reviewTipsOverlay').style.display = 'none';
        });
        document.getElementById('itDownloadBtn').addEventListener('click', function() {
            this.classList.remove('coach-mark-flash');
        });
    }

    let itPollTimer = null;
    let itAllPosts = [];

    function populateItDropdown(posts) {
        const select = document.getElementById('itPostSelect');
        // Clear existing options except placeholder
        select.length = 1;
        select.value = '';
        posts.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.post_id;
            opt.textContent = p.title;
            if (p.date_iso) opt.dataset.date = p.date_iso;
            opt.dataset.status = p.status || 'Published';
            select.appendChild(opt);
        });
        // Reset selection state
        document.getElementById('itPostOptions').style.display = 'none';
        document.getElementById('itRunBtn').disabled = true;
    }

    // Load all posts into dropdown
    (async function loadImprovementTipsPosts() {
        mountFancySelect(document.getElementById('itPostSelect'));
        try {
            const resp = await fetch('/insights/improvement-tips/posts/');
            const data = await resp.json();
            if (data.success && data.posts.length > 0) {
                itAllPosts = data.posts;
                populateItDropdown(itAllPosts);
            }
        } catch (err) {
            console.error('Error loading improvement tips posts:', err);
        }
    })();

    // Drafts only checkbox
    document.getElementById('itDraftsOnly').addEventListener('change', function() {
        if (this.checked) {
            populateItDropdown(itAllPosts.filter(p => p.status === 'Draft'));
        } else {
            populateItDropdown(itAllPosts);
        }
    });

    // Post selection change
    document.getElementById('itPostSelect').addEventListener('change', function() {
        const postId = this.value;
        const runBtn = document.getElementById('itRunBtn');
        const postOptions = document.getElementById('itPostOptions');
        const downloadSection = document.getElementById('itDownloadSection');
        const downloadBtn = document.getElementById('itDownloadBtn');

        runBtn.disabled = !postId;
        postOptions.style.display = postId ? 'block' : 'none';
        downloadSection.style.display = 'none';
        downloadBtn.removeAttribute('href');
    });

    // Run button
    document.getElementById('itRunBtn').addEventListener('click', async function() {
        const postSelect = document.getElementById('itPostSelect');
        const postId = postSelect.value;
        if (!postId) return;

        const postObj = itAllPosts.find(p => p.post_id === postId);
        const postName = postObj ? postObj.title : postSelect.options[postSelect.selectedIndex].text;
        const runBtn = this;
        runBtn.disabled = true;
        const downloadSection = document.getElementById('itDownloadSection');
        const downloadBtn = document.getElementById('itDownloadBtn');
        downloadSection.style.display = 'none';
        downloadBtn.removeAttribute('href');

        try {
            const data = await postJSON('/insights/improvement-tips/run/', { post_id: postId });

            if (!data.success) {
                showToast(data.error || GENERIC_ERROR_TOAST, 'danger');
                runBtn.disabled = false;
                return;
            }

            // Start progress bar and polling
            ProgressBar.start({
                containerId: 'itProgressContainer',
                subtext: 'Generating improvement tips...',
            });
            if (itPollTimer) clearInterval(itPollTimer);
            itPollTimer = pollTask('/insights/improvement-tips/status/', data.task_id, {
                progressContainerId: 'itProgressContainer',
                runBtnId: 'itRunBtn',
                devPanelName: 'Improvement Tips',
                onComplete: (data, taskId) => {
                    downloadBtn.href = `/insights/improvement-tips/download/${taskId}/`;
                    document.getElementById('itDownloadPostName').textContent = postName;
                    downloadSection.style.display = '';
                    if (!hasUsedPostImprovement) {
                        // Show coach and flash the download button
                        document.getElementById('reviewTipsOverlay').style.display = 'flex';
                        downloadBtn.classList.add('coach-mark-flash');
                    }
                },
            });
        } catch (err) {
            console.error('Error starting improvement tips:', err);
            showToast(GENERIC_ERROR_TOAST, 'danger');
            runBtn.disabled = false;
        }
    });
})();
