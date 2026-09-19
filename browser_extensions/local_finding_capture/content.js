function send(message) {
  return chrome.runtime.sendMessage(message);
}

function caseMatches(pending) {
  return location.pathname.startsWith(`/cms/workflow/case/${encodeURIComponent(pending.caseId)}`);
}

function showPendingUpload(pending) {
  if (!pending || pending.state !== "ready" || !caseMatches(pending)) return;
  if (document.getElementById("osint-local-capture-banner")) return;
  const banner = document.createElement("div");
  banner.id = "osint-local-capture-banner";
  banner.setAttribute("role", "status");
  banner.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;max-width:360px;padding:14px;border:1px solid #2563eb;border-radius:8px;background:#fff;color:#111827;box-shadow:0 8px 24px rgba(0,0,0,.2);font:14px system-ui";
  const text = document.createElement("p");
  text.textContent = "A local browser screenshot is ready. Upload it to this finding?";
  text.style.margin = "0 0 10px";
  const upload = document.createElement("button");
  upload.type = "button";
  upload.textContent = "Upload screenshot";
  upload.style.cssText = "margin-right:8px;padding:6px 10px;background:#2563eb;color:#fff;border:0;border-radius:4px;cursor:pointer";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = "Discard";
  cancel.style.cssText = "padding:6px 10px;background:#fff;color:#374151;border:1px solid #9ca3af;border-radius:4px;cursor:pointer";
  upload.addEventListener("click", async () => {
    upload.disabled = true;
    const result = await send({type: "UPLOAD_PENDING"});
    if (result.ok) location.reload();
    else {
      upload.disabled = false;
      text.textContent = result.error || "The screenshot could not be uploaded.";
    }
  });
  cancel.addEventListener("click", async () => {
    await send({type: "CANCEL_PENDING"});
    banner.remove();
  });
  banner.append(text, upload, cancel);
  document.body.append(banner);
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-browser-capture]");
  if (!button) return;
  const caseId = button.dataset.caseId;
  const findingId = button.dataset.findingId;
  if (!caseId || !findingId) return;
  const result = await send({type: "ARM_CAPTURE", caseId, findingId});
  if (result.ok) {
    button.textContent = "🧩 Finding selected — open the source tab and click the extension";
    button.disabled = true;
  }
});

function refreshPendingUpload() {
  send({type: "GET_PENDING"}).then(({pending}) => showPendingUpload(pending));
}

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "SHOW_PENDING_UPLOAD") refreshPendingUpload();
});

window.addEventListener("pageshow", refreshPendingUpload);
refreshPendingUpload();
