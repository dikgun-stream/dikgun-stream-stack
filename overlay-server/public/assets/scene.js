/* ============================================================================
   Shared scene runtime
   ----------------------------------------------------------------------------
   Handles everything the three waiting/BRB/ending screens have in common:
     - horizontal vs vertical composition
     - the live game/category label fed from the dashboard
     - countdown + wall clock
     - websocket wiring (config fanout and a placeholder event hook)

   Every scene connects on channel "scenes", which receives the full CONFIG on
   connect and whenever the dashboard changes something.
   ========================================================================= */

(() => {
  const params = new URLSearchParams(location.search);

  // --- orientation. Do NOT use container queries: CEF inside OBS lags Chrome,
  // and the browser-source viewport is already the canvas size.
  const vertical = params.get('v') === '1'
    || (window.innerHeight > window.innerWidth * 1.2);
  if (vertical) document.documentElement.classList.add('v');

  const state = {
    vertical,
    game: '',
    title: '',
    headline: '',
    subline: '',
  };

  // ---------------------------------------------------------------- helpers
  const pad = (n) => String(n).padStart(2, '0');
  const MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const DAYS = ['SUN','MON','TUE','WED','THU','FRI','SAT'];

  function stamp(d = new Date()) {
    return `${DAYS[d.getDay()]} ${pad(d.getDate())} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  }
  function clockText(d = new Date()) {
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  // ------------------------------------------------------------ game label
  function applyGame(game) {
    state.game = game || '';
    const text = state.game ? state.game.toUpperCase() : 'CATEGORY PENDING';
    for (const el of document.querySelectorAll('[data-game]')) {
      el.textContent = text;
    }
    // keep a stable width class so the layout does not jump between titles
    for (const el of document.querySelectorAll('[data-game-wrap]')) {
      el.classList.toggle('is-long', text.length > 18);
    }
  }

  function applyConfig(cfg) {
    if (!cfg) return;
    if (cfg.stream && typeof cfg.stream.game === 'string') applyGame(cfg.stream.game);
    if (cfg.stream && typeof cfg.stream.title === 'string') state.title = cfg.stream.title;

    const ss = cfg.starting_soon || {};
    if (typeof ss.headline === 'string' && ss.headline) state.headline = ss.headline;
    if (typeof ss.subline === 'string' && ss.subline) state.subline = ss.subline;

    for (const el of document.querySelectorAll('[data-headline]')) {
      el.textContent = (state.headline || 'STARTING SOON').toUpperCase();
    }
    for (const el of document.querySelectorAll('[data-subline]')) {
      el.textContent = state.subline || '';
    }
    for (const el of document.querySelectorAll('[data-goal-followers]')) {
      const g = (cfg.goals || {}).followers;
      if (g) el.textContent = `${Number(g.current || 0).toLocaleString('en-US')} / ${Number(g.target || 0).toLocaleString('en-US')}`;
    }
    if (cfg.starting_soon && Number.isFinite(Number(cfg.starting_soon.minutes))) {
      state.minutes = Number(cfg.starting_soon.minutes);
    }
    document.dispatchEvent(new CustomEvent('scene:config', { detail: cfg }));
  }

  // -------------------------------------------------------------- countdown
  function makeCountdown(el, barEl) {
    let endsAt = 0, total = 0, timer = null;

    function paint() {
      const left = Math.max(0, endsAt - Date.now());
      const s = Math.floor(left / 1000);
      const hh = Math.floor(s / 3600);
      const mm = Math.floor((s % 3600) / 60);
      const ss = s % 60;
      if (el) el.textContent = hh > 0 ? `${hh}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`;
      if (barEl && total > 0) {
        barEl.style.transform = `scaleX(${left / total})`;
      }
      if (left <= 0 && timer) { clearInterval(timer); timer = null; }
    }

    return {
      start(minutes) {
        const mins = Math.max(1, Number(minutes) || 5);
        total = mins * 60 * 1000;
        endsAt = Date.now() + total;
        paint();
        if (timer) clearInterval(timer);
        timer = setInterval(paint, 250);
      },
      remaining() { return Math.max(0, endsAt - Date.now()); },
    };
  }

  // -------------------------------------------------------------- bootstrap
  async function boot() {
    for (const el of document.querySelectorAll('[data-stamp]')) el.textContent = stamp();
    for (const el of document.querySelectorAll('[data-clock]')) el.textContent = clockText();
    setInterval(() => {
      for (const el of document.querySelectorAll('[data-clock]')) el.textContent = clockText();
    }, 15000);

    // marquee: duplicate the track so translateX(-50%) loops seamlessly
    for (const track of document.querySelectorAll('.marquee-track')) {
      if (track.dataset.dup === '1') continue;
      track.innerHTML += track.innerHTML;
      track.dataset.dup = '1';
      const seconds = Number(track.dataset.speed) || 48;
      track.style.animationDuration = `${seconds}s`;
    }

    try {
      const r = await fetch('/api/stream/info', { cache: 'no-store' });
      if (r.ok) applyConfig(await r.json());
    } catch { /* serve stale */ }

    // the /api/stream/info payload is flat; normalise it into the config shape
    try {
      const r = await fetch('/api/config', { cache: 'no-store' });
      if (r.ok) applyConfig(await r.json());
    } catch { /* endpoint may not exist */ }

    connect();
    document.dispatchEvent(new CustomEvent('scene:ready', { detail: state }));
  }

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    let ws;
    try {
      ws = new WebSocket(`${proto}://${location.host}/ws?role=overlay&ch=scenes`);
    } catch {
      return setTimeout(connect, 2000);
    }
    ws.onmessage = (e) => {
      let m; try { m = JSON.parse(e.data); } catch { return; }
      if (m.kind === 'config' && m.config) applyConfig(m.config);
      if (m.kind === 'event' && typeof m.type === 'string') {
        // Placeholder hook for gameplay-driven content: a kill or assist circle
        // appearing in game can fling an event at this scene instead.
        document.dispatchEvent(new CustomEvent('scene:event', { detail: m }));
      }
    };
    ws.onclose = () => setTimeout(connect, 1500);
    ws.onerror = () => { try { ws.close(); } catch {} };
  }

  window.Scene = { state, vertical, makeCountdown, stamp, clockText, applyGame };
  boot();
})();
