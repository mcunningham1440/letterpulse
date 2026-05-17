// =========================================================================
// Learning / Updating flow
// =========================================================================
(function () {
    const cfg = document.getElementById('page-config').dataset;
    const UPDATE_TTL_MS = 15 * 60 * 1000;
    const PUB_ID = cfg.pubId || '';
    const TTL_KEY = "incrementalRefreshLastRun:" + PUB_ID;
    const PROGRESS_CONTAINER_ID = 'learningRunProgressContainer';
    const overlay = document.getElementById('learningRunOverlay');
    const titleEl = document.getElementById('learningRunTitle');
    const subtextEl = document.getElementById('learningRunSubtext');
    let activeTaskId = null;
    let activeKind = null;
    let lastPhase = null;
    let pollTimer = null;

    function setCopy(kind, phase, targetCount) {
        if (kind === 'initial') {
            titleEl.textContent = 'Learning Your Audience';
            subtextEl.textContent = phase === 'fetch'
                ? 'Fetching your posts…'
                : 'Studying your posts…';
        } else {
            titleEl.textContent = 'Updating Your Posts';
            subtextEl.textContent = phase === 'fetch'
                ? 'Fetching new posts…'
                : 'Learning from new posts…';
        }
    }

    function openModal() {
        overlay.style.display = 'flex';
    }

    function isModalVisible() {
        return overlay.style.display === 'flex';
    }

    function closeModal() {
        overlay.style.display = 'none';
        ProgressBar.forceClose({ containerId: PROGRESS_CONTAINER_ID });
    }

    function startPhase(kind, phase, targetCount) {
        setCopy(kind, phase, targetCount);
        ProgressBar.forceClose({ containerId: PROGRESS_CONTAINER_ID });
        ProgressBar.start({
            containerId: PROGRESS_CONTAINER_ID,
            mode: 'spinner',
        });
    }

    function handleTaskFinished(data) {
        clearInterval(pollTimer);
        pollTimer = null;
        const kind = activeKind;
        activeTaskId = null;
        activeKind = null;
        lastPhase = null;

        if (kind === 'update') {
            try { localStorage.setItem(TTL_KEY, String(Date.now())); } catch (e) {}
        }

        // An update task that never surfaced a modal (no posts needed
        // processing) should clean up silently.
        const silentUpdate = kind === 'update' && !isModalVisible();

        if (data.status === 'error') {
            if (!silentUpdate) {
                ProgressBar.complete({ containerId: PROGRESS_CONTAINER_ID, success: false, message: 'Error' });
                setTimeout(closeModal, 1200);
                showToast(GENERIC_ERROR_TOAST, 'danger');
            }
            // Initial-run errors wipe partial data via PendingLearningTask.on_error;
            // reload so the Learning coach re-fires for a clean retry.
            if (kind === 'initial') {
                setTimeout(function () { window.location.reload(); }, 600);
            }
            return;
        }

        if (silentUpdate) return;

        ProgressBar.complete({ containerId: PROGRESS_CONTAINER_ID, success: true, message: 'Done!' });
        setTimeout(function () {
            closeModal();
            if (kind === 'initial' && data.status === 'complete') {
                const doneOverlay = document.getElementById('learningDoneOverlay');
                if (doneOverlay) doneOverlay.style.display = 'flex';
            }
        }, 1000);
    }

    function pollActiveTask() {
        pollTimer = setInterval(async function () {
            if (!activeTaskId) return;
            try {
                const resp = await fetch(`/insights/learning/status/${activeTaskId}/`);
                const data = await resp.json();
                if (!data.success) return;

                if (data.phase && data.phase !== lastPhase) {
                    lastPhase = data.phase;
                    // Update flow runs silently until there are posts
                    // to process — reveal the modal on the transition
                    // into the process phase, but only while the task
                    // is still live (avoid a flash when a single poll
                    // returns both the phase bump and the terminal state).
                    const stillRunning = data.status === 'pending' || data.status === 'running';
                    if (data.kind === 'update' && data.phase === 'process' && stillRunning && !isModalVisible()) {
                        openModal();
                    }
                    startPhase(data.kind, data.phase, data.target_process_count);
                }

                if (data.status === 'complete' || data.status === 'error') {
                    handleTaskFinished(data);
                }
            } catch (err) {
                console.error('Learning poll failed', err);
            }
        }, 3000);
    }

    async function startTask(kind) {
        const url = kind === 'initial' ? cfg.urlStartLearning : cfg.urlStartUpdate;
        try {
            const data = await postJSON(url, {});
            if (!data.success || !data.task_id) {
                showToast(GENERIC_ERROR_TOAST, 'danger');
                return;
            }
            activeTaskId = data.task_id;
            activeKind = kind;
            lastPhase = 'fetch';
            // Update flow runs silently in the background during fetch;
            // the modal only opens if the task enters the process phase.
            if (kind !== 'update') {
                openModal();
                startPhase(kind, 'fetch', 0);
            }
            pollActiveTask();
        } catch (err) {
            showToast(GENERIC_ERROR_TOAST, 'danger');
        }
    }

    // Attach Learning coach button
    const startBtn = document.getElementById('startLearningBtn');
    if (startBtn) {
        startBtn.addEventListener('click', function () {
            const coach = document.getElementById('learningCoachOverlay');
            if (coach) coach.style.display = 'none';
            startTask('initial');
        });
    }

    // If an initial task is already running on page load, resume the modal
    if (cfg.showLearningModal === '1' && cfg.activeLearningTaskId) {
        activeTaskId = cfg.activeLearningTaskId;
        activeKind = 'initial';
        lastPhase = 'fetch';
        openModal();
        startPhase('initial', 'fetch', 0);
        pollActiveTask();
    }

    // Auto-fire update task on eligible page loads, subject to TTL
    if (cfg.showUpdateModal === '1') {
        (function () {
            let lastRun = 0;
            try { lastRun = parseInt(localStorage.getItem(TTL_KEY) || '0', 10); } catch (e) {}
            if (Date.now() - lastRun < UPDATE_TTL_MS) return;
            startTask('update');
        })();
    }

    // Dismiss the post-learning coach and reload so insights_view
    // recomputes has_processed_data etc. and the next coach can render.
    const doneBtn = document.getElementById('closeLearningDoneBtn');
    if (doneBtn) {
        doneBtn.addEventListener('click', function () {
            const doneOverlay = document.getElementById('learningDoneOverlay');
            if (doneOverlay) doneOverlay.style.display = 'none';
            window.location.reload();
        });
    }
})();

// =========================================================================
// Write Post Feedback + write-post-poll coach marks
// (Coach marks for content-finder and improvement-tips live with those
// modules. This file owns the write-post-poll demo flow, which is shown
// after the user finishes their first Content Finder run.)
// =========================================================================
(function () {
    const cfg = document.getElementById('page-config').dataset;
    const hasProcessedData       = cfg.hasProcessedData === '1';
    const hasSeenWritePostPoll   = cfg.hasSeenWritePostPoll === '1';
    const hasUsedWritePostPoll   = cfg.hasUsedWritePostPoll === '1';
    const writePostFeedbackInitial = cfg.writePostFeedbackResponse || '';

    // "Check them out" + "Next feature" coaches: shown to users who haven't
    // yet seen the write-post poll. The Content Finder completion handler
    // is what reveals the contentFoundOverlay — these handlers wire up the
    // buttons inside it.
    if (hasProcessedData && !hasSeenWritePostPoll) {
        document.getElementById('checkThemOutBtn').addEventListener('click', function() {
            document.getElementById('contentFoundOverlay').style.display = 'none';
        });

        // "Next feature" button → mark as seen, reveal sections 2 & 3 permanently,
        // hide the button forever, show Future Feature poll coach.
        document.getElementById('nextFeatureBtn').addEventListener('click', async function() {
            try {
                await postJSON(cfg.urlSubmitFeedback, { feature: 'seen_write_post_poll', response: 'shown' });
            } catch (err) {
                // Continue even if save fails — the user still gets the in-page reveal
            }
            var container = document.getElementById('nextFeatureContainer');
            if (container) container.style.display = 'none';
            document.getElementById('writeAndImproveContainer').style.display = '';
            document.getElementById('futureFeatureOverlay').style.display = 'flex';
        });
    }

    // Future Feature poll: shown to users who've seen but not yet answered.
    if (hasProcessedData && !hasUsedWritePostPoll) {
        // Auto-show the Future Feature poll on every Write page load until the
        // user has answered it. Only fires once they've already clicked "Next
        // feature" (otherwise the button click handler shows it).
        if (hasSeenWritePostPoll) {
            document.getElementById('futureFeatureOverlay').style.display = 'flex';
        }

        // Future Feature coach poll buttons → submit feedback, close coach, show Thank You
        document.querySelectorAll('.coach-poll-btn').forEach(function(btn) {
            btn.addEventListener('click', async function() {
                var response = this.dataset.response;
                try {
                    await postJSON(cfg.urlSubmitFeedback, { feature: 'write_post', response: response });
                } catch (err) {
                    // Continue even if save fails
                }
                // Close the Future Feature overlay
                document.getElementById('futureFeatureOverlay').style.display = 'none';
                // Also highlight the matching button in the actual card
                highlightFeedbackBtn(response);
                // Show Thank You coach
                document.getElementById('thankYouOverlay').style.display = 'flex';
            });
        });

        // Thank You "Next feature" → dismiss; show Review Your Draft coach.
        // (Sections 2 & 3 are already revealed at this point — they were unhidden
        // when the user first clicked "Next feature".)
        document.getElementById('thankYouNextBtn').addEventListener('click', function() {
            document.getElementById('thankYouOverlay').style.display = 'none';
            var reviewOverlay = document.getElementById('reviewDraftOverlay');
            if (reviewOverlay) reviewOverlay.style.display = 'flex';
        });
    }

    // Toggle "2. Write your post" card body open/closed.
    (function() {
        var toggleBtn = document.getElementById('writePostToggleBtn');
        var body = document.getElementById('writePostCardBody');
        var icon = document.getElementById('writePostToggleIcon');
        if (!toggleBtn || !body || !icon) return;
        toggleBtn.addEventListener('click', function() {
            var isHidden = body.style.display === 'none';
            if (isHidden) {
                body.style.display = '';
                icon.classList.remove('bi-chevron-down');
                icon.classList.add('bi-chevron-up');
                toggleBtn.setAttribute('aria-expanded', 'true');
            } else {
                body.style.display = 'none';
                icon.classList.remove('bi-chevron-up');
                icon.classList.add('bi-chevron-down');
                toggleBtn.setAttribute('aria-expanded', 'false');
            }
        });
    })();

    // Exposed because the coach-poll-btn handlers above also call it. Declared
    // as a window property rather than top-level `function` so it stays scoped
    // to this IIFE's wiring even though it's globally addressable.
    window.highlightFeedbackBtn = function(response) {
        document.querySelectorAll('.write-post-feedback-btn').forEach(b => {
            if (b.dataset.response === response) {
                b.classList.remove('btn-outline-primary');
                b.classList.add('btn-outline-primary');
                b.style.backgroundColor = '#0d6efd';
                b.style.borderColor = '#0d6efd';
                b.style.color = '#fff';
            } else {
                b.style.backgroundColor = '';
                b.style.borderColor = '';
                b.style.color = '';
            }
        });
    };

    // Pre-highlight if user already submitted
    if (writePostFeedbackInitial) {
        highlightFeedbackBtn(writePostFeedbackInitial);
    }

    document.querySelectorAll('.write-post-feedback-btn').forEach(btn => {
        btn.addEventListener('click', async function() {
            const response = this.dataset.response;
            try {
                const data = await postJSON(cfg.urlSubmitFeedback, { feature: 'write_post', response: response });
                if (data.success) {
                    highlightFeedbackBtn(response);
                    showAlert('Thank you for helping us improve!', 'Feedback Received');
                } else {
                    showToast(GENERIC_ERROR_TOAST, 'danger');
                }
            } catch (err) {
                showToast(GENERIC_ERROR_TOAST, 'danger');
            }
        });
    });
})();
