// Companion turtle — opt-in screen pet for the teacher's browser.
//
// - Off by default. Enabled via the discreet checkbox in the nav.
// - State machine: idle -> walk -> idle -> ... with occasional sleep.
// - Click for a happy hop. Click and hold to drag; release falls back down.
// - Pauses when the tab is hidden. Clamps to viewport on resize.
// - Persistence is per-browser via localStorage. No DB, no per-teacher config.
//
// Self-contained: injects its own styles, requires no external assets,
// adds nothing but a single fixed-position <div> on body when active.
(function () {
    'use strict';

    var STORAGE_KEY = 'screen_pet_enabled';
    var WIDTH = 76;          // SVG box width
    var HEIGHT = 64;         // SVG box height
    var GROUND_PAD = 8;      // gap between turtle and viewport bottom

    var pet = null;          // root element (when mounted)
    var rafId = null;
    var paused = false;

    var state = {
        x: 200,              // px from left edge of viewport
        y: 0,                // px from top — set on mount based on innerHeight
        facing: 'left',      // 'left' = head pointed at -X (default SVG orientation)
        mode: 'idle',        // idle | walk | sleep | drag | hop | fall
        modeUntil: 0,        // ms timestamp when current mode should end
        targetX: 200,        // for walk
        speed: 50,           // px/sec walking speed
        idleSince: 0,        // ms timestamp of last activity (used for sleep onset)
        dragOffset: null,    // { x, y } during drag
        fallVy: 0,           // vertical velocity during fall
        lastT: 0,
    };

    // ---------------------------------------------------------------- styles
    function injectStyles() {
        if (document.getElementById('screen-pet-styles')) return;
        var css = ''
            // --- nav checkbox (lives in base.html nav, must render even when pet is off)
            + '.screen-pet-toggle {'
            + '  position: relative; width: 16px; height: 16px;'
            + '  cursor: pointer; appearance: none; -webkit-appearance: none;'
            + '  background: white;'
            + '  border: 1.5px solid #c7c8d0;'
            + '  border-radius: 4px; margin: 0; flex: 0 0 auto;'
            + '  transition: background 0.15s, border-color 0.15s;'
            + '  vertical-align: middle;'
            + '}'
            + '.screen-pet-toggle:hover { border-color: #6cc788; }'
            + '.screen-pet-toggle:checked { background: #6cc788; border-color: #4fa56d; }'
            + '.screen-pet-toggle:checked::after {'
            + '  content: ""; position: absolute; left: 4px; top: 0px;'
            + '  width: 4px; height: 9px; border: solid white; border-width: 0 2px 2px 0;'
            + '  transform: rotate(45deg);'
            + '}'
            + '.screen-pet-toggle:focus-visible { outline: 2px solid #6cc788; outline-offset: 2px; }'

            // --- pet container
            + '#screen-pet {'
            + '  position: fixed; left: 0; top: 0; width: ' + WIDTH + 'px; height: ' + HEIGHT + 'px;'
            + '  pointer-events: auto; user-select: none; -webkit-user-select: none;'
            + '  z-index: 9999; cursor: grab;'
            + '  will-change: transform;'
            + '}'
            + '#screen-pet[data-mode="drag"] { cursor: grabbing; }'
            + '#screen-pet svg { display: block; width: 100%; height: 100%; pointer-events: none; }'

            // --- walking leg cycle (4 legs, diagonal gait)
            + '@keyframes screen-pet-leg-step {'
            + '  0%   { transform: translate(0, 0); }'
            + '  20%  { transform: translate(-2px, -3px); }'
            + '  40%  { transform: translate(-3px, 0); }'
            + '  60%  { transform: translate(-2px, 0); }'
            + '  80%  { transform: translate(-1px, 0); }'
            + '  100% { transform: translate(0, 0); }'
            + '}'
            + '@keyframes screen-pet-head-poke {'
            + '  0%, 100% { transform: translateX(0); }'
            + '  50% { transform: translateX(-1.5px); }'
            + '}'
            + '@keyframes screen-pet-tail-wag {'
            + '  0%, 100% { transform: rotate(-3deg); }'
            + '  50% { transform: rotate(3deg); }'
            + '}'
            + '@keyframes screen-pet-blink {'
            + '  0%, 92%, 100% { transform: scaleY(1); }'
            + '  95% { transform: scaleY(0.1); }'
            + '}'
            + '@keyframes screen-pet-z-float {'
            + '  0% { transform: translate(0, 0); opacity: 0.9; }'
            + '  100% { transform: translate(8px, -10px); opacity: 0; }'
            + '}'
            + '@keyframes screen-pet-hop {'
            + '  0%, 100% { transform: translateY(0); }'
            + '  40%      { transform: translateY(-12px); }'
            + '}'

            // --- transform-box defaults so transforms work on inner SVG nodes
            + '#screen-pet .leg, #screen-pet .turtle-head, #screen-pet .turtle-tail, #screen-pet .eye {'
            + '  transform-origin: center; transform-box: fill-box;'
            + '}'

            // --- mode-driven activations
            + '#screen-pet[data-mode="idle"] .eye-open { animation: screen-pet-blink 4s infinite; }'
            + '#screen-pet[data-mode="walk"] .leg-a { animation: screen-pet-leg-step 0.7s ease-in-out infinite; }'
            + '#screen-pet[data-mode="walk"] .leg-b { animation: screen-pet-leg-step 0.7s ease-in-out infinite; animation-delay: -0.35s; }'
            + '#screen-pet[data-mode="walk"] .turtle-head { animation: screen-pet-head-poke 0.7s ease-in-out infinite; }'
            + '#screen-pet[data-mode="walk"] .turtle-tail { animation: screen-pet-tail-wag 0.7s ease-in-out infinite; transform-origin: 78px 56px; }'
            + '#screen-pet .eye-closed { display: none; }'
            + '#screen-pet[data-mode="sleep"] .eye-open { display: none; }'
            + '#screen-pet[data-mode="sleep"] .eye-closed { display: block; }'
            + '#screen-pet .z-bubble-group { display: none; }'
            + '#screen-pet[data-mode="sleep"] .z-bubble-group { display: block; }'
            + '#screen-pet[data-mode="sleep"] .z-bubble { animation: screen-pet-z-float 1.6s ease-out infinite; }'
            + '#screen-pet[data-mode="hop"] .turtle-svg { animation: screen-pet-hop 0.4s ease-out; }'
            + '';
        var style = document.createElement('style');
        style.id = 'screen-pet-styles';
        style.textContent = css;
        document.head.appendChild(style);
    }

    // ------------------------------------------------------------------- svg
    // ViewBox 96x80 from the approved preview. Class hooks let CSS animate
    // legs / head / tail / eyes / Z bubble per state.
    function svgMarkup() {
        return ''
            + '<svg class="turtle-svg" viewBox="0 0 96 80" xmlns="http://www.w3.org/2000/svg">'
            +   '<ellipse cx="48" cy="74" rx="28" ry="3" fill="rgba(0,0,0,0.18)"/>'
            // far-side legs (small, paired diagonally with near-side)
            +   '<ellipse class="leg leg-b" cx="28" cy="58" rx="4" ry="3.5" fill="#8fc486" opacity="0.7"/>'
            +   '<ellipse class="leg leg-a" cx="68" cy="58" rx="4" ry="3.5" fill="#8fc486" opacity="0.7"/>'
            // tail
            +   '<path class="turtle-tail" d="M 78 54 q 8 0 9 4 q -2 2 -9 1 z" fill="#a8d99b"/>'
            // belly
            +   '<ellipse cx="48" cy="60" rx="26" ry="10" fill="#f3e7c1"/>'
            // shell
            +   '<path d="M 22 56 q 0 -28 26 -28 q 26 0 26 28 z" fill="#6cc788"/>'
            +   '<path d="M 22 56 q 0 -28 26 -28 q 26 0 26 28 z" fill="none" stroke="#4fa56d" stroke-width="2"/>'
            +   '<path d="M 48 32 l 8 5 l 0 9 l -8 5 l -8 -5 l 0 -9 z" fill="#4fa56d" opacity="0.55"/>'
            +   '<path d="M 32 41 l 6 4 l 0 8 l -6 3 z" fill="#4fa56d" opacity="0.45"/>'
            +   '<path d="M 64 41 l -6 4 l 0 8 l 6 3 z" fill="#4fa56d" opacity="0.45"/>'
            // near-side legs (large, in front)
            +   '<ellipse class="leg leg-a" cx="22" cy="62" rx="6" ry="5" fill="#a8d99b"/>'
            +   '<ellipse class="leg leg-b" cx="72" cy="62" rx="6" ry="5" fill="#a8d99b"/>'
            // head + eye
            +   '<g class="turtle-head">'
            +     '<circle cx="14" cy="50" r="9" fill="#a8d99b"/>'
            +     '<circle cx="11" cy="52" r="2" fill="#ffb1b1" opacity="0.7"/>'
            +     '<ellipse class="eye eye-open" cx="11" cy="48" rx="1.4" ry="2" fill="#222"/>'
            +     '<circle class="eye eye-open" cx="10.6" cy="47.3" r="0.5" fill="white"/>'
            +     '<path class="eye-closed" d="M 9 48.5 q 2 -1.5 4 0" fill="none" stroke="#222" stroke-width="0.9" stroke-linecap="round"/>'
            +     '<path class="mouth" d="M 9 51.5 q 1.5 1 3 0" fill="none" stroke="#222" stroke-width="0.7" stroke-linecap="round"/>'
            +   '</g>'
            // z bubble (only visible during sleep)
            +   '<g class="z-bubble-group">'
            +     '<text class="z-bubble" x="22" y="38" font-size="9" fill="#7a8c9c" font-family="serif" font-style="italic">z</text>'
            +     '<text x="26" y="34" font-size="6" fill="#7a8c9c" font-family="serif" font-style="italic" opacity="0.6">z</text>'
            +   '</g>'
            + '</svg>';
    }

    // ------------------------------------------------------------ positioning
    function groundY() { return Math.max(0, window.innerHeight - HEIGHT - GROUND_PAD); }

    function clampX(x) { return Math.max(8, Math.min(window.innerWidth - WIDTH - 8, x)); }

    function applyTransform() {
        if (!pet) return;
        var sx = state.facing === 'left' ? 1 : -1;
        // facing right = mirror via scaleX; we anchor scale at element's centre
        // by translating to its centre, scaling, then back.
        if (sx === 1) {
            pet.style.transform = 'translate(' + state.x + 'px, ' + state.y + 'px)';
        } else {
            pet.style.transform = 'translate(' + state.x + 'px, ' + state.y + 'px) scaleX(-1)';
        }
        pet.dataset.mode = state.mode;
    }

    // -------------------------------------------------------------- behaviour
    function setMode(mode, durationMs) {
        state.mode = mode;
        state.modeUntil = performance.now() + (durationMs || 0);
        if (mode !== 'idle' && mode !== 'sleep') state.idleSince = 0;
        applyTransform();
    }

    function pickWalkTarget() {
        var minDist = 80, maxDist = 280;
        var dist = minDist + Math.random() * (maxDist - minDist);
        var dir = Math.random() < 0.5 ? -1 : 1;
        // Bias toward direction with more room
        if (state.x < window.innerWidth * 0.25) dir = 1;
        else if (state.x > window.innerWidth * 0.75) dir = -1;
        var target = clampX(state.x + dir * dist);
        state.targetX = target;
        state.facing = (target < state.x) ? 'left' : 'right';
    }

    function pickNextIdleAction() {
        // 65% walk, 25% short idle, 10% sleep (only if idle a while)
        var r = Math.random();
        var now = performance.now();
        if (state.idleSince && (now - state.idleSince) > 25000 && r < 0.10) {
            setMode('sleep', 8000 + Math.random() * 6000);
            return;
        }
        if (r < 0.65) {
            pickWalkTarget();
            setMode('walk', 0);
        } else {
            if (!state.idleSince) state.idleSince = now;
            setMode('idle', 1500 + Math.random() * 2500);
        }
    }

    // -------------------------------------------------------------------- tick
    function tick(t) {
        if (!pet) return;
        if (paused) { rafId = requestAnimationFrame(tick); return; }
        var dt = Math.min(0.05, (t - state.lastT) / 1000); // cap dt, handle first frame
        state.lastT = t;

        if (state.mode === 'walk') {
            var dx = state.targetX - state.x;
            var step = state.speed * dt * (dx < 0 ? -1 : 1);
            if (Math.abs(dx) <= Math.abs(step)) {
                state.x = state.targetX;
                state.idleSince = t;
                setMode('idle', 1500 + Math.random() * 2500);
            } else {
                state.x += step;
            }
            applyTransform();
        } else if (state.mode === 'fall') {
            state.fallVy += 1500 * dt;                       // gravity px/s^2
            state.y += state.fallVy * dt;
            var gy = groundY();
            if (state.y >= gy) {
                state.y = gy;
                state.fallVy = 0;
                state.idleSince = t;
                setMode('idle', 800);
            }
            applyTransform();
        } else if (state.mode === 'idle' || state.mode === 'sleep') {
            if (t >= state.modeUntil) pickNextIdleAction();
        } else if (state.mode === 'hop') {
            if (t >= state.modeUntil) {
                state.idleSince = t;
                setMode('idle', 1200);
            }
        }
        // 'drag' is driven entirely by mouse events; no per-frame logic here.

        rafId = requestAnimationFrame(tick);
    }

    // ---------------------------------------------------------- input handling
    function onMouseDown(e) {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        state.dragOffset = { x: e.clientX - state.x, y: e.clientY - state.y };
        // Wait for movement before transitioning to drag, so a plain click can hop.
        var moved = false;
        function onMove(ev) {
            moved = true;
            setMode('drag', 0);
            state.x = clampX(ev.clientX - state.dragOffset.x);
            state.y = Math.max(0, Math.min(window.innerHeight - HEIGHT, ev.clientY - state.dragOffset.y));
            applyTransform();
        }
        function onUp() {
            window.removeEventListener('mousemove', onMove);
            window.removeEventListener('mouseup', onUp);
            if (!moved) {
                // Pure click — happy hop.
                setMode('hop', 400);
            } else if (state.y < groundY()) {
                state.fallVy = 0;
                setMode('fall', 0);
            } else {
                state.idleSince = performance.now();
                setMode('idle', 600);
            }
        }
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
    }

    // -------------------------------------------------------------- lifecycle
    function mount() {
        if (pet) return;
        injectStyles();
        pet = document.createElement('div');
        pet.id = 'screen-pet';
        pet.innerHTML = svgMarkup();
        pet.addEventListener('mousedown', onMouseDown);
        document.body.appendChild(pet);
        // Position in the bottom-right region by default.
        state.y = groundY();
        state.x = clampX(Math.min(window.innerWidth - WIDTH - 24, 240));
        state.facing = 'left';
        state.idleSince = performance.now();
        setMode('idle', 800);
        state.lastT = performance.now();
        rafId = requestAnimationFrame(tick);
    }

    function unmount() {
        if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
        if (pet && pet.parentNode) pet.parentNode.removeChild(pet);
        pet = null;
    }

    function setEnabled(on) {
        try {
            if (on) localStorage.setItem(STORAGE_KEY, '1');
            else localStorage.removeItem(STORAGE_KEY);
        } catch (e) { /* private mode etc. — toggle still works in-memory */ }
        if (on) mount(); else unmount();
    }

    function isEnabled() {
        try { return localStorage.getItem(STORAGE_KEY) === '1'; }
        catch (e) { return false; }
    }

    // ----------------------------------------------------- viewport / lifecycle
    window.addEventListener('resize', function () {
        if (!pet) return;
        state.x = clampX(state.x);
        state.y = Math.min(state.y, groundY());
        applyTransform();
    });

    document.addEventListener('visibilitychange', function () {
        paused = document.hidden;
        // resync timestamp on resume so dt doesn't spike
        if (!paused) state.lastT = performance.now();
    });

    // Wire the nav checkbox + restore state on load.
    function init() {
        injectStyles();  // even when off, so the checkbox is styled
        var box = document.getElementById('screenPetToggle');
        if (box) {
            box.checked = isEnabled();
            box.addEventListener('change', function () {
                setEnabled(box.checked);
            });
        }
        if (isEnabled()) mount();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Public-ish handle for debugging.
    window.ScreenPet = { setEnabled: setEnabled, isEnabled: isEnabled };
})();
