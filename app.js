/**
 * SENTINEL-01 // PURE SCROLL ANIMATION ENGINE
 * Seamless Canvas Frame Scrubbing with Smooth Lerp Physics.
 * Clean, unobstructed visual experience matching reference design.
 */

(function () {
  'use strict';

  // --- CONFIGURATION ---
  const TOTAL_FRAMES = 240;
  const FRAME_DIR = './ezgif-1dab58a0391d528b-jpg';
  const BG_COLOR = '#eaeaea'; // Matches frame edge RGB(234, 234, 234)

  const getFramePath = (index) => {
    const padded = String(index).padStart(3, '0');
    return `${FRAME_DIR}/ezgif-frame-${padded}.jpg`;
  };

  // --- DOM ELEMENTS ---
  const canvas = document.getElementById('animation-canvas');
  const ctx = canvas.getContext('2d', { alpha: false });
  const animContainer = document.getElementById('animation-container');
  const loaderOverlay = document.getElementById('loader-overlay');
  const loaderBar = document.getElementById('loader-bar');
  const loaderPercentage = document.getElementById('loader-percentage');
  const loaderCount = document.getElementById('loader-count');



  // --- STATE ---
  const images = new Array(TOTAL_FRAMES + 1);
  let loadedFrames = 0;
  let targetProgress = 0;
  let currentProgress = 0;
  let lastRenderedFrame = -1;

  // --- CANVAS RESIZING (HIGH DPI / RETINA) ---
  function resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const width = rect.width || window.innerWidth;
    const height = rect.height || window.innerHeight;

    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);

    // Force redraw
    lastRenderedFrame = -1;
    renderFrame(getCurrentFrameIndex());
  }

  window.addEventListener('resize', resizeCanvas);

  // --- PRELOAD IMAGES ---
  function preloadImages() {
    for (let i = 1; i <= TOTAL_FRAMES; i++) {
      const img = new Image();
      img.src = getFramePath(i);

      img.onload = () => {
        images[i] = img;
        loadedFrames++;

        const percent = Math.floor((loadedFrames / TOTAL_FRAMES) * 100);
        if (loaderBar) loaderBar.style.width = `${percent}%`;
        if (loaderPercentage) loaderPercentage.textContent = `${percent}%`;
        if (loaderCount) loaderCount.textContent = `${loadedFrames} / ${TOTAL_FRAMES}`;

        // Render first frame immediately
        if (i === 1) {
          renderFrame(1);
        }

        if (loadedFrames === TOTAL_FRAMES) {
          onPreloadComplete();
        }
      };

      img.onerror = () => {
        loadedFrames++;
        if (loadedFrames === TOTAL_FRAMES) {
          onPreloadComplete();
        }
      };
    }
  }

  function onPreloadComplete() {
    setTimeout(() => {
      if (loaderOverlay) loaderOverlay.classList.add('hidden');
      resizeCanvas();
    }, 300);
  }

  // --- FRAME CALCULATION ---
  function getCurrentFrameIndex() {
    const clampedProgress = Math.min(1, Math.max(0, currentProgress));
    return Math.min(
      TOTAL_FRAMES,
      Math.max(1, Math.round(1 + clampedProgress * (TOTAL_FRAMES - 1)))
    );
  }

  // --- DRAW FRAME ON CANVAS ---
  function renderFrame(frameIndex) {
    const img = images[frameIndex];
    if (!img || !img.complete || img.naturalWidth === 0) return;

    const cw = canvas.width;
    const ch = canvas.height;
    const iw = img.naturalWidth;
    const ih = img.naturalHeight;

    const imgRatio = iw / ih;
    const canvasRatio = cw / ch;

    let drawW, drawH, drawX, drawY;

    // Fill background with exact matching gray so entire frame blends seamlessly
    ctx.fillStyle = BG_COLOR;
    ctx.fillRect(0, 0, cw, ch);

    // COVER mode: fills 100% of the screen with ZERO gaps on left, right, top, or bottom
    if (canvasRatio > imgRatio) {
      drawW = cw;
      drawH = cw / imgRatio;
      drawX = 0;
      drawY = (ch - drawH) / 2;
    } else {
      drawH = ch;
      drawW = ch * imgRatio;
      drawX = (cw - drawW) / 2;
      drawY = 0;
    }

    ctx.drawImage(img, drawX, drawY, drawW, drawH);
    lastRenderedFrame = frameIndex;
  }

  // --- SMOOTH LERP ANIMATION LOOP ---
  function tick() {
    // Smooth lerp physics for fluid frame transition
    const lerpFactor = 0.12;
    const diff = targetProgress - currentProgress;
    if (Math.abs(diff) > 0.0001) {
      currentProgress += diff * lerpFactor;
    } else {
      currentProgress = targetProgress;
    }

    const currentFrame = getCurrentFrameIndex();
    if (currentFrame !== lastRenderedFrame) {
      renderFrame(currentFrame);
    }

    requestAnimationFrame(tick);
  }

  // --- SCROLL EVENT LISTENER ---
  function onScroll() {
    if (!animContainer) return;
    const containerTop = animContainer.offsetTop;
    const scrollableHeight = animContainer.scrollHeight - window.innerHeight;
    const scrollY = window.scrollY;

    if (scrollY < containerTop) {
      targetProgress = 0;
    } else if (scrollY > containerTop + scrollableHeight) {
      targetProgress = 1;
    } else {
      targetProgress = (scrollY - containerTop) / scrollableHeight;
    }
  }

  window.addEventListener('scroll', onScroll, { passive: true });



  // --- 3D TILT MOUSE TRACKER (Adapted from CARD EFFECT.txt) ---
  function init3DTiltCards() {
    const cards = document.querySelectorAll('.card-effect-3d, .card-capability-3d, .card-integration-3d, .app-card');
    if (!cards.length) return;

    // Only apply on fine-pointer devices (desktop mice/trackpads)
    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;

    cards.forEach(card => {
      let rafId = null;

      card.addEventListener('mousemove', (e) => {
        const rect = card.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = (e.clientY - rect.top) / rect.height;

        // Realistic perspective tilt corresponding to mouse position
        const tiltX = (0.5 - y) * 14;
        const tiltY = (x - 0.5) * 14;

        if (rafId) cancelAnimationFrame(rafId);
        rafId = requestAnimationFrame(() => {
          card.style.transform = `perspective(800px) rotateX(${tiltX.toFixed(2)}deg) rotateY(${tiltY.toFixed(2)}deg) scale3d(1.02, 1.02, 1.02)`;
          card.style.transition = 'transform 75ms ease-out, filter 0.2s ease, box-shadow 0.2s ease';
        });
      });

      card.addEventListener('mouseleave', () => {
        if (rafId) cancelAnimationFrame(rafId);
        card.style.transform = 'perspective(800px) rotateX(0deg) rotateY(0deg) scale3d(1, 1, 1)';
        card.style.transition = 'transform 0.45s cubic-bezier(0.16, 1, 0.3, 1), filter 0.2s ease, box-shadow 0.2s ease';
      });
    });
  }

  // --- INITIALIZATION ---
  resizeCanvas();
  preloadImages();
  requestAnimationFrame(tick);
  init3DTiltCards();

})();
