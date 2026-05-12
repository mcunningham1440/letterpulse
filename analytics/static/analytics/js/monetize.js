(function () {
    const STORAGE_KEY = 'lp_monetize_campaign_state_v1';
    // Persisted shape: { launch_at: ISO string | null, cancelled: boolean }
    // The displayed state is *derived* from this:
    //   cancelled === true                -> 'cancelled'
    //   launch_at is null                  -> 'idle'
    //   launch_at > now                    -> 'preparing'
    //   launch_at <= now                   -> 'active'

    function readState() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) return { launch_at: null, cancelled: false };
            const obj = JSON.parse(raw);
            return {
                launch_at: obj.launch_at || null,
                cancelled: !!obj.cancelled,
            };
        } catch (e) {
            // Corrupt JSON in localStorage — silently fall back to idle rather
            // than erroring out, since this is a demo-only flag.
            return { launch_at: null, cancelled: false };
        }
    }

    function writeState(s) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
    }

    function deriveView(s) {
        if (s.cancelled) return 'cancelled';
        if (!s.launch_at) return 'idle';
        const launchMs = Date.parse(s.launch_at);
        if (isNaN(launchMs)) return 'idle';
        return launchMs > Date.now() ? 'preparing' : 'active';
    }

    function renderEta(launchAtIso) {
        const el = document.getElementById('prepEta');
        if (!el) return;
        const launchMs = Date.parse(launchAtIso);
        if (isNaN(launchMs)) {
            // Invalid timestamp — soft fallback message rather than erroring.
            el.innerHTML = 'Your first emails go out soon.';
            return;
        }
        const launch = new Date(launchMs);
        const dateStr = launch.toLocaleDateString(undefined, {
            weekday: 'long', month: 'long', day: 'numeric',
        });
        const timeStr = launch.toLocaleTimeString(undefined, {
            hour: 'numeric', minute: '2-digit',
        });
        const diffMs = launchMs - Date.now();
        let etaTail;
        if (diffMs <= 0) {
            etaTail = 'any moment now';
        } else {
            const hours = Math.round(diffMs / 3.6e6);
            etaTail = `about ${hours} hour${hours === 1 ? '' : 's'} from now`;
        }
        el.innerHTML = `Your first emails go out by <strong>${dateStr} at ${timeStr}</strong> — ${etaTail}.`;
    }

    function applyView() {
        const s = readState();
        const view = deriveView(s);
        ['idle', 'preparing', 'active', 'cancelled'].forEach(name => {
            const node = document.getElementById('status-' + name);
            if (node) node.hidden = (name !== view);
        });
        if (view === 'preparing') {
            renderEta(s.launch_at);
        }
    }

    // -- Email sequence tab switcher --
    document.querySelectorAll('.seq-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const n = tab.getAttribute('data-seq');
            document.querySelectorAll('.seq-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.seq-pane').forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            const pane = document.getElementById('seq-' + n);
            if (pane) pane.classList.add('active');
            // Hide "Edit pitch" when not viewing the pitch tab.
            const editBtn = document.getElementById('editEmail1Btn');
            if (editBtn) editBtn.style.visibility = (n === '1') ? 'visible' : 'hidden';
        });
    });

    // -- Volume tier select --
    document.querySelectorAll('.volume-card').forEach(card => {
        card.addEventListener('click', () => {
            document.querySelectorAll('.volume-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
        });
    });

    // -- Launch / Cancel / Relaunch --
    const launchBtn = document.getElementById('launchBtn');
    if (launchBtn) {
        launchBtn.addEventListener('click', () => {
            const launchAt = new Date(Date.now() + 48 * 3600 * 1000);
            writeState({ launch_at: launchAt.toISOString(), cancelled: false });
            applyView();
        });
    }

    function cancelCampaign() {
        writeState({ launch_at: null, cancelled: true });
        applyView();
    }
    const cancelPrepBtn = document.getElementById('cancelBtnPreparing');
    if (cancelPrepBtn) cancelPrepBtn.addEventListener('click', cancelCampaign);
    const cancelActiveBtn = document.getElementById('cancelBtnActive');
    if (cancelActiveBtn) cancelActiveBtn.addEventListener('click', cancelCampaign);

    const relaunchBtn = document.getElementById('relaunchBtn');
    if (relaunchBtn) {
        relaunchBtn.addEventListener('click', () => {
            writeState({ launch_at: null, cancelled: false });
            applyView();
        });
    }

    // Initial render based on persisted state.
    applyView();

    // ====================================================================
    // -- Edit profile (Niche + Readers click most on) --
    // Persisted in localStorage so edits survive reloads, mirroring the
    // campaign-state pattern. Audience and click-topic counts on this page
    // are demo-static; only these two fields are editable per spec.
    // ====================================================================
    const PROFILE_KEY = 'lp_monetize_profile_v1';
    const MAX_TOPICS = 5;

    const nichePill        = document.getElementById('nichePill');
    const nicheInput       = document.getElementById('nicheInput');
    const topicsView       = document.getElementById('topicsView');
    const topicsEdit       = document.getElementById('topicsEdit');
    const topicEditList    = document.getElementById('topicEditList');
    const topicRowTemplate = document.getElementById('topicRowTemplate');
    const addTopicBtn      = document.getElementById('addTopicBtn');
    const topicLimitHint   = document.getElementById('topicLimitHint');
    const editBtn          = document.getElementById('profileEditBtn');
    const saveBtn          = document.getElementById('profileSaveBtn');
    const cancelBtn        = document.getElementById('profileCancelBtn');

    const NICHE_EMPTY_TEXT  = "Click 'Edit profile' to add your niche";
    const TOPICS_EMPTY_TEXT = "Click 'Edit profile' to add the topics readers click on most";

    function readProfile() {
        try {
            const raw = localStorage.getItem(PROFILE_KEY);
            if (!raw) return null;
            const obj = JSON.parse(raw);
            if (!obj || typeof obj !== 'object') return null;
            return {
                niche: typeof obj.niche === 'string' ? obj.niche : '',
                topics: Array.isArray(obj.topics)
                    ? obj.topics.filter(t => typeof t === 'string').slice(0, MAX_TOPICS)
                    : [],
            };
        } catch (e) {
            // Corrupt JSON — silently fall back to the rendered defaults.
            return null;
        }
    }
    function writeProfile(p) {
        localStorage.setItem(PROFILE_KEY, JSON.stringify(p));
    }

    function setNiche(niche) {
        // Render the niche pill in either the value state (green ".strong"
        // pill) or the empty state (dashed muted pill prompting the user to
        // click "Edit profile"). The two states share id="nichePill" so
        // outside references stay valid.
        const trimmed = (niche || '').trim();
        if (trimmed) {
            nichePill.className = 'pp-pill strong';
            nichePill.textContent = trimmed;
            delete nichePill.dataset.empty;
        } else {
            nichePill.className = 'pp-pill pp-pill-empty';
            nichePill.textContent = NICHE_EMPTY_TEXT;
            nichePill.dataset.empty = 'true';
        }
    }

    function renderTopicsView(topics) {
        topicsView.innerHTML = '';
        if (topics && topics.length) {
            topics.forEach(t => {
                const span = document.createElement('span');
                span.className = 'pp-pill';
                span.textContent = t;
                topicsView.appendChild(span);
            });
        } else {
            const span = document.createElement('span');
            span.className = 'pp-pill pp-pill-empty';
            span.dataset.empty = 'true';
            span.textContent = TOPICS_EMPTY_TEXT;
            topicsView.appendChild(span);
        }
    }

    function getCurrentNiche() {
        return nichePill.dataset.empty === 'true' ? '' : nichePill.textContent.trim();
    }

    function getCurrentTopics() {
        return Array.from(topicsView.querySelectorAll('.pp-pill'))
            .filter(el => el.dataset.empty !== 'true')
            .map(el => el.textContent.trim())
            .filter(Boolean);
    }

    function addTopicEditRow(value) {
        const node = topicRowTemplate.content.firstElementChild.cloneNode(true);
        node.querySelector('.topic-edit-input').value = value;
        node.querySelector('.topic-remove-btn').addEventListener('click', () => {
            node.remove();
            updateAddTopicState();
        });
        topicEditList.appendChild(node);
    }

    function updateAddTopicState() {
        const count = topicEditList.querySelectorAll('.topic-edit-row').length;
        addTopicBtn.disabled = count >= MAX_TOPICS;
        topicLimitHint.textContent = `${count} of ${MAX_TOPICS} topics.`;
    }

    function enterEditMode() {
        nicheInput.value = getCurrentNiche();
        topicEditList.innerHTML = '';
        getCurrentTopics().forEach(t => addTopicEditRow(t));
        updateAddTopicState();
        nichePill.hidden = true;
        nicheInput.hidden = false;
        topicsView.hidden = true;
        topicsEdit.hidden = false;
        editBtn.hidden = true;
        saveBtn.hidden = false;
        cancelBtn.hidden = false;
        nicheInput.focus();
        nicheInput.select();
    }

    function exitEditMode() {
        nichePill.hidden = false;
        nicheInput.hidden = true;
        topicsView.hidden = false;
        topicsEdit.hidden = true;
        editBtn.hidden = false;
        saveBtn.hidden = true;
        cancelBtn.hidden = true;
    }

    if (editBtn) editBtn.addEventListener('click', enterEditMode);
    if (cancelBtn) cancelBtn.addEventListener('click', exitEditMode);
    if (saveBtn) saveBtn.addEventListener('click', () => {
        // Empty values are intentionally allowed — they re-render the
        // "Click 'Edit profile' to add" prompt pills. The act of saving (even
        // an empty profile) commits the user to manual control: the niche
        // analysis poll handler will not overwrite their choices afterward.
        const newNiche = nicheInput.value.trim();
        const newTopics = Array.from(topicEditList.querySelectorAll('.topic-edit-input'))
            .map(i => i.value.trim())
            .filter(Boolean)
            .slice(0, MAX_TOPICS);
        setNiche(newNiche);
        renderTopicsView(newTopics);
        writeProfile({ niche: newNiche, topics: newTopics });
        // Make subsequent poll completions respect the just-saved state, even
        // if the LLM finishes after this save.
        savedProfile = { niche: newNiche, topics: newTopics };
        exitEditMode();
    });
    if (addTopicBtn) addTopicBtn.addEventListener('click', () => {
        const count = topicEditList.querySelectorAll('.topic-edit-row').length;
        if (count >= MAX_TOPICS) return;
        addTopicEditRow('');
        updateAddTopicState();
        const inputs = topicEditList.querySelectorAll('.topic-edit-input');
        if (inputs.length) inputs[inputs.length - 1].focus();
    });

    // On load: apply persisted profile (if any) over the rendered defaults.
    // Empty saved values are honored — the helper functions render the
    // empty-state prompt pills in that case. `let` (not `const`) because
    // the save handler reassigns this on every save.
    let savedProfile = readProfile();
    if (savedProfile) {
        setNiche(savedProfile.niche || '');
        renderTopicsView(savedProfile.topics || []);
    }

    // ====================================================================
    // -- Niche analysis polling (Monetize-tab one-shot LLM call) --
    // The server kicks off the analysis on first visit when the user has
    // processed posts. While it runs, the niche pill and topics show
    // "Analyzing…" placeholders. We poll until status flips to complete
    // (or error) and replace the placeholders with the LLM result.
    //
    // Priority on completion: any saved profile > LLM result. Once the user
    // has hit Save (even to clear everything), they own the profile state and
    // the LLM result no longer overrides it.
    // ====================================================================
    const profileGrid = document.getElementById('profileGrid');
    const initialNicheStatus = profileGrid ? profileGrid.dataset.nicheStatus : '';
    const nicheTaskId = profileGrid ? profileGrid.dataset.nicheTaskId : '';

    function userHasManualEdits() {
        // True once the user has saved at least once. Empty-but-saved counts
        // — clearing the field is itself a deliberate choice and shouldn't
        // be overwritten by an in-flight analysis.
        return savedProfile !== null;
    }

    async function pollNicheAnalysis() {
        if (!nicheTaskId) return;
        const url = `/monetize/niche-analysis/status/${nicheTaskId}/`;
        // Cap the wait at ~3 minutes — the call is gpt-5.4 with medium
        // reasoning and should complete in well under that. After the cap,
        // we silently stop polling (flagged as soft fallback per global
        // instructions): the placeholder remains and the user can refresh.
        const MAX_ATTEMPTS = 60;  // 60 * 3s = 3 min
        for (let i = 0; i < MAX_ATTEMPTS; i++) {
            await new Promise(r => setTimeout(r, 3000));
            let data;
            try {
                const resp = await fetch(url, {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                data = await resp.json();
            } catch (e) {
                // Transient network error — keep trying.
                continue;
            }
            if (!data || !data.success) continue;
            if (data.status === 'complete') {
                if (userHasManualEdits()) return;
                const llmNiche = (data.niche || '').trim();
                const llmTypes = Array.isArray(data.content_types) ? data.content_types : [];
                // setNiche / renderTopicsView swap into the empty-state pill
                // prompt automatically when the LLM returned a blank value,
                // so a no-content_types result still shows the right UI.
                setNiche(llmNiche);
                renderTopicsView(llmTypes);
                return;
            }
            if (data.status === 'error') {
                // Silently leave placeholders in place. This is a low-stakes
                // background analysis; surfacing an error toast on a tab the
                // user just landed on would be jarring.
                return;
            }
        }
    }

    if (initialNicheStatus === 'running' && nicheTaskId) {
        pollNicheAnalysis();
    }
})();
