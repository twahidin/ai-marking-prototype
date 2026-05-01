// Companion turtle — opt-in screen pet for the teacher's browser.
//
// - Off by default. Enabled via the discreet checkbox in the nav.
// - Wanders along the bottom of the viewport: idle <-> walk.
// - Sleeps when the teacher's mouse hasn't moved for 20s; wakes the moment
//   the mouse moves again or the turtle is clicked / dragged.
// - Plain click on the turtle pops a small arc of bubble actions above it:
//   pet, feed lettuce, cherry blossom, water bath, tuck into bed.
// - Click-and-drag picks the turtle up; release falls back down.
// - Animations only interrupt on click or drag of the turtle itself.
// - Pauses when the tab is hidden. Clamps to viewport on resize.
// - Persistence is per-browser via localStorage. No DB, no per-teacher config.
//
// Self-contained: injects its own styles, requires no external assets,
// adds nothing but a fixed-position <div> + transient effect nodes on body.
(function () {
    'use strict';

    var STORAGE_KEY = 'screen_pet_enabled';
    var WIDTH = 76;
    var HEIGHT = 64;
    var GROUND_PAD = 8;
    var SLEEP_AFTER_MS = 20000;     // teacher mouse-idle threshold
    var DRAG_THRESHOLD_PX = 5;      // movement before mousedown becomes a drag

    var pet = null;
    var menuEl = null;
    var bedEl = null;
    var rafId = null;
    var paused = false;
    var lastMouseMove = 0;          // performance.now() timestamp

    var state = {
        x: 200,
        y: 0,
        facing: 'left',             // 'left' = head pointed at -X (default SVG)
        mode: 'idle',               // idle | walk | sleep | drag | hop | fall | menu | busy | bed
        modeUntil: 0,
        targetX: 200,
        speed: 50,                  // px/sec
        dragOffset: null,
        fallVy: 0,
        lastT: 0,
    };

    // -------------------------------------------------------------- styles
    function injectStyles() {
        if (document.getElementById('screen-pet-styles')) return;
        var css = ''
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

            // pet container
            + '#screen-pet {'
            + '  position: fixed; left: 0; top: 0; width: ' + WIDTH + 'px; height: ' + HEIGHT + 'px;'
            + '  pointer-events: auto; user-select: none; -webkit-user-select: none;'
            + '  z-index: 9999; cursor: grab;'
            + '  will-change: transform;'
            + '}'
            + '#screen-pet[data-mode="drag"] { cursor: grabbing; }'
            + '#screen-pet svg { display: block; width: 100%; height: 100%; pointer-events: none; }'

            // walk leg cycle
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
            + '@keyframes screen-pet-shake {'
            + '  0%, 100% { transform: translateX(0); }'
            + '  25% { transform: translateX(-2px); }'
            + '  75% { transform: translateX(2px); }'
            + '}'
            + '@keyframes screen-pet-munch {'
            + '  0%, 100% { transform: translateX(0); }'
            + '  50% { transform: translateX(-3px); }'
            + '}'

            // transform-box defaults so transforms work on inner SVG nodes
            + '#screen-pet .leg, #screen-pet .turtle-head, #screen-pet .turtle-tail, '
            + '#screen-pet .eye, #screen-pet .turtle-svg {'
            + '  transform-origin: center; transform-box: fill-box;'
            + '}'

            // mode-driven activations
            + '#screen-pet[data-mode="idle"] .eye-open { animation: screen-pet-blink 4s infinite; }'
            + '#screen-pet[data-mode="walk"] .leg-a { animation: screen-pet-leg-step 0.7s ease-in-out infinite; }'
            + '#screen-pet[data-mode="walk"] .leg-b { animation: screen-pet-leg-step 0.7s ease-in-out infinite; animation-delay: -0.35s; }'
            + '#screen-pet[data-mode="walk"] .turtle-head { animation: screen-pet-head-poke 0.7s ease-in-out infinite; }'
            + '#screen-pet[data-mode="walk"] .turtle-tail { animation: screen-pet-tail-wag 0.7s ease-in-out infinite; transform-origin: 78px 56px; }'
            + '#screen-pet .eye-closed, #screen-pet .eye-happy { display: none; }'
            + '#screen-pet[data-mode="sleep"] .eye-open, #screen-pet[data-mode="bed"] .eye-open { display: none; }'
            + '#screen-pet[data-mode="sleep"] .eye-closed, #screen-pet[data-mode="bed"] .eye-closed { display: block; }'
            + '#screen-pet.happy .eye-open { display: none; }'
            + '#screen-pet.happy .eye-happy { display: block; }'
            + '#screen-pet .z-bubble-group { display: none; }'
            + '#screen-pet[data-mode="sleep"] .z-bubble-group, #screen-pet[data-mode="bed"] .z-bubble-group { display: block; }'
            + '#screen-pet[data-mode="sleep"] .z-bubble, #screen-pet[data-mode="bed"] .z-bubble { animation: screen-pet-z-float 1.6s ease-out infinite; }'
            + '#screen-pet[data-mode="hop"] .turtle-svg { animation: screen-pet-hop 0.4s ease-out; }'
            + '#screen-pet.shake .turtle-svg { animation: screen-pet-shake 0.18s ease-in-out 4; }'
            + '#screen-pet.munch .turtle-head { animation: screen-pet-munch 0.32s ease-in-out 3; }'

            // ---------- bubble menu ----------
            + '.screen-pet-menu {'
            + '  position: fixed; left: 0; top: 0; width: 0; height: 0; pointer-events: none;'
            + '  z-index: 10000;'
            + '}'
            + '.screen-pet-bubble {'
            + '  position: absolute; width: 40px; height: 40px; border-radius: 50%;'
            + '  background: white; box-shadow: 0 4px 14px rgba(0,0,0,0.18);'
            + '  display: flex; align-items: center; justify-content: center;'
            + '  cursor: pointer; pointer-events: auto;'
            + '  transform: translate(-50%, -50%) scale(0); opacity: 0;'
            + '  animation: screen-pet-bubble-pop 0.22s cubic-bezier(.5,1.4,.5,1) forwards;'
            + '  transition: box-shadow 0.15s, background 0.15s;'
            + '}'
            + '.screen-pet-bubble:hover { background: #f8fbf6; box-shadow: 0 6px 18px rgba(0,0,0,0.24); }'
            + '.screen-pet-bubble svg { width: 22px; height: 22px; display: block; }'
            + '@keyframes screen-pet-bubble-pop {'
            + '  0%   { transform: translate(-50%, -50%) scale(0); opacity: 0; }'
            + '  100% { transform: translate(-50%, -50%) scale(1); opacity: 1; }'
            + '}'

            // ---------- effect: heart (pet) ----------
            + '.screen-pet-fx-heart {'
            + '  position: fixed; pointer-events: none; z-index: 9998;'
            + '  width: 22px; height: 22px;'
            + '  animation: screen-pet-heart-rise 1.4s ease-out forwards;'
            + '}'
            + '@keyframes screen-pet-heart-rise {'
            + '  0%   { transform: translate(-50%, -50%) scale(0.4); opacity: 0; }'
            + '  20%  { transform: translate(-50%, -50%) scale(1.1); opacity: 1; }'
            + '  100% { transform: translate(-50%, -130%) scale(0.7); opacity: 0; }'
            + '}'

            // ---------- effect: lettuce (food drops + shrinks) ----------
            + '.screen-pet-fx-lettuce {'
            + '  position: fixed; pointer-events: none; z-index: 9998;'
            + '  width: 28px; height: 28px;'
            + '  animation: screen-pet-lettuce-drop 0.6s cubic-bezier(.4,1.6,.6,1) forwards;'
            + '}'
            + '.screen-pet-fx-lettuce.eaten {'
            + '  animation: screen-pet-lettuce-eat 1.6s ease-in forwards;'
            + '}'
            + '@keyframes screen-pet-lettuce-drop {'
            + '  0%   { transform: translate(-50%, calc(-50% - 80px)) rotate(-15deg); opacity: 0; }'
            + '  60%  { transform: translate(-50%, -50%) rotate(8deg); opacity: 1; }'
            + '  100% { transform: translate(-50%, -50%) rotate(0deg); opacity: 1; }'
            + '}'
            + '@keyframes screen-pet-lettuce-eat {'
            + '  0%   { transform: translate(-50%, -50%) scale(1); opacity: 1; }'
            + '  33%  { transform: translate(-50%, -50%) scale(0.7); opacity: 0.85; }'
            + '  66%  { transform: translate(-50%, -50%) scale(0.4); opacity: 0.6; }'
            + '  100% { transform: translate(-50%, -50%) scale(0); opacity: 0; }'
            + '}'

            // ---------- effect: cherry blossom petals ----------
            + '.screen-pet-fx-petal {'
            + '  position: fixed; pointer-events: none; z-index: 9998;'
            + '  width: 14px; height: 14px;'
            + '}'
            + '@keyframes screen-pet-petal-fall {'
            + '  0%   { transform: translate(-50%, -50%) rotate(0deg); opacity: 0; }'
            + '  10%  { opacity: 1; }'
            + '  100% { transform: translate(calc(-50% + var(--drift, 0px)), calc(-50% + var(--fall, 200px))) rotate(360deg); opacity: 0; }'
            + '}'

            // ---------- effect: water droplets ----------
            + '.screen-pet-fx-drop {'
            + '  position: fixed; pointer-events: none; z-index: 9998;'
            + '  width: 10px; height: 14px;'
            + '}'
            + '@keyframes screen-pet-drop-fall {'
            + '  0%   { transform: translate(-50%, calc(-50% - 80px)); opacity: 0; }'
            + '  20%  { opacity: 1; }'
            + '  100% { transform: translate(-50%, -50%); opacity: 0; }'
            + '}'

            // ---------- effect: bed (stays until interaction) ----------
            + '.screen-pet-fx-bed {'
            + '  position: fixed; pointer-events: none; z-index: 9997;'
            + '  width: 110px; height: 26px;'
            + '  animation: screen-pet-bed-in 0.35s ease-out;'
            + '}'
            + '@keyframes screen-pet-bed-in {'
            + '  0%   { transform: translate(-50%, calc(-50% + 30px)) scale(0.8); opacity: 0; }'
            + '  100% { transform: translate(-50%, -50%) scale(1); opacity: 1; }'
            + '}'
            + '';
        var style = document.createElement('style');
        style.id = 'screen-pet-styles';
        style.textContent = css;
        document.head.appendChild(style);
    }

    // ------------------------------------------------------------------- svg
    function turtleSvg() {
        return ''
            + '<svg class="turtle-svg" viewBox="0 0 96 80" xmlns="http://www.w3.org/2000/svg">'
            +   '<ellipse cx="48" cy="74" rx="28" ry="3" fill="rgba(0,0,0,0.18)"/>'
            +   '<ellipse class="leg leg-b" cx="28" cy="58" rx="4" ry="3.5" fill="#8fc486" opacity="0.7"/>'
            +   '<ellipse class="leg leg-a" cx="68" cy="58" rx="4" ry="3.5" fill="#8fc486" opacity="0.7"/>'
            +   '<path class="turtle-tail" d="M 78 54 q 8 0 9 4 q -2 2 -9 1 z" fill="#a8d99b"/>'
            +   '<ellipse cx="48" cy="60" rx="26" ry="10" fill="#f3e7c1"/>'
            +   '<path d="M 22 56 q 0 -28 26 -28 q 26 0 26 28 z" fill="#6cc788"/>'
            +   '<path d="M 22 56 q 0 -28 26 -28 q 26 0 26 28 z" fill="none" stroke="#4fa56d" stroke-width="2"/>'
            +   '<path d="M 48 32 l 8 5 l 0 9 l -8 5 l -8 -5 l 0 -9 z" fill="#4fa56d" opacity="0.55"/>'
            +   '<path d="M 32 41 l 6 4 l 0 8 l -6 3 z" fill="#4fa56d" opacity="0.45"/>'
            +   '<path d="M 64 41 l -6 4 l 0 8 l 6 3 z" fill="#4fa56d" opacity="0.45"/>'
            +   '<ellipse class="leg leg-a" cx="22" cy="62" rx="6" ry="5" fill="#a8d99b"/>'
            +   '<ellipse class="leg leg-b" cx="72" cy="62" rx="6" ry="5" fill="#a8d99b"/>'
            +   '<g class="turtle-head">'
            +     '<circle cx="14" cy="50" r="9" fill="#a8d99b"/>'
            +     '<circle cx="11" cy="52" r="2" fill="#ffb1b1" opacity="0.7"/>'
            +     '<ellipse class="eye eye-open" cx="11" cy="48" rx="1.4" ry="2" fill="#222"/>'
            +     '<circle class="eye eye-open" cx="10.6" cy="47.3" r="0.5" fill="white"/>'
            +     '<path class="eye-closed" d="M 9 48.5 q 2 -1.5 4 0" fill="none" stroke="#222" stroke-width="0.9" stroke-linecap="round"/>'
            +     '<path class="eye-happy" d="M 9 49 q 2 -2.2 4 0" fill="none" stroke="#222" stroke-width="0.9" stroke-linecap="round"/>'
            +     '<path class="mouth" d="M 9 51.5 q 1.5 1 3 0" fill="none" stroke="#222" stroke-width="0.7" stroke-linecap="round"/>'
            +   '</g>'
            +   '<g class="z-bubble-group">'
            +     '<text class="z-bubble" x="22" y="38" font-size="9" fill="#7a8c9c" font-family="serif" font-style="italic">z</text>'
            +     '<text x="26" y="34" font-size="6" fill="#7a8c9c" font-family="serif" font-style="italic" opacity="0.6">z</text>'
            +   '</g>'
            + '</svg>';
    }

    // Bubble icons — each renders inside a 22x22 SVG.
    var ICONS = {
        pet: '<svg viewBox="0 0 24 24"><path d="M12 21s-7-4.5-7-11a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 6.5-7 11-7 11z" fill="#ff7c9c"/></svg>',
        lettuce: '<svg viewBox="0 0 24 24"><path d="M12 3 C6 5 4 12 12 21 C20 12 18 5 12 3 Z" fill="#7dc879" stroke="#4fa56d" stroke-width="1"/><path d="M12 6 L12 19" stroke="#4fa56d" stroke-width="1" stroke-linecap="round"/></svg>',
        blossom: '<svg viewBox="0 0 24 24">'
            + '<g transform="translate(12 12)">'
            + '<ellipse cx="0" cy="-6" rx="3.5" ry="5" fill="#ffc1d6"/>'
            + '<ellipse cx="6" cy="-2" rx="3.5" ry="5" fill="#ffc1d6" transform="rotate(72 6 -2)"/>'
            + '<ellipse cx="3.7" cy="5" rx="3.5" ry="5" fill="#ffc1d6" transform="rotate(144 3.7 5)"/>'
            + '<ellipse cx="-3.7" cy="5" rx="3.5" ry="5" fill="#ffc1d6" transform="rotate(216 -3.7 5)"/>'
            + '<ellipse cx="-6" cy="-2" rx="3.5" ry="5" fill="#ffc1d6" transform="rotate(288 -6 -2)"/>'
            + '<circle cx="0" cy="0" r="2" fill="#ffe680"/>'
            + '</g></svg>',
        water: '<svg viewBox="0 0 24 24"><path d="M12 3 C 8 8 5 13 5 16 a 7 7 0 0 0 14 0 c 0 -3 -3 -8 -7 -13 z" fill="#7ec1ed"/><path d="M9 14 q 0.5 2.5 3 3" fill="none" stroke="white" stroke-width="1.4" stroke-linecap="round"/></svg>',
        bed: '<svg viewBox="0 0 24 24"><rect x="2" y="13" width="20" height="6" rx="1" fill="#c6957a"/><rect x="2" y="9" width="6" height="6" rx="1.5" fill="#fff5e0" stroke="#c6957a" stroke-width="1"/><rect x="3" y="14" width="18" height="2" fill="#7d5a48" opacity="0.4"/><path d="M2 19 l20 0" stroke="#7d5a48" stroke-width="1.2"/></svg>',
    };

    // -------------------------------------------------- positioning helpers
    function groundY() { return Math.max(0, window.innerHeight - HEIGHT - GROUND_PAD); }
    function clampX(x) { return Math.max(8, Math.min(window.innerWidth - WIDTH - 8, x)); }

    function applyTransform() {
        if (!pet) return;
        var sx = state.facing === 'left' ? 1 : -1;
        if (sx === 1) pet.style.transform = 'translate(' + state.x + 'px, ' + state.y + 'px)';
        else pet.style.transform = 'translate(' + state.x + 'px, ' + state.y + 'px) scaleX(-1)';
        pet.dataset.mode = state.mode;
        if (bedEl) positionBed();
    }

    // ---------------------------------------------------------- mode helpers
    function setMode(mode, durationMs) {
        state.mode = mode;
        state.modeUntil = performance.now() + (durationMs || 0);
        applyTransform();
    }

    function pickWalkTarget() {
        var minDist = 80, maxDist = 280;
        var dist = minDist + Math.random() * (maxDist - minDist);
        var dir = Math.random() < 0.5 ? -1 : 1;
        if (state.x < window.innerWidth * 0.25) dir = 1;
        else if (state.x > window.innerWidth * 0.75) dir = -1;
        var target = clampX(state.x + dir * dist);
        state.targetX = target;
        state.facing = (target < state.x) ? 'left' : 'right';
    }

    function pickNextIdleAction() {
        // 70% walk, 30% short idle. Sleep is mouse-idle-driven now, not random.
        if (Math.random() < 0.7) {
            pickWalkTarget();
            setMode('walk', 0);
        } else {
            setMode('idle', 1500 + Math.random() * 2500);
        }
    }

    // -------------------------------------------------------- bubble menu
    function turtleCenter() {
        return { cx: state.x + WIDTH / 2, cy: state.y + HEIGHT / 2 };
    }

    function openMenu() {
        if (menuEl) return;
        closeBed();          // opening menu interrupts the bed
        clearAllEffects();   // and any in-flight animation effects
        setMode('menu', 0);

        var c = turtleCenter();
        var arcCenterY = state.y;       // top of turtle
        var radius = 78;
        var items = ['pet', 'lettuce', 'blossom', 'water', 'bed'];
        var n = items.length;
        // Spread 5 bubbles in a 120° arc above the turtle (-60° .. +60° from up)
        var spread = 120 * Math.PI / 180;
        var startA = -spread / 2;
        var stepA = spread / (n - 1);

        menuEl = document.createElement('div');
        menuEl.className = 'screen-pet-menu';
        document.body.appendChild(menuEl);

        items.forEach(function (id, i) {
            var angle = startA + stepA * i; // 0 = straight up
            var bx = c.cx + radius * Math.sin(angle);
            var by = arcCenterY - radius * Math.cos(angle);
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'screen-pet-bubble';
            b.dataset.action = id;
            b.title = ({ pet: 'Pet it', lettuce: 'Feed lettuce', blossom: 'Cherry blossom', water: 'Water bath', bed: 'Tuck in' })[id];
            b.style.left = bx + 'px';
            b.style.top = by + 'px';
            b.style.animationDelay = (i * 0.04) + 's';
            b.innerHTML = ICONS[id];
            b.addEventListener('click', function (ev) {
                ev.stopPropagation();
                runAction(id);
            });
            menuEl.appendChild(b);
        });

        // Click outside the menu (and outside the turtle) closes it.
        setTimeout(function () { document.addEventListener('mousedown', outsideMenuClose, true); }, 0);
    }

    function outsideMenuClose(ev) {
        if (!menuEl) return;
        if (menuEl.contains(ev.target)) return;
        if (pet && pet.contains(ev.target)) return; // turtle handles itself
        closeMenu();
    }

    function closeMenu() {
        document.removeEventListener('mousedown', outsideMenuClose, true);
        if (menuEl && menuEl.parentNode) menuEl.parentNode.removeChild(menuEl);
        menuEl = null;
    }

    // ------------------------------------------------------------ effects
    var effectEls = [];

    function spawn(cls, html, x, y, ttl, extraStyle) {
        var el = document.createElement('div');
        el.className = cls;
        el.innerHTML = html;
        el.style.left = x + 'px';
        el.style.top = y + 'px';
        if (extraStyle) for (var k in extraStyle) el.style.setProperty(k, extraStyle[k]);
        document.body.appendChild(el);
        effectEls.push(el);
        if (ttl) setTimeout(function () { removeEffect(el); }, ttl);
        return el;
    }

    function removeEffect(el) {
        var i = effectEls.indexOf(el);
        if (i >= 0) effectEls.splice(i, 1);
        if (el && el.parentNode) el.parentNode.removeChild(el);
    }

    function clearAllEffects() {
        effectEls.slice().forEach(removeEffect);
    }

    // Helper to wrap timeouts so they're cancelled if interrupted.
    var pendingTimers = [];
    function later(fn, ms) {
        var id = setTimeout(function () {
            var i = pendingTimers.indexOf(id);
            if (i >= 0) pendingTimers.splice(i, 1);
            fn();
        }, ms);
        pendingTimers.push(id);
        return id;
    }
    function clearTimers() {
        pendingTimers.slice().forEach(clearTimeout);
        pendingTimers.length = 0;
    }

    // -------------------------------------------- action handlers (animations)
    function runAction(id) {
        closeMenu();
        clearTimers();
        clearAllEffects();
        if (id === 'pet') doPet();
        else if (id === 'lettuce') doLettuce();
        else if (id === 'blossom') doBlossom();
        else if (id === 'water') doWater();
        else if (id === 'bed') doBed();
    }

    function doPet() {
        setMode('busy', 0);
        if (pet) pet.classList.add('happy');
        var c = turtleCenter();
        // 3 hearts staggered
        for (var i = 0; i < 3; i++) (function (i) {
            later(function () {
                spawn(
                    'screen-pet-fx-heart',
                    '<svg viewBox="0 0 24 24" style="width:100%;height:100%"><path d="M12 21s-7-4.5-7-11a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 6.5-7 11-7 11z" fill="#ff7c9c"/></svg>',
                    c.cx + (i - 1) * 12,
                    state.y - 6,
                    1500
                );
            }, i * 250);
        })(i);
        later(function () {
            if (pet) pet.classList.remove('happy');
            setMode('idle', 600);
        }, 1700);
    }

    function doLettuce() {
        setMode('busy', 0);
        var c = turtleCenter();
        var lettuceX = state.facing === 'left' ? state.x - 8 : state.x + WIDTH + 8;
        var lettuceY = state.y + HEIGHT - 18;
        var lettuce = spawn(
            'screen-pet-fx-lettuce',
            '<svg viewBox="0 0 24 24" style="width:100%;height:100%"><path d="M12 3 C6 5 4 12 12 21 C20 12 18 5 12 3 Z" fill="#7dc879" stroke="#4fa56d" stroke-width="1"/><path d="M12 6 L12 19" stroke="#4fa56d" stroke-width="1" stroke-linecap="round"/></svg>',
            lettuceX, lettuceY, 0
        );
        // After drop completes, start munch + eat animations
        later(function () {
            if (pet) pet.classList.add('munch');
            lettuce.classList.add('eaten');
        }, 600);
        later(function () {
            if (pet) pet.classList.remove('munch');
            removeEffect(lettuce);
            setMode('idle', 600);
        }, 2200);
    }

    function doBlossom() {
        setMode('busy', 0);
        var c = turtleCenter();
        var n = 12;
        var petalSvg = '<svg viewBox="0 0 24 24" style="width:100%;height:100%">'
            + '<path d="M12 2 C 8 6 8 12 12 16 C 16 12 16 6 12 2 Z" fill="#ffc1d6" stroke="#f199b8" stroke-width="0.8"/></svg>';
        for (var i = 0; i < n; i++) (function (i) {
            later(function () {
                var x = c.cx + (Math.random() - 0.5) * 220;
                var y = c.cy - 100 - Math.random() * 60;
                var drift = (Math.random() - 0.5) * 80;
                var fall = 160 + Math.random() * 120;
                var dur = 2.0 + Math.random() * 1.4;
                var el = spawn('screen-pet-fx-petal', petalSvg, x, y, dur * 1000 + 100, {
                    '--drift': drift + 'px',
                    '--fall': fall + 'px',
                    'animation': 'screen-pet-petal-fall ' + dur + 's linear forwards',
                });
            }, i * 120);
        })(i);
        later(function () { setMode('idle', 600); }, 3500);
    }

    function doWater() {
        setMode('busy', 0);
        var c = turtleCenter();
        var dropSvg = '<svg viewBox="0 0 24 24" style="width:100%;height:100%"><path d="M12 3 C 9 8 6 13 6 16 a 6 6 0 0 0 12 0 c 0 -3 -3 -8 -6 -13 z" fill="#7ec1ed"/><path d="M9 14 q 0.5 2 2.5 2.5" fill="none" stroke="white" stroke-width="1.2" stroke-linecap="round"/></svg>';
        // 6 droplets falling onto turtle
        for (var i = 0; i < 6; i++) (function (i) {
            later(function () {
                var x = state.x + 14 + Math.random() * (WIDTH - 28);
                var y = state.y + 14 + Math.random() * 16;
                spawn('screen-pet-fx-drop', dropSvg, x, y, 800, {
                    'animation': 'screen-pet-drop-fall 0.6s ease-in forwards',
                });
            }, i * 140);
        })(i);
        later(function () { if (pet) pet.classList.add('shake'); }, 200);
        later(function () { if (pet) pet.classList.remove('shake'); }, 1100);
        later(function () { setMode('idle', 600); }, 1800);
    }

    function doBed() {
        setMode('bed', 0);
        positionBed();
        var bedSvg = '<svg viewBox="0 0 110 26" style="width:100%;height:100%">'
            + '<rect x="2" y="10" width="106" height="14" rx="3" fill="#c6957a"/>'
            + '<rect x="2" y="6" width="22" height="14" rx="3" fill="#fff5e0" stroke="#c6957a" stroke-width="1.5"/>'
            + '<path d="M2 24 l106 0" stroke="#7d5a48" stroke-width="1.5"/>'
            + '<rect x="3" y="13" width="104" height="3" fill="#7d5a48" opacity="0.35"/>'
            + '</svg>';
        bedEl = document.createElement('div');
        bedEl.className = 'screen-pet-fx-bed';
        bedEl.innerHTML = bedSvg;
        document.body.appendChild(bedEl);
        positionBed();
    }

    function positionBed() {
        if (!bedEl) return;
        var c = turtleCenter();
        bedEl.style.left = c.cx + 'px';
        bedEl.style.top = (state.y + HEIGHT - 4) + 'px';
    }

    function closeBed() {
        if (!bedEl) return;
        if (bedEl.parentNode) bedEl.parentNode.removeChild(bedEl);
        bedEl = null;
    }

    // ------------------------------------------------- interrupt / wake-up
    function interruptToIdle() {
        clearTimers();
        clearAllEffects();
        closeBed();
        if (pet) { pet.classList.remove('happy', 'shake', 'munch'); }
        setMode('idle', 600);
    }

    function wakeFromSleep() {
        if (state.mode === 'sleep') setMode('idle', 600);
        else if (state.mode === 'bed') { closeBed(); setMode('idle', 600); }
    }

    // -------------------------------------------------------------------- tick
    function tick(t) {
        if (!pet) return;
        if (paused) { rafId = requestAnimationFrame(tick); return; }
        var dt = Math.min(0.05, (t - state.lastT) / 1000);
        state.lastT = t;

        if (state.mode === 'walk') {
            var dx = state.targetX - state.x;
            var step = state.speed * dt * (dx < 0 ? -1 : 1);
            if (Math.abs(dx) <= Math.abs(step)) {
                state.x = state.targetX;
                setMode('idle', 1500 + Math.random() * 2500);
            } else {
                state.x += step;
            }
            applyTransform();
        } else if (state.mode === 'fall') {
            state.fallVy += 1500 * dt;
            state.y += state.fallVy * dt;
            var gy = groundY();
            if (state.y >= gy) {
                state.y = gy;
                state.fallVy = 0;
                setMode('idle', 800);
            }
            applyTransform();
        } else if (state.mode === 'idle') {
            // Sleep when teacher's mouse has been idle long enough.
            if (t - lastMouseMove > SLEEP_AFTER_MS) {
                setMode('sleep', 0);
            } else if (t >= state.modeUntil) {
                pickNextIdleAction();
            }
        } else if (state.mode === 'hop') {
            if (t >= state.modeUntil) setMode('idle', 1200);
        }
        // 'menu', 'busy', 'sleep', 'bed', 'drag' are event-driven; no per-frame logic.

        rafId = requestAnimationFrame(tick);
    }

    // ---------------------------------------------------------- input handling
    function onMouseDown(e) {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        var startX = e.clientX, startY = e.clientY;
        var dragging = false;
        state.dragOffset = { x: startX - state.x, y: startY - state.y };

        function onMove(ev) {
            if (!dragging) {
                var dx = ev.clientX - startX, dy = ev.clientY - startY;
                if (dx * dx + dy * dy > DRAG_THRESHOLD_PX * DRAG_THRESHOLD_PX) {
                    dragging = true;
                    closeMenu();
                    interruptToIdle();
                    setMode('drag', 0);
                }
            }
            if (dragging) {
                state.x = clampX(ev.clientX - state.dragOffset.x);
                state.y = Math.max(0, Math.min(window.innerHeight - HEIGHT, ev.clientY - state.dragOffset.y));
                applyTransform();
            }
        }
        function onUp() {
            window.removeEventListener('mousemove', onMove);
            window.removeEventListener('mouseup', onUp);
            if (!dragging) {
                // Plain click on the turtle.
                if (state.mode === 'menu') {
                    closeMenu();
                    setMode('idle', 600);
                } else if (state.mode === 'sleep' || state.mode === 'bed') {
                    wakeFromSleep();
                    openMenu();
                } else if (state.mode === 'busy') {
                    interruptToIdle();
                    openMenu();
                } else {
                    openMenu();
                }
            } else {
                if (state.y < groundY()) {
                    state.fallVy = 0;
                    setMode('fall', 0);
                } else {
                    setMode('idle', 600);
                }
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
        pet.innerHTML = turtleSvg();
        pet.addEventListener('mousedown', onMouseDown);
        document.body.appendChild(pet);
        state.y = groundY();
        state.x = clampX(Math.min(window.innerWidth - WIDTH - 24, 240));
        state.facing = 'left';
        lastMouseMove = performance.now();
        setMode('idle', 800);
        state.lastT = performance.now();
        rafId = requestAnimationFrame(tick);
    }

    function unmount() {
        if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
        clearTimers();
        clearAllEffects();
        closeMenu();
        closeBed();
        if (pet && pet.parentNode) pet.parentNode.removeChild(pet);
        pet = null;
    }

    function setEnabled(on) {
        try {
            if (on) localStorage.setItem(STORAGE_KEY, '1');
            else localStorage.removeItem(STORAGE_KEY);
        } catch (e) {}
        if (on) mount(); else unmount();
    }

    function isEnabled() {
        try { return localStorage.getItem(STORAGE_KEY) === '1'; }
        catch (e) { return false; }
    }

    // ------------------------------------------------ viewport / global hooks
    document.addEventListener('mousemove', function () {
        lastMouseMove = performance.now();
        if (state.mode === 'sleep') setMode('idle', 600);
    });

    window.addEventListener('resize', function () {
        if (!pet) return;
        state.x = clampX(state.x);
        state.y = Math.min(state.y, groundY());
        applyTransform();
        if (menuEl) {
            // Reposition the menu to the turtle's new spot
            closeMenu();
            openMenu();
        }
    });

    document.addEventListener('visibilitychange', function () {
        paused = document.hidden;
        if (!paused) {
            state.lastT = performance.now();
            lastMouseMove = performance.now();   // don't snap-sleep on return
        }
    });

    function init() {
        injectStyles();
        var box = document.getElementById('screenPetToggle');
        if (box) {
            box.checked = isEnabled();
            box.addEventListener('change', function () { setEnabled(box.checked); });
        }
        if (isEnabled()) mount();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();

    window.ScreenPet = { setEnabled: setEnabled, isEnabled: isEnabled };
})();
