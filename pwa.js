/**
 * SachScan Progressive Web App Client Controller
 * - Service worker registration & lifecycle monitoring
 * - Native desktop & mobile app install prompt handling
 * - Network status monitoring (Online / Offline sovereign toasts)
 * - Standalone mode detection & UI optimization
 */
(function initSachScanPWA() {
  'use strict';

  let deferredPrompt = null;
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;

  // 1. Register Service Worker
  // 1. Register Service Worker with instant update activation
  if ('serviceWorker' in navigator) {
    let refreshing = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (!refreshing) {
        refreshing = true;
        window.location.reload();
      }
    });

    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/' })
        .then((registration) => {
          console.log('[SachScan PWA] Service Worker registered with scope:', registration.scope);
          registration.update();

          // If a worker is waiting, activate it immediately
          if (registration.waiting) {
            registration.waiting.postMessage({ type: 'SKIP_WAITING' });
          }

          // Check for new service worker updates
          registration.addEventListener('updatefound', () => {
            const installingWorker = registration.installing;
            if (installingWorker) {
              installingWorker.addEventListener('statechange', () => {
                if (installingWorker.state === 'installed' && navigator.serviceWorker.controller) {
                  installingWorker.postMessage({ type: 'SKIP_WAITING' });
                }
              });
            }
          });
        })
        .catch((error) => {
          console.error('[SachScan PWA] Service Worker registration failed:', error);
        });
    });
  }

  // 2. Custom Toast Notification Helper
  function showToast(message, type = 'info', duration = 4000) {
    let toastContainer = document.getElementById('pwa-toast-container');
    if (!toastContainer) {
      toastContainer = document.createElement('div');
      toastContainer.id = 'pwa-toast-container';
      toastContainer.style.cssText = `
        position: fixed;
        bottom: 24px;
        left: 50%;
        transform: translateX(-50%);
        z-index: 999999;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 8px;
        pointer-events: none;
      `;
      document.body.appendChild(toastContainer);
    }

    const toast = document.createElement('div');
    const isError = type === 'offline' || type === 'error';
    const isSuccess = type === 'online' || type === 'success';

    const bg = isError ? 'rgba(26, 26, 30, 0.96)' : isSuccess ? 'rgba(0, 33, 19, 0.96)' : 'rgba(15, 17, 21, 0.95)';
    const borderColor = isError ? 'rgba(255, 120, 120, 0.25)' : isSuccess ? 'rgba(111, 251, 190, 0.3)' : 'rgba(255, 255, 255, 0.15)';
    const accentColor = isError ? '#ffdad6' : isSuccess ? '#6ffbbe' : '#ffffff';

    toast.style.cssText = `
      background: ${bg};
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      color: #ffffff;
      border: 1px solid ${borderColor};
      padding: 10px 18px;
      border-radius: 9999px;
      font-family: 'Geist', 'Inter', -apple-system, sans-serif;
      font-size: 13px;
      font-weight: 500;
      box-shadow: 0 12px 32px rgba(0, 0, 0, 0.28);
      opacity: 0;
      transform: translateY(14px) scale(0.96);
      transition: opacity 0.28s cubic-bezier(0.16, 1, 0.3, 1), transform 0.28s cubic-bezier(0.16, 1, 0.3, 1);
      display: flex;
      align-items: center;
      gap: 10px;
      pointer-events: auto;
    `;

    const iconName = isError ? 'wifi_off' : isSuccess ? 'wifi' : 'verified';
    toast.innerHTML = `
      <span class="material-symbols-outlined" style="font-size: 18px; color: ${accentColor};">${iconName}</span>
      <span>${message}</span>
    `;

    toastContainer.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => {
      toast.style.opacity = '1';
      toast.style.transform = 'translateY(0) scale(1)';
    });

    // Animate out
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px) scale(0.96)';
      setTimeout(() => {
        if (toast.parentElement) toast.remove();
      }, 300);
    }, duration);
  }

  // 3. Online / Offline Monitoring
  window.addEventListener('offline', () => {
    showToast('Offline Mode Active — Local Forensic Engine Ready', 'offline', 5000);
  });

  window.addEventListener('online', () => {
    showToast('Reconnected — Cloud Node Cluster Synchronized', 'online', 4000);
  });

  // 4. Custom App Installation Experience
  function createInstallUI() {
    if (isStandalone) return; // Do not show install button if already running as standalone app

    // A. Add "Install App" button into top navbar
    const navBarActionGroup = document.querySelector('#main-navbar .flex.items-center.gap-3') ||
                              document.querySelector('#main-navbar .flex.items-center.gap-space-sm') ||
                              document.querySelector('header .flex.items-center.gap-3');

    if (navBarActionGroup && !document.getElementById('pwa-nav-install-btn')) {
      const installBtn = document.createElement('button');
      installBtn.id = 'pwa-nav-install-btn';
      installBtn.type = 'button';
      installBtn.className = 'hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-black/15 bg-white/70 hover:bg-black hover:text-white text-black text-xs font-semibold tracking-wide transition-all shadow-sm';
      installBtn.title = 'Install SachScan Desktop / Mobile App';
      installBtn.innerHTML = `
        <span class="material-symbols-outlined text-[16px]">download</span>
        <span>Install App</span>
      `;

      installBtn.addEventListener('click', triggerInstallPrompt);
      navBarActionGroup.insertBefore(installBtn, navBarActionGroup.firstChild);
    }

    // B. Add Floating Sovereign Install Pill (Bottom Right)
    if (!document.getElementById('pwa-install-banner')) {
      const banner = document.createElement('div');
      banner.id = 'pwa-install-banner';
      banner.style.cssText = `
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 99998;
        background: rgba(255, 255, 255, 0.88);
        backdrop-filter: blur(28px) saturate(180%);
        -webkit-backdrop-filter: blur(28px) saturate(180%);
        border: 1px solid rgba(0, 0, 0, 0.12);
        box-shadow: 0 16px 40px -8px rgba(0, 0, 0, 0.16), 0 0 0 1px rgba(255, 255, 255, 0.6) inset;
        border-radius: 20px;
        padding: 12px 16px;
        display: flex;
        align-items: center;
        gap: 12px;
        max-width: 380px;
        animation: pwaBannerSlideIn 0.45s cubic-bezier(0.16, 1, 0.3, 1) forwards;
      `;

      banner.innerHTML = `
        <img src="/icons/icon-192.png" alt="SachScan App" style="width: 38px; height: 38px; border-radius: 10px; object-fit: contain; box-shadow: 0 2px 8px rgba(0,0,0,0.1);" />
        <div style="flex: 1; min-width: 0;">
          <div style="font-family: 'Geist', sans-serif; font-size: 13px; font-weight: 700; color: #000; line-height: 1.2;">SachScan App</div>
          <div style="font-family: 'Inter', sans-serif; font-size: 11px; color: #555; line-height: 1.3;">Instant offline access & faster scans</div>
        </div>
        <button id="pwa-banner-install-btn" style="background: #000; color: #fff; border: none; padding: 7px 13px; border-radius: 12px; font-family: 'Geist', sans-serif; font-size: 12px; font-weight: 600; cursor: pointer; transition: background 0.18s ease;">Install</button>
        <button id="pwa-banner-close-btn" style="background: transparent; border: none; color: #888; font-size: 16px; cursor: pointer; padding: 4px; display: flex; align-items: center; justify-content: center;" title="Dismiss">✕</button>
      `;

      document.body.appendChild(banner);

      document.getElementById('pwa-banner-install-btn').addEventListener('click', triggerInstallPrompt);
      document.getElementById('pwa-banner-close-btn').addEventListener('click', () => {
        banner.style.opacity = '0';
        banner.style.transform = 'translateY(16px)';
        setTimeout(() => banner.remove(), 250);
      });
    }
  }

  // 5. Trigger Native Install Prompt
  async function triggerInstallPrompt() {
    if (!deferredPrompt) {
      // If browser doesn't expose deferred prompt (e.g. Safari iOS), give instructions
      if (/iPad|iPhone|iPod/.test(navigator.userAgent)) {
        showToast('To install: tap the Share button and select "Add to Home Screen"', 'info', 6000);
      } else {
        showToast('App is ready. Look for the install icon in your browser address bar.', 'info', 4000);
      }
      return;
    }

    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    console.log(`[SachScan PWA] Install choice outcome: ${outcome}`);

    deferredPrompt = null;
    const banner = document.getElementById('pwa-install-banner');
    if (banner) banner.remove();
  }

  // 6. Listen for beforeinstallprompt event
  window.addEventListener('beforeinstallprompt', (e) => {
    // Prevent the mini-infobar from appearing on mobile
    e.preventDefault();
    deferredPrompt = e;
    console.log('[SachScan PWA] beforeinstallprompt event captured');
    createInstallUI();
  });

  // 7. Listen for appinstalled event
  window.addEventListener('appinstalled', () => {
    console.log('[SachScan PWA] SachScan application was installed');
    deferredPrompt = null;
    const banner = document.getElementById('pwa-install-banner');
    if (banner) banner.remove();
    const navBtn = document.getElementById('pwa-nav-install-btn');
    if (navBtn) navBtn.remove();
    showToast('SachScan Sovereign App installed successfully!', 'success', 5000);
  });

  // Inject animation keyframe for install pill banner
  if (!document.getElementById('pwa-styles')) {
    const styleEl = document.createElement('style');
    styleEl.id = 'pwa-styles';
    styleEl.textContent = `
      @keyframes pwaBannerSlideIn {
        0% { opacity: 0; transform: translateY(20px) scale(0.95); }
        100% { opacity: 1; transform: translateY(0) scale(1); }
      }
    `;
    document.head.appendChild(styleEl);
  }

  // Expose helper on window
  window.triggerSachScanInstall = triggerInstallPrompt;
})();
