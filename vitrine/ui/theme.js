(() => {
  const key = 'vitrine-theme';
  const system = matchMedia('(prefers-color-scheme: light)');
  let preference = 'system';
  try { preference = localStorage.getItem(key) || 'system'; } catch {}
  const apply = () => {
    document.documentElement.dataset.theme = preference === 'system' ? (system.matches ? 'light' : 'dark') : preference;
    const theme = document.documentElement.dataset.theme;
    document.querySelectorAll('img[data-brand-asset]').forEach(el => {
      el.src = `/static/brand/vitrine-${el.dataset.brandAsset}-${theme}.svg`;
    });
    document.querySelectorAll('img[data-xr-lab-logo]').forEach(el => {
      el.src = theme === 'light' ? '/static/brand/xr-lab-black.png' : '/static/brand/xr-lab-white.png';
    });
    document.querySelectorAll('link[data-brand-icon]').forEach(el => {
      el.href = `/static/brand/vitrine-icon-${theme}.svg`;
    });
    document.querySelectorAll('[data-theme-select]').forEach(el => { el.value = preference; });
    document.querySelectorAll('[data-theme-choice]').forEach(el => {
      const on = el.dataset.themeChoice === preference;
      el.classList.toggle('is-active', on);
      el.setAttribute('aria-pressed', String(on));
    });
    window.dispatchEvent(new Event('themechange'));
  };
  apply();
  system.addEventListener('change', apply);
  window.addEventListener('storage', event => {
    if (event.key === key) { preference = event.newValue || 'system'; apply(); }
  });
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.querySelectorAll('[data-theme-select]').forEach(el => {
      el.value = preference;
      el.addEventListener('change', () => {
        preference = el.value;
        try { localStorage.setItem(key, preference); } catch {}
        apply();
      });
    });
    document.querySelectorAll('[data-theme-choice]').forEach(el => {
      el.addEventListener('click', () => {
        preference = el.dataset.themeChoice;
        try { localStorage.setItem(key, preference); } catch {}
        apply();
      });
    });
  });
})();
