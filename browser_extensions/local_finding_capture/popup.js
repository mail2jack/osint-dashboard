const status = document.getElementById("status");
const capture = document.getElementById("capture");
const upload = document.getElementById("upload");
const discard = document.getElementById("discard");

async function refresh() {
  const {pending} = await chrome.runtime.sendMessage({type: "GET_PENDING"});
  capture.hidden = true;
  upload.hidden = true;
  discard.hidden = true;
  if (!pending) {
    status.textContent = "First select ‘Capture from browser’ on a finding in OSINT Dashboard.";
    return;
  }
  if (pending.state === "armed") {
    status.textContent = "Open the source page, then capture its currently visible tab.";
    capture.hidden = false;
    discard.hidden = false;
    return;
  }
  status.textContent = "Screenshot ready. Return to the selected case in OSINT Dashboard to upload or discard it.";
  upload.hidden = false;
  discard.hidden = false;
}

capture.addEventListener("click", async () => {
  capture.disabled = true;
  const result = await chrome.runtime.sendMessage({type: "CAPTURE_ACTIVE_TAB"});
  if (result.ok) return refresh();
  status.textContent = result.error || "The visible tab could not be captured.";
  capture.disabled = false;
});

upload.addEventListener("click", async () => {
  upload.disabled = true;
  const result = await chrome.runtime.sendMessage({type: "FOCUS_DASHBOARD_UPLOAD"});
  if (result.ok) return window.close();
  status.textContent = result.error || "The selected dashboard case could not be opened.";
  upload.disabled = false;
});

discard.addEventListener("click", async () => {
  await chrome.runtime.sendMessage({type: "CANCEL_PENDING"});
  await refresh();
});

refresh();
