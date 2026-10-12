const fs = require('fs');

const codeHtml = fs.readFileSync('code.html', 'utf8');

// 1. Extract head content from code.html
const headStart = codeHtml.indexOf('<head>') + '<head>'.length;
const headEnd = codeHtml.indexOf('</head>');
const headContent = codeHtml.substring(headStart, headEnd);

// 2. Extract sections from <!-- INTERACTIVE LIVE DEMO SCANNER SECTION -->
const marker = '<!-- INTERACTIVE LIVE DEMO SCANNER SECTION -->';
const markerPos = codeHtml.indexOf(marker);
if (markerPos === -1) {
  console.error('Marker not found!');
  process.exit(1);
}

// Extract up to </body>
const bodyEndPos = codeHtml.lastIndexOf('</body>');
const bodySection = codeHtml.substring(markerPos, bodyEndPos);

// 3. Construct unified index.html
const unifiedHtml = `<!DOCTYPE html>
<html lang="en">
<head>
${headContent}
  <link rel="stylesheet" href="style.css"/>
</head>
<body class="bg-background font-body-md text-on-surface antialiased selection:bg-secondary-fixed selection:text-on-secondary-fixed">

  <!-- Preloader Screen -->
  <div id="loader-overlay" class="loader-overlay">
    <div class="loader-content">
      <div class="loader-brand">SACHSCAN<span>//01</span></div>
      <div class="loader-status">Preloading High-Resolution Forensic Frames...</div>
      <div class="loader-bar-wrap">
        <div id="loader-bar" class="loader-bar-fill"></div>
      </div>
      <div class="loader-info">
        <span id="loader-percentage">0%</span>
        <span id="loader-count">0 / 240</span>
      </div>
    </div>
  </div>

  <!-- Sticky Frame Animation Stage (Full Bleed Hero) -->
  <section class="animation-container" id="animation-container">
    <div class="sticky-viewport" id="sticky-viewport">
      <canvas id="animation-canvas"></canvas>
    </div>
  </section>

  <!-- Content from code.html below scroll animations (WITHOUT navbar and hero section) -->
  <main class="w-full bg-background min-h-screen">
    <div class="flex flex-col w-full">
${bodySection}

  <!-- Scroll Animation Engine -->
  <script src="app.js"></script>
</body>
</html>
`;

fs.writeFileSync('index.html', unifiedHtml, 'utf8');
console.log('Successfully generated index.html! File size:', unifiedHtml.length, 'bytes');
