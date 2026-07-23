// Navigation partagée entre toutes les pages du site (évite de dupliquer
// le même bloc <nav> dans chaque fichier HTML) + menu mobile responsive.
export function renderNav({ active = '', ctaVariant = 'default' } = {}) {
  const root = document.getElementById('nav-root');
  if (!root) return;

  const links = [
    { href: 'fonctionnalites.html', label: 'Fonctionnalités' },
    { href: 'tarifs.html', label: 'Tarifs' },
    { href: 'exemples.html', label: 'Exemples' },
  ];

  const linksHtml = links.map(l =>
    `<a href="${l.href}" class="nav-link${active === l.href ? ' nav-link-active' : ''}">${l.label}</a>`
  ).join('');

  const ctaHtml = ctaVariant === 'signup'
    ? `<span class="nav-hint">Déjà un compte ?</span><a href="login.html" class="btn-outline">Connexion</a>`
    : ctaVariant === 'login'
    ? `<span class="nav-hint">Pas encore de compte ?</span><a href="signup.html" class="btn-pink">S'inscrire</a>`
    : `<a href="login.html" class="nav-link">Connexion</a><a href="signup.html" class="btn-pink">S'inscrire</a>`;

  root.innerHTML = `
    <nav class="nav">
      <a href="index.html" class="nav-logo">
        <svg width="32" height="32" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
          <rect x="4" y="4" width="56" height="56" rx="16" fill="#7c3aed"/>
          <path d="M22 20 L22 44 L42 32 Z" fill="#0f0f14"/>
          <rect x="41" y="18" width="6" height="10" rx="3" fill="#0f0f14" transform="rotate(28 44 23)"/>
          <rect x="41" y="36" width="6" height="10" rx="3" fill="#0f0f14" transform="rotate(-28 44 41)"/>
        </svg>
        <span class="nav-brand">Sortclip</span>
      </a>
      <button class="nav-toggle" id="nav-toggle" aria-label="Ouvrir le menu">☰</button>
      <div class="nav-links" id="nav-links">
        ${linksHtml}
        <div class="nav-auth" id="auth-area">${ctaHtml}</div>
      </div>
    </nav>
  `;

  document.getElementById('nav-toggle').addEventListener('click', () => {
    document.getElementById('nav-links').classList.toggle('nav-links-open');
  });
}
