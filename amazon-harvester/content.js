(() => {
  const results = [];
  const cards = document.querySelectorAll('div[data-component-type="s-search-result"][data-asin]');

  for (const card of cards) {
    if (results.length >= 4) break;

    const asin = card.getAttribute("data-asin")?.trim();
    if (!asin || asin.length !== 10) continue;

    // Lewati iklan Sponsored
    const sponsored = card.querySelector('.puis-sponsored-label-text');
    if (sponsored) continue;

    const titleEl = card.querySelector("h2 span") || card.querySelector("h2 a span");
    const title = titleEl ? titleEl.innerText.trim() : "";
    if (!title) continue;

    // Ekstrak Rating (misal: "4.7 out of 5 stars" -> 4.7)
    const ratingEl = card.querySelector('span[aria-label*="out of 5 stars"]');
    let rating = null;
    if (ratingEl) {
      const m = ratingEl.getAttribute("aria-label").match(/([\d.]+)\s+out of 5/);
      if (m) rating = parseFloat(m[1]);
    }

    // Ekstrak Jumlah Review (misal: "1,234" -> 1234)
    const reviewEl = card.querySelector('span[aria-label*="ratings"], a[href*="#customerReviews"] span.s-underline-text');
    let review_count = 0;
    if (reviewEl) {
      const rawNum = (reviewEl.innerText || "").replace(/[^0-9]/g, "");
      if (rawNum) review_count = parseInt(rawNum, 10);
    }

    // Deteksi kasar OEM (akan divalidasi ulang oleh AI di Tahap 3)
    const lowerTitle = title.toLowerCase();
    const is_oem = lowerTitle.includes("genuine") || lowerTitle.includes("official") || lowerTitle.includes("oem");

    results.push({ asin, title, rating, review_count, is_oem });
  }

  return results;
})();