document.getElementById("startBtn").addEventListener("click", () => {
  chrome.runtime.sendMessage({ action: "START" });
});

document.getElementById("stopBtn").addEventListener("click", () => {
  chrome.runtime.sendMessage({ action: "STOP" });
});

setInterval(() => {
  chrome.storage.local.get("statusText", (data) => {
    if (data.statusText) {
      document.getElementById("status").innerText = data.statusText;
    }
  });
}, 1000);