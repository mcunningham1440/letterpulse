/**
 * LetterPulse Progress Bar Module
 *
 * Indeterminate progress indicators in two modes:
 *   - 'shimmer' (default): 3px hairline bar with a sweeping gradient.
 *     Used inline beneath buttons (Content Finder, Improvement Tips).
 *   - 'spinner': small circular spinner. Used inside the Learning modal,
 *     where the surrounding modal already supplies phase copy.
 */
const ProgressBar = (function() {
    const activeBars = {};
    const COMPLETION_DELAY = 700;
    const BRAND = '#28a745';

    let stylesInjected = false;
    function injectStyles() {
        if (stylesInjected) return;
        stylesInjected = true;
        const css = `
            @keyframes lp-shimmer-sweep {
                0%   { transform: translateX(-100%); }
                100% { transform: translateX(400%); }
            }
            @keyframes lp-spin {
                to { transform: rotate(360deg); }
            }
            @keyframes lp-fade-out {
                to { opacity: 0; }
            }
            .lp-progress {
                width: 100%;
                opacity: 1;
                transition: opacity 0.25s ease;
            }
            .lp-progress.is-fading {
                opacity: 0;
            }
            .lp-progress-track {
                position: relative;
                width: 100%;
                height: 3px;
                background: rgba(0, 0, 0, 0.06);
                border-radius: 999px;
                overflow: hidden;
            }
            .lp-progress-track__fill {
                position: absolute;
                top: 0;
                left: 0;
                bottom: 0;
                width: 30%;
                background: linear-gradient(
                    90deg,
                    transparent 0%,
                    ${BRAND} 50%,
                    transparent 100%
                );
                animation: lp-shimmer-sweep 1.4s ease-in-out infinite;
            }
            .lp-progress-track--solid .lp-progress-track__fill {
                animation: none;
                width: 100%;
                background: ${BRAND};
            }
            .lp-progress-track--error .lp-progress-track__fill {
                background: #dc3545;
            }
            .lp-progress-subtext {
                margin-top: 6px;
                font-size: 0.75rem;
                color: #6c757d;
                line-height: 1.2;
            }
            .lp-progress-subtext--success { color: ${BRAND}; }
            .lp-progress-subtext--error   { color: #dc3545; }

            .lp-spinner-wrap {
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 4px 0;
            }
            .lp-spinner {
                width: 22px;
                height: 22px;
                border: 2px solid rgba(0, 0, 0, 0.08);
                border-top-color: ${BRAND};
                border-radius: 50%;
                animation: lp-spin 0.7s linear infinite;
            }
            .lp-spinner--success {
                border-color: ${BRAND};
                animation: none;
            }
            .lp-spinner--error {
                border-color: #dc3545;
                animation: none;
            }
        `;
        const style = document.createElement('style');
        style.setAttribute('data-lp-progress', '');
        style.textContent = css;
        document.head.appendChild(style);
    }

    function buildShimmer(subtext) {
        const wrap = document.createElement('div');
        wrap.className = 'lp-progress lp-progress--shimmer';
        wrap.innerHTML = `
            <div class="lp-progress-track">
                <div class="lp-progress-track__fill"></div>
            </div>
            <div class="lp-progress-subtext"></div>
        `;
        if (subtext) {
            wrap.querySelector('.lp-progress-subtext').textContent = subtext;
        }
        return wrap;
    }

    function buildSpinner() {
        const wrap = document.createElement('div');
        wrap.className = 'lp-progress lp-progress--spinner';
        wrap.innerHTML = `
            <div class="lp-spinner-wrap">
                <div class="lp-spinner"></div>
            </div>
        `;
        return wrap;
    }

    /**
     * Start a progress indicator.
     * @param {Object} options
     * @param {string} options.containerId   - Target container element ID.
     * @param {('shimmer'|'spinner')} [options.mode='shimmer']
     * @param {string} [options.subtext]     - Caption (shimmer mode only).
     */
    function start(options) {
        injectStyles();
        const containerId = options.containerId;
        const mode = options.mode === 'spinner' ? 'spinner' : 'shimmer';
        const subtext = options.subtext || '';

        const container = document.getElementById(containerId);
        if (!container) {
            console.error('Progress bar container not found:', containerId);
            return;
        }

        if (activeBars[containerId]) {
            forceClose({ containerId: containerId });
        }

        const node = mode === 'spinner' ? buildSpinner() : buildShimmer(subtext);
        container.appendChild(node);

        activeBars[containerId] = {
            node: node,
            mode: mode,
            isComplete: false,
        };
    }

    /**
     * Complete the indicator with a brief success/error state, then remove it.
     */
    function complete(options) {
        options = options || {};
        const containerId = options.containerId;
        const success = options.success !== undefined ? options.success : true;
        const message = options.message || null;

        const state = activeBars[containerId];
        if (!state) return;

        state.isComplete = true;

        if (state.mode === 'shimmer') {
            const track = state.node.querySelector('.lp-progress-track');
            const subtextEl = state.node.querySelector('.lp-progress-subtext');
            track.classList.add('lp-progress-track--solid');
            if (!success) track.classList.add('lp-progress-track--error');
            if (message) {
                subtextEl.textContent = message;
                subtextEl.classList.add(
                    success ? 'lp-progress-subtext--success' : 'lp-progress-subtext--error'
                );
            }
        } else {
            const spinner = state.node.querySelector('.lp-spinner');
            spinner.classList.add(success ? 'lp-spinner--success' : 'lp-spinner--error');
        }

        // Fade and remove.
        setTimeout(function() {
            state.node.classList.add('is-fading');
            setTimeout(function() {
                if (state.node.parentNode) state.node.parentNode.removeChild(state.node);
                delete activeBars[containerId];
            }, 250);
        }, COMPLETION_DELAY);
    }

    /**
     * Remove the indicator immediately, no animation.
     */
    function forceClose(options) {
        const containerId = options.containerId;
        const state = activeBars[containerId];
        if (!state) return;
        if (state.node.parentNode) state.node.parentNode.removeChild(state.node);
        delete activeBars[containerId];
    }

    return {
        start: start,
        complete: complete,
        forceClose: forceClose,
    };
})();
