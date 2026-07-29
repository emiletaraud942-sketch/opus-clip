// Bannière « Installer l'app » discrète.
// - Android/Chrome : capture l'événement beforeinstallprompt et propose un
//   vrai bouton d'installation natif.
// - iOS/Safari : pas d'API d'installation → on affiche une mini-consigne
//   (Partager → Sur l'écran d'accueil).
// Ne s'affiche jamais si l'app est déjà installée (mode standalone) ou si
// l'utilisateur a déjà fermé la bannière (mémorisé 30 jours).

const DISMISS_KEY = 'sc_install_dismissed_until';

function dismissed() {
  try {
    const until = Number(localStorage.getItem(DISMISS_KEY) || 0);
    return Date.now() < until;
  } catch { return false; }
}
function remember() {
  try { localStorage.setItem(DISMISS_KEY, String(Date.now() + 30 * 864e5)); } catch {}
}
function isStandalone() {
  return window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
}
function isIOS() {
  return /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
}

let deferredPrompt = null;

function banner(innerHTML) {
  if (document.getElementById('sc-install')) return null;
  const el = document.createElement('div');
  el.id = 'sc-install';
  el.style.cssText = [
    'position:fixed', 'left:12px', 'right:12px',
    'bottom:calc(12px + env(safe-area-inset-bottom))',
    'max-width:440px', 'margin:0 auto', 'z-index:9999',
    'background:#17171d', 'border:1px solid #2a2a35', 'border-radius:16px',
    'box-shadow:0 12px 40px rgba(0,0,0,.5)', 'padding:12px 14px',
    'display:flex', 'align-items:center', 'gap:12px',
    "font-family:'Inter',sans-serif", 'color:#fff',
  ].join(';');
  el.innerHTML = innerHTML;
  document.body.appendChild(el);
  return el;
}

const icon = `<img src="/icon-192.png" alt="" width="40" height="40" style="border-radius:10px;flex-shrink:0;">`;
const closeBtn = `<button data-close aria-label="Fermer" style="background:none;border:0;color:#6b7280;font-size:20px;line-height:1;cursor:pointer;padding:0 2px;flex-shrink:0;">×</button>`;

function wireClose(el) {
  el.querySelector('[data-close]').addEventListener('click', () => { remember(); el.remove(); });
}

// --- Android / Chrome : installation native ---
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  if (isStandalone() || dismissed()) return;
  const el = banner(`
    ${icon}
    <div style="flex:1;min-width:0;">
      <div style="font-size:14px;font-weight:700;">Installer Sortclip</div>
      <div style="font-size:12px;color:#9ca3af;">Accès direct depuis ton écran d'accueil, en plein écran.</div>
    </div>
    <button data-install style="background:#f43f8e;color:#fff;border:0;font-size:13px;font-weight:700;padding:9px 16px;border-radius:9999px;cursor:pointer;flex-shrink:0;">Installer</button>
    ${closeBtn}`);
  if (!el) return;
  wireClose(el);
  el.querySelector('[data-install]').addEventListener('click', async () => {
    if (!deferredPrompt) { el.remove(); return; }
    deferredPrompt.prompt();
    try { await deferredPrompt.userChoice; } catch {}
    deferredPrompt = null;
    remember();
    el.remove();
  });
});

// L'app vient d'être installée : on nettoie et on ne repropose plus.
window.addEventListener('appinstalled', () => {
  remember();
  const el = document.getElementById('sc-install');
  if (el) el.remove();
});

// --- iOS / Safari : consigne manuelle (pas d'API d'installation) ---
window.addEventListener('load', () => {
  if (!isIOS() || isStandalone() || dismissed()) return;
  // Laisse quelques secondes avant de proposer, pour ne pas gêner d'emblée.
  setTimeout(() => {
    if (isStandalone() || dismissed() || document.getElementById('sc-install')) return;
    const el = banner(`
      ${icon}
      <div style="flex:1;min-width:0;">
        <div style="font-size:14px;font-weight:700;">Ajouter Sortclip à l'écran d'accueil</div>
        <div style="font-size:12px;color:#9ca3af;">Appuie sur <strong style="color:#fff;">Partager</strong> puis <strong style="color:#fff;">« Sur l'écran d'accueil »</strong>.</div>
      </div>
      ${closeBtn}`);
    if (el) wireClose(el);
  }, 4000);
});
