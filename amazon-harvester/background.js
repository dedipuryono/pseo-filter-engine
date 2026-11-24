const BRIDGE_URL = "http://localhost:8787";
let isRunning = false;
let workerTabId = null;
let targetWindowId = null;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomDelay(minSec, maxSec) {
  return Math.floor((Math.random() * (maxSec - minSec) + minSec) * 1000);
}

// Reset workerTabId jika tab ditutup manual oleh pengguna
chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId === workerTabId) {
    workerTabId = null;
  }
});

async function waitForTabComplete(tabId, timeoutMs = 25000) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }, timeoutMs);

    const listener = (updatedTabId, info) => {
      if (updatedTabId === tabId && info.status === "complete") {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function closeWorkerTab() {
  if (workerTabId !== null) {
    try {
      await chrome.tabs.remove(workerTabId);
    } catch (_) {
      // Abaikan jika tab sudah tertutup
    }
    workerTabId = null;
  }
}

async function runHarvestLoop() {
  while (isRunning) {
    try {
      const res = await fetch(`${BRIDGE_URL}/next`);
      const data = await res.json();

      if (!isRunning) break;

      if (data.done) {
        isRunning = false;
        await closeWorkerTab();
        chrome.storage.local.set({ statusText: "SELESAI! Semua model telah di-scrape." });
        break;
      }

      const { device, remaining } = data;
      chrome.storage.local.set({
        statusText: `Status: ON (${remaining} tersisa) -> ${device.brand} ${device.model_name}`
      });

      const searchUrl = `https://www.amazon.com/s?k=${encodeURIComponent(device.query)}`;

      if (workerTabId === null) {
        const createOpts = { url: searchUrl, active: false };
        if (targetWindowId !== null) {
          createOpts.windowId = targetWindowId;
        }
        const tab = await chrome.tabs.create(createOpts);
        workerTabId = tab.id;
      } else {
        await chrome.tabs.update(workerTabId, { url: searchUrl });
      }

      await waitForTabComplete(workerTabId);
      if (!isRunning) break;

      await sleep(2500); // Tunggu DOM Amazon stabil
      if (!isRunning) break;

      const execResults = await chrome.scripting.executeScript({
        target: { tabId: workerTabId },
        files: ["content.js"]
      });

      const products = execResults?.[0]?.result || [];

      await fetch(`${BRIDGE_URL}/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          device_id: device.id,
          products
        })
      });

      // Jeda acak 5-9 detik (Anti-Captcha)
      await sleep(randomDelay(5, 9));
    } catch (err) {
      if (!isRunning) break;
      chrome.storage.local.set({ statusText: `Error: ${err.message}. Mencoba lagi...` });
      await sleep(5000);
    }
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "START" && !isRunning) {
    isRunning = true;
    chrome.windows.getLastFocused({ populate: false }, (win) => {
      targetWindowId = win ? win.id : null;
      chrome.storage.local.set({ statusText: "Status: ON - Memulai panen..." });
      runHarvestLoop();
    });
    sendResponse({ running: true });
  } else if (msg.action === "STOP") {
    isRunning = false;
    closeWorkerTab();
    chrome.storage.local.set({ statusText: "Status: OFF (Aman untuk scraper lain)" });
    sendResponse({ running: false });
  }
  return true;
});