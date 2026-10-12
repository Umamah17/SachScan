# 🛡️ SachScan — AI Integrity & Media Verification Intelligence

> **Sovereign multimodal authenticity & AI integrity intelligence engine for verifying deepfakes, synthetic media, and digital claims in real-time.**

---

## 📌 Overview

**SachScan** is a web-based intelligence platform designed to detect deepfakes, verify digital authenticity, and analyze misleading synthetic media. Featuring a 240-frame scroll-driven visual experience, forensic analysis tools, and messaging integrations, SachScan empowers users and enterprises to verify truth in the age of generative AI.

---

## ✨ Features

- **🎞️ Interactive Scroll-Driven Visualizer**: High-performance canvas-based sequence rendering 240 forensic animation frames synchronized with user scroll.
- **🔬 Multimodal Forensic Intelligence**:
  - **Claim DNA & Provenance**: Trace origins, verify tampering history, and cross-reference authoritative sources.
  - **FFT Spectral Frequency Probe**: Detect synthetic high-frequency artifacts and generative model signatures.
  - **Forensic Neural Stack**: Real-time authenticity confidence scoring across audio, video, text, and imagery.
- **💬 Omnichannel Messaging Integrations**: Connect seamlessly with bots and verification workflows for WhatsApp, Telegram, Discord, Slack, Gmail, X (Twitter), Reddit, and Threads.
- **📱 Progressive Web App (PWA)**:
  - Installable on desktop and mobile devices.
  - Offline mode with custom fallback (`offline.html`) and service worker caching (`sw.js`).
  - Preloaded asset pipeline with responsive progress tracking.
- **🎨 Glassmorphic Interface**: Sleek, modern aesthetic with fluid page transitions, typography, and dark/light responsive layouts.

---

## 📂 Project Structure

```text
├── index.html            # Main landing page with 240-frame scroll animation & overview
├── technology.html       # Deep dive into forensic neural stacks and detection models
├── integrations.html     # Messaging integrations portal (WhatsApp, Discord, etc.)
├── help.html             # Interactive verification scanner and user assistance
├── login.html            # User authentication and enterprise portal
├── offline.html          # Fallback screen for offline PWA operation
├── style.css             # Core stylesheet and design tokens
├── transitions.js        # Smooth page transitions and state management
├── pwa.js                # Service worker registration & PWA installation handling
├── sw.js                 # PWA service worker caching logic
├── manifest.json         # Web App Manifest configuration
├── server.js             # Lightweight Node.js static file server
├── package.json          # Project metadata and start scripts
└── ezgif-1dab58a0391d528b-jpg/ # 240 high-resolution animation frames
```

---

## 🚀 Getting Started

### Prerequisites

Ensure you have [Node.js](https://nodejs.org/) (v16 or higher) installed on your system.

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Bhavesh1424/SachScan.git
   cd SachScan
   ```

2. **Start the local server:**
   ```bash
   npm start
   ```
   *(or run directly via Node)*
   ```bash
   node server.js
   ```

3. **Open the application:**
   Navigate to [http://localhost:3000](http://localhost:3000) in your web browser.

---

## 🛠️ Tech Stack

- **Frontend**: HTML5, Vanilla JavaScript (ES6+), Tailwind CSS
- **Typography**: Geist, Inter, JetBrains Mono, Rajdhani
- **PWA**: Service Worker API, Cache Storage API, Web App Manifest
- **Backend / Serving**: Node.js HTTP Server (`server.js`)

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
