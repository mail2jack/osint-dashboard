# OSINT Dashboard Local Capture extension

This extension captures the **visible active tab** in Brave, Chrome or Edge and
adds it to a selected finding in OSINT Dashboard. It does not read, export or
store website cookies, passwords or login sessions.

## Install (developer mode)

1. Open `brave://extensions` (or `chrome://extensions`).
2. Turn on **Developer mode**.
3. Choose **Load unpacked** and select this directory.
4. Pin **OSINT Dashboard Local Capture** in the browser toolbar.

## Use

1. In OSINT Dashboard, select **Capture from browser** on a finding.
2. Open the already authenticated source page in a separate tab.
3. Click the pinned extension icon. It captures only the currently visible tab.
4. In the extension select **Go to upload**. It returns you to the selected case.
5. Explicitly select **Upload screenshot** in the dashboard prompt.

The capture stays only in the extension's session memory until uploaded,
discarded, or the browser is restarted. The dashboard receives the JPEG, source
URL, a local-browser evidence note and the normal audit entry.

## Deliberate limits

- No automatic login, cookies, passwords or 2FA handling.
- No background capture and no full-page scrolling capture.
- The visible tab is stored as a bounded JPEG in browser-session memory only.
- The extension only communicates with `https://joost.iveras.com`.
