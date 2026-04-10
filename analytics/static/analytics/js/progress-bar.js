/**
 * LetterPulse Progress Bar Module
 *
 * Inline time-based progress bars that attach below buttons.
 * Advances in 5% increments at intervals based on expected duration,
 * then jumps to 100% on completion.
 */
const ProgressBar = (function() {
    // Track active progress bars by container ID
    const activeBars = {};

    // Configuration
    const COMPLETION_DELAY = 800;  // ms to show 100% before hiding
    const MAX_PROGRESS = 90;       // Max progress before completion signal

    /**
     * Create progress bar HTML
     */
    function createProgressHTML() {
        return `
            <div class="progress-bar-container mt-2" style="width: 100%;">
                <div class="progress" style="height: 20px; border-radius: 0.25rem; background-color: #e9ecef;">
                    <div class="progress-bar progress-bar-striped progress-bar-animated bg-success"
                         role="progressbar"
                         style="width: 0%; transition: width 0.3s ease-in-out;"
                         aria-valuenow="0"
                         aria-valuemin="0"
                         aria-valuemax="100">
                    </div>
                </div>
                <div class="progress-subtext text-muted mt-1" style="font-size: 0.75rem;"></div>
            </div>
        `;
    }

    /**
     * Start a progress bar
     * @param {Object} options - Configuration options
     * @param {string} options.containerId - ID of the container element to append progress bar to
     * @param {number} options.expectedDuration - Expected duration in seconds
     * @param {string} options.subtext - Optional subtext below progress bar
     */
    function start(options) {
        const {
            containerId,
            expectedDuration = 15,
            subtext = '',
            startProgress = 0
        } = options;

        const container = document.getElementById(containerId);
        if (!container) {
            console.error('Progress bar container not found:', containerId);
            return;
        }

        // Clean up any existing progress bar in this container
        if (activeBars[containerId]) {
            forceClose({ containerId: containerId });
        }

        // Insert progress bar HTML
        container.insertAdjacentHTML('beforeend', createProgressHTML());

        const progressContainer = container.querySelector('.progress-bar-container');
        const progressBar = progressContainer.querySelector('.progress-bar');
        const subtextEl = progressContainer.querySelector('.progress-subtext');

        // Set subtext
        if (subtext) {
            subtextEl.textContent = subtext;
        }

        // Initialize state, optionally at an offset for resumed bars
        const initialProgress = Math.min(startProgress, MAX_PROGRESS);
        const state = {
            container: progressContainer,
            progressBar: progressBar,
            subtextEl: subtextEl,
            currentProgress: initialProgress,
            isComplete: false,
            intervalId: null
        };

        activeBars[containerId] = state;

        if (initialProgress > 0) {
            updateProgressUI(state, initialProgress);
        }

        // Calculate interval: advance 5% at each interval
        // Total intervals needed: 18 (to reach 90%)
        const intervalMs = (expectedDuration * 1000) / 18;

        // Start progress timer
        state.intervalId = setInterval(function() {
            if (state.currentProgress < MAX_PROGRESS && !state.isComplete) {
                state.currentProgress += 5;
                updateProgressUI(state, state.currentProgress);
            }
        }, intervalMs);
    }

    /**
     * Update the progress bar UI
     */
    function updateProgressUI(state, percent) {
        state.progressBar.style.width = percent + '%';
        state.progressBar.setAttribute('aria-valuenow', percent);
    }

    /**
     * Complete the progress bar - jumps to 100% and hides
     * @param {Object} options - Completion options
     * @param {string} options.containerId - ID of the container
     * @param {boolean} options.success - Whether operation succeeded
     * @param {string} options.message - Optional completion message
     */
    function complete(options) {
        options = options || {};
        const containerId = options.containerId;
        const success = options.success !== undefined ? options.success : true;
        const message = options.message || null;

        const state = activeBars[containerId];
        if (!state) return;

        state.isComplete = true;

        // Clear the interval timer
        if (state.intervalId) {
            clearInterval(state.intervalId);
            state.intervalId = null;
        }

        // Jump to 100%
        state.currentProgress = 100;
        updateProgressUI(state, 100);

        // Update appearance based on success/failure
        if (success) {
            state.progressBar.classList.remove('bg-danger');
            state.progressBar.classList.add('bg-success');
            if (message) {
                state.subtextEl.classList.remove('text-danger');
                state.subtextEl.classList.add('text-success');
                state.subtextEl.textContent = message;
            }
        } else {
            state.progressBar.classList.remove('bg-success');
            state.progressBar.classList.add('bg-danger');
            if (message) {
                state.subtextEl.classList.remove('text-success');
                state.subtextEl.classList.add('text-danger');
                state.subtextEl.textContent = message;
            }
        }

        // Remove after brief delay
        setTimeout(function() {
            if (state.container && state.container.parentNode) {
                state.container.remove();
            }
            delete activeBars[containerId];
        }, COMPLETION_DELAY);
    }

    /**
     * Force close without animation
     */
    function forceClose(options) {
        const containerId = options.containerId;
        const state = activeBars[containerId];
        if (!state) return;

        if (state.intervalId) {
            clearInterval(state.intervalId);
            state.intervalId = null;
        }

        if (state.container && state.container.parentNode) {
            state.container.remove();
        }

        delete activeBars[containerId];
    }

    // Public API
    return {
        start: start,
        complete: complete,
        forceClose: forceClose
    };
})();
