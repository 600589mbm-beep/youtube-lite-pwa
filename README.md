# YouTube Lite PWA

A clean iPhone Home Screen web app / launcher for YouTube.

This project does **not** bypass YouTube ads, proxy YouTube, strip ads, download videos, or modify YouTube scripts. It is only a simple static launcher that opens YouTube mobile links and can be hosted on GitHub Pages.

## Files

- `index.html` — main app page
- `manifest.webmanifest` — PWA manifest (name, icon, theme color)
- `sw.js` — basic service worker for local app shell caching
- `icon.svg` — app icon (red rounded square + white play triangle)

## GitHub Pages Setup

1. Create a new GitHub repository.
   Example name: `youtube-lite-pwa`

2. Upload these files to the root of the repository:
   - `index.html`
   - `manifest.webmanifest`
   - `sw.js`
   - `icon.svg`
   - `README.md`

3. Go to the repository on GitHub.

4. Click **Settings**.

5. In the left sidebar, click **Pages**.

6. Under **Build and deployment**:
   - Source: `Deploy from a branch`
   - Branch: `main` / `/ (root)`

7. Click **Save**.

8. GitHub will give you a public link like:
   `https://YOUR-GITHUB-USERNAME.github.io/youtube-lite-pwa/`

## Add to iPhone Home Screen

1. Open Safari on your iPhone.

2. Go to your GitHub Pages link:
   `https://YOUR-GITHUB-USERNAME.github.io/youtube-lite-pwa/`

3. Tap the **Share** button.

4. Tap **Add to Home Screen**.

5. Turn on **Open as Web App**.

6. Tap **Add**.

7. The YouTube Lite icon now sits on your iPhone Home Screen.

## Important

This launcher does not block YouTube ads by itself. For an official ad-free YouTube experience, use **YouTube Premium** or **Premium Lite** where available.
