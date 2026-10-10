/* Quote messages in the customer/staff case chat without inline JavaScript. */
(function () {
  function quoteMessage(button) {
    const box = document.getElementById("message");
    if (!box) return;
    const message = button.getAttribute("data-message") || "";
    const author = button.getAttribute("data-author") || "Iveras";
    const time = button.getAttribute("data-time") || "";
    const quote = message.split("\n").map((line) => `> ${line}`).join("\n");
    const prefix = `> ${author} (${time}):\n${quote}\n\n`;
    box.value = box.value.trim() ? `${box.value.trim()}\n\n${prefix}` : prefix;
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-quote-message]");
    if (button) quoteMessage(button);
  });
})();
