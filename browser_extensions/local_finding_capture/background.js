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
  try {
    const result = await chrome.tabs.sendMessage(tabId, {
      type: "REQUEST_DASHBOARD_UPLOAD",
      pending,
    });
    return result && typeof result.ok === "boolean"
      ? result
      : {ok: false, error: "The dashboard upload bridge is unavailable"};
  } catch (_) {
    return {ok: false, error: "The dashboard page is unavailable — reload it and try again"};
  }
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
