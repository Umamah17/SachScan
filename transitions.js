/**
 * SachScan Sovereign Navigation & Dissolve Transition Engine
 * 1. Smooth dissolve transitions between sections and pages
 * 2. Smart navbar scroll reveal: hides on scroll down, instantly glides back when scrolled up
 * 3. Handles in-page anchor crossfades and cross-page anchor landing
 */
(function initDissolveTransitions() {
  'use strict';

  // 1. Restore body opacity if restored from bfcache (browser back/forward button)
  window.addEventListener('pageshow', () => {
    document.body.classList.remove('page-dissolving-out');
  });

  // Helper to get active navbar element
  function getNavbar() {
    return document.getElementById('main-navbar') || document.querySelector('header');
  }

  // 2. In-Page Section Dissolve Transition
  function dissolveScrollTo(targetId) {
    if (!targetId) return;

    const navbar = getNavbar();
    if (navbar) {
      navbar.classList.remove('nav-hidden');
    }

    // Top / Home target
    if (targetId === 'top' || targetId === 'home' || targetId === 'animation-container') {
      const mainContainer = document.querySelector('main') || document.body;
      mainContainer.classList.add('section-dissolving-out');

      setTimeout(() => {
        const htmlEl = document.documentElement;
        const prevBehavior = htmlEl.style.scrollBehavior;
        htmlEl.style.scrollBehavior = 'auto';

        window.scrollTo({ top: 0, behavior: 'auto' });

        htmlEl.style.scrollBehavior = prevBehavior || '';
        mainContainer.classList.remove('section-dissolving-out');
        mainContainer.classList.add('section-dissolve-entering');
        if (navbar) navbar.classList.remove('nav-hidden');

        setTimeout(() => {
          mainContainer.classList.remove('section-dissolve-entering');
        }, 380);
      }, 150);
      return;
    }

    const targetEl = document.getElementById(targetId);
    if (!targetEl) return;

    const navHeight = navbar ? navbar.offsetHeight : 70;
    const targetPos = targetEl.getBoundingClientRect().top + window.pageYOffset - navHeight + 8;
    const mainContainer = document.querySelector('main') || document.body;

    // Phase 1: Subtle Dissolve Out
    mainContainer.classList.add('section-dissolving-out');

    setTimeout(() => {
      // Phase 2: Instant Position Swap during Dissolve Blur
      const htmlEl = document.documentElement;
      const prevBehavior = htmlEl.style.scrollBehavior;
      htmlEl.style.scrollBehavior = 'auto';

      window.scrollTo({
        top: Math.max(0, targetPos),
        behavior: 'auto'
      });

      htmlEl.style.scrollBehavior = prevBehavior || '';

      if (navbar) {
        navbar.classList.remove('nav-hidden');
      }

      mainContainer.classList.remove('section-dissolving-out');
      mainContainer.classList.add('section-dissolve-entering');

      // Trigger focal highlight on target element
      targetEl.classList.remove('section-dissolve-highlight');
      void targetEl.offsetWidth; // Force reflow
      targetEl.classList.add('section-dissolve-highlight');

      setTimeout(() => {
        mainContainer.classList.remove('section-dissolve-entering');
        targetEl.classList.remove('section-dissolve-highlight');
      }, 650);
    }, 150);
  }

  // 3. Smart Navbar Scroll Reveal Engine (Hide on scroll down, slide down on scroll up anywhere on page)
  function setupNavbarScrollBehavior() {
    const navbar = getNavbar();
    if (!navbar) return;

    let lastScrollY = Math.max(0, window.pageYOffset || document.documentElement.scrollTop || 0);
    let ticking = false;

    function revealNavbar() {
      if (navbar.classList.contains('nav-hidden')) {
        navbar.classList.remove('nav-hidden');
      }
    }

    function hideNavbar() {
      const mobileDrawer = document.getElementById('nav-mobile-drawer');
      if (mobileDrawer && !mobileDrawer.classList.contains('hidden')) {
        return;
      }
      if (!navbar.classList.contains('nav-hidden')) {
        navbar.classList.add('nav-hidden');
      }
    }

    let accumulatedUp = 0;
    let accumulatedDown = 0;

    // A. Instant Wheel Reaction (triggers immediately on any upward wheel flick)
    window.addEventListener('wheel', (e) => {
      if (e.deltaY < 0) {
        // User spun wheel upward anywhere on page: immediately slide navbar down
        revealNavbar();
        accumulatedUp = 0;
      } else if (e.deltaY > 5 && (window.pageYOffset || document.documentElement.scrollTop || 0) > 70) {
        // User spun wheel downward past header area: slide navbar up out of view
        hideNavbar();
      }
    }, { passive: true });

    // B. Window Scroll Event (handles trackpads, momentum, keyboard, and scrollbars)
    function handleScroll() {
      const currentScrollY = Math.max(0, window.pageYOffset || document.documentElement.scrollTop || 0);

      // At or near top of page: always reveal
      if (currentScrollY <= 30) {
        revealNavbar();
        lastScrollY = currentScrollY;
        accumulatedUp = 0;
        accumulatedDown = 0;
        ticking = false;
        return;
      }

      const diff = currentScrollY - lastScrollY;

      // Scrolled UP anywhere on page: immediately glide navbar into view
      if (diff < -0.5) {
        revealNavbar();
        accumulatedUp = 0;
        accumulatedDown = 0;
      }
      // Scrolled DOWN: smoothly glide navbar up out of view
      else if (diff > 0) {
        accumulatedDown += diff;
        accumulatedUp = 0;
        if (accumulatedDown >= 10 && currentScrollY > 70) {
          hideNavbar();
        }
      }

      lastScrollY = currentScrollY;
      ticking = false;
    }

    window.addEventListener('scroll', () => {
      if (!ticking) {
        window.requestAnimationFrame(handleScroll);
        ticking = true;
      }
    }, { passive: true });

    // C. Mobile/Touch Swipe Support
    let touchStartY = 0;
    window.addEventListener('touchstart', (e) => {
      if (e.touches && e.touches[0]) {
        touchStartY = e.touches[0].clientY;
      }
    }, { passive: true });

    window.addEventListener('touchmove', (e) => {
      if (e.touches && e.touches[0]) {
        const currentTouchY = e.touches[0].clientY;
        const touchDiff = currentTouchY - touchStartY;
        // Swiping finger DOWN means page moves UP: reveal navbar!
        if (touchDiff > 12) {
          revealNavbar();
        } else if (touchDiff < -12 && (window.pageYOffset || document.documentElement.scrollTop || 0) > 70) {
          hideNavbar();
        }
        touchStartY = currentTouchY;
      }
    }, { passive: true });

    // D. Desktop hover intent: moving cursor near top edge reveals navbar
    window.addEventListener('mousemove', (e) => {
      if (e.clientY <= 30) {
        revealNavbar();
      }
    }, { passive: true });
  }

  // 4. Check initial URL hash on load and trigger dissolve transition
  function checkInitialHash() {
    if (window.location.hash) {
      const hashId = window.location.hash.substring(1);
      setTimeout(() => {
        dissolveScrollTo(hashId);
      }, 140);
    }
  }

  // 5. Attach Dissolve Event Listeners to all Navigation Links
  function setupDissolveLinks() {
    const selector = [
      '#navbar-links a',
      '.nav-item',
      '.mobile-nav-link',
      '#nav-mobile-drawer a',
      'header a',
      'a[data-dissolve]',
      'footer a[data-path]'
    ].join(', ');

    const navLinks = document.querySelectorAll(selector);

    navLinks.forEach((link) => {
      if (link.dataset.dissolveBound) return;
      link.dataset.dissolveBound = 'true';

      link.addEventListener('click', (e) => {
        const href = link.getAttribute('href');
        if (!href) return;

        // Skip non-navigational links
        if (
          href.startsWith('mailto:') ||
          href.startsWith('tel:') ||
          href.startsWith('javascript:') ||
          link.getAttribute('target') === '_blank' ||
          (href.startsWith('http') && !href.includes(window.location.host))
        ) {
          return;
        }

        // Close mobile drawer if open
        const mobileDrawer = document.getElementById('nav-mobile-drawer');
        const mobileIcon = document.getElementById('nav-mobile-icon');
        if (mobileDrawer && !mobileDrawer.classList.contains('hidden')) {
          mobileDrawer.classList.add('hidden');
          if (mobileIcon) mobileIcon.textContent = 'menu';
        }

        // Ensure navbar is immediately revealed on link click
        const navbar = getNavbar();
        if (navbar) {
          navbar.classList.remove('nav-hidden');
        }

        // Scenario A: Anchor link on current page (#sectionId)
        if (href.startsWith('#')) {
          e.preventDefault();
          const targetId = href.substring(1);
          dissolveScrollTo(targetId);
          if (history.pushState) {
            history.pushState(null, null, href);
          }
          return;
        }

        // Scenario B: Link pointing to same page with an anchor
        const currentPath = window.location.pathname.split('/').pop() || 'index.html';
        const [targetPage, targetHash] = href.split('#');

        const isSamePage =
          targetPage === currentPath ||
          (currentPath === '' && targetPage === 'index.html') ||
          (currentPath === 'index.html' && (targetPage === '' || targetPage === './' || targetPage === '/'));

        if (isSamePage && targetHash) {
          e.preventDefault();
          dissolveScrollTo(targetHash);
          if (history.pushState) {
            history.pushState(null, null, '#' + targetHash);
          }
          return;
        }

        // Scenario C: Navigating to another page -> Smooth Dissolve Out
        e.preventDefault();
        document.body.classList.add('page-dissolving-out');

        setTimeout(() => {
          window.location.href = href;
        }, 220);
      });
    });
  }

  // Initialize all features on DOM ready
  function init() {
    setupNavbarScrollBehavior();
    setupDissolveLinks();
    checkInitialHash();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Expose global helper
  window.dissolveScrollTo = dissolveScrollTo;
})();
