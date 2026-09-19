const DASHBOARD_ORIGIN = "https://joost.iveras.com";
const PENDING_KEY = "pendingLocalFindingCapture";
const MAX_AGE_MS = 10 * 60 * 1000;
const MAX_CAPTURE_DATA_URL_BYTES = 7 * 1024 * 1024;

function isDashboardCaseUrl(rawUrl, caseId) {
  try {
    const url = new URL(rawUrl);
    return url.origin === DASHBOARD_ORIGIN && url.pathname.startsWith(
      `/cms/workflow/case/${encodeURIComponent(caseId)}`,
    );
  } catch (_) {
    return false;
  }
}

function isCaptureableWebUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch (_) {
    return false;
  }
}

async function setBadge(text) {
  await chrome.action.setBadgeText({text});
  if (text) await chrome.action.setBadgeBackgroundColor({color: "#2563eb"});
}

async function getPending() {
  const stored = await chrome.storage.session.get(PENDING_KEY);
  const pending = stored[PENDING_KEY] || null;
  if (pending && Date.now() - pending.createdAt > MAX_AGE_MS) {
    await chrome.storage.session.remove(PENDING_KEY);
    await setBadge("");
    return null;
  }
  return pending;
}

async function clearPending() {
  await chrome.storage.session.remove(PENDING_KEY);
  await setBadge("");
}

async function armCapture(message) {
  const pending = {
    caseId: message.caseId,
    findingId: message.findingId,
    createdAt: Date.now(),
    state: "armed",
  };
  await chrome.storage.session.set({[PENDING_KEY]: pending});
  await setBadge("1");
  return {ok: true};
}

async function captureActiveTab(tab) {
  const pending = await getPending();
  if (!pending) return {ok: false, error: "No finding is selected"};
  if (!tab?.id || !tab.windowId || !tab.url) {
    return {ok: false, error: "No active tab is available"};
  }
  if (tab.url.startsWith("chrome:") || tab.url.startsWith("brave:")) {
    return {ok: false, error: "This browser page cannot be captured"};
  }
  if (isDashboardCaseUrl(tab.url, pending.caseId)) {
    return {ok: false, error: "Open the source website tab before capturing"};
  }
  if (!isCaptureableWebUrl(tab.url)) {
    return {ok: false, error: "Only normal website tabs can be captured"};
  }
  try {
    const imageDataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
      format: "jpeg",
      quality: 90,
    });
    if (imageDataUrl.length > MAX_CAPTURE_DATA_URL_BYTES) {
      return {ok: false, error: "The screenshot is too large to retain safely"};
    }
    await chrome.storage.session.set({
      [PENDING_KEY]: {
        ...pending,
        state: "ready",
        sourceUrl: tab.url,
        imageDataUrl,
        createdAt: Date.now(),
      },
    });
    await setBadge("U");
    return {ok: true};
  } catch (_) {
    return {ok: false, error: "The visible tab could not be captured"};
  }
}

async function uploadViaDashboard(tabId, pending) {
  const results = await chrome.scripting.executeScript({
    target: {tabId},
    world: "MAIN",
    args: [pending],
    func: async (capture) => {
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
      if (!csrf) return {ok: false, error: "Dashboard session is unavailable"};
      const blob = await fetch(capture.imageDataUrl).then((response) => response.blob());
      const form = new FormData();
      form.append("file", new File([blob], "local-browser-capture.jpg", {type: "image/jpeg"}));
      form.append("source_url", capture.sourceUrl);
      form.append("notes", "Captured from the investigator's local browser");
      form.append("csrf_token", csrf);
      const response = await fetch(
        `/cms/workflow/api/case/${encodeURIComponent(capture.caseId)}/findings/${encodeURIComponent(capture.findingId)}/screenshots`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: {"X-CSRFToken": csrf, "Accept": "application/json"},
          body: form,
        },
      );
      const body = await response.json().catch(() => ({}));
      return response.ok && body.ok
        ? {ok: true}
        : {ok: false, error: body.error || "The dashboard rejected the screenshot"};
    },
  });
  return results[0]?.result || {ok: false, error: "The dashboard page was unavailable"};
}

async function focusDashboardForUpload() {
  const pending = await getPending();
  if (!pending || pending.state !== "ready") {
    return {ok: false, error: "No completed local capture is available"};
  }
  const tabs = await chrome.tabs.query({
    url: `${DASHBOARD_ORIGIN}/cms/workflow/case/${pending.caseId}*`,
  });
  const dashboardTab = tabs.find((tab) => isDashboardCaseUrl(tab.url, pending.caseId));
  if (!dashboardTab?.id || !dashboardTab.windowId) {
    return {ok: false, error: "Return to the selected case in OSINT Dashboard"};
  }
  await chrome.windows.update(dashboardTab.windowId, {focused: true});
  await chrome.tabs.update(dashboardTab.id, {active: true});
  try {
    await chrome.tabs.sendMessage(dashboardTab.id, {type: "SHOW_PENDING_UPLOAD"});
  } catch (_) {
    // The content script will display the banner on the next completed page load.
  }
  return {ok: true};
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (message?.type === "ARM_CAPTURE") return armCapture(message);
    if (message?.type === "GET_PENDING") return {pending: await getPending()};
    if (message?.type === "CAPTURE_ACTIVE_TAB") {
      const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
      return captureActiveTab(tab);
    }
    if (message?.type === "FOCUS_DASHBOARD_UPLOAD") {
      return focusDashboardForUpload();
    }
    if (message?.type === "CANCEL_PENDING") {
      await clearPending();
      return {ok: true};
    }
    if (message?.type === "UPLOAD_PENDING") {
      const pending = await getPending();
      if (!pending || pending.state !== "ready") {
        return {ok: false, error: "No completed local capture is available"};
      }
      if (!sender.tab || !isDashboardCaseUrl(sender.tab.url, pending.caseId)) {
        return {ok: false, error: "Return to the selected case before uploading"};
      }
      const result = await uploadViaDashboard(sender.tab.id, pending);
      if (result.ok) await clearPending();
      return result;
    }
    return {ok: false, error: "Unsupported request"};
  })().then(sendResponse, () => sendResponse({ok: false, error: "Local capture failed"}));
  return true;
});
