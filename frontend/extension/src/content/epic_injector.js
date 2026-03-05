// Sotto Cockpit — Epic DOM Injector (Content Script)
// Runs on Applied Epic (*.useappliedepic.com) pages.
// Extracts client data from the DOM and sends it to the service worker.
// Uses defensive selectors with fallbacks — Epic UI updates may break these.

(() => {
  // Track the last extracted client to avoid duplicate messages
  let lastClientName = null;

  // =========================================================
  // DOM Selectors
  // =========================================================

  // Each selector has a primary (data-attribute based) and secondary (class/tag based)
  // fallback. Document why each might break.

  const SELECTORS = {
    // Primary: data-field attribute set by Epic's React/Angular binding.
    // May break if: Epic renames the data attribute or switches frameworks.
    clientNamePrimary: '[data-field="ClientName"]',

    // Secondary: heading tag used for the client name display.
    // May break if: Epic changes the heading level or removes the class.
    clientNameSecondary: "h1.client-name",

    // Tertiary: fall back to any h1 inside the client detail pane.
    // May break if: page layout changes and a different h1 appears first.
    clientNameTertiary: ".client-detail h1, .client-header h1",

    // Primary: data-field attribute for phone number elements.
    // May break if: Epic renames the field identifier.
    phonePrimary: '[data-field="Phone"]',

    // Secondary: elements with phone-related classes.
    // May break if: Epic changes CSS class naming conventions.
    phoneSecondary: ".phone-number, .contact-phone",

    // Tertiary: links with tel: protocol (common for clickable phone numbers).
    // May break if: Epic stops using tel: links.
    phoneTertiary: 'a[href^="tel:"]',

    // Container where we inject the Sotto call button.
    // Primary: the actions toolbar in the client detail view.
    // May break if: Epic restructures the toolbar layout.
    actionBarPrimary: ".client-actions, .detail-toolbar",

    // Secondary: the first button group found in the client header area.
    // May break if: Epic changes the header structure.
    actionBarSecondary: ".client-header .btn-group, .detail-header .actions",
  };

  // =========================================================
  // Extraction
  // =========================================================

  function extractClientName() {
    const el =
      document.querySelector(SELECTORS.clientNamePrimary) ||
      document.querySelector(SELECTORS.clientNameSecondary) ||
      document.querySelector(SELECTORS.clientNameTertiary);
    return el ? el.textContent.trim() : null;
  }

  function extractPhoneNumbers() {
    const phones = new Set();

    // Try each selector tier
    for (const selector of [
      SELECTORS.phonePrimary,
      SELECTORS.phoneSecondary,
      SELECTORS.phoneTertiary,
    ]) {
      const elements = document.querySelectorAll(selector);
      for (const el of elements) {
        const raw = el.href
          ? el.href.replace("tel:", "")
          : el.textContent;
        const cleaned = raw.replace(/[^\d+]/g, "");
        if (cleaned.length >= 7) {
          phones.add(cleaned);
        }
      }
      if (phones.size > 0) break; // Use the first tier that yields results
    }

    return [...phones];
  }

  function extractAndSend() {
    const clientName = extractClientName();
    if (!clientName || clientName === lastClientName) return;

    lastClientName = clientName;
    const phoneNumbers = extractPhoneNumbers();

    chrome.runtime.sendMessage({
      type: "epic_client_data",
      data: {
        clientName,
        phoneNumbers,
        url: window.location.href,
        timestamp: Date.now(),
      },
    });
  }

  // =========================================================
  // Call button injection
  // =========================================================

  const BUTTON_ID = "sotto-call-btn";

  function injectCallButton() {
    // Don't inject twice
    if (document.getElementById(BUTTON_ID)) return;

    const actionBar =
      document.querySelector(SELECTORS.actionBarPrimary) ||
      document.querySelector(SELECTORS.actionBarSecondary);
    if (!actionBar) return;

    const btn = document.createElement("button");
    btn.id = BUTTON_ID;
    btn.textContent = "Sotto Call";
    btn.title = "Log call via Sotto";
    btn.style.cssText =
      "margin-left:8px;padding:6px 12px;background:#2563eb;color:#fff;" +
      "border:none;border-radius:4px;font-size:13px;cursor:pointer;";

    btn.addEventListener("click", () => {
      const clientName = extractClientName();
      const phoneNumbers = extractPhoneNumbers();
      chrome.runtime.sendMessage({
        type: "epic_call_initiate",
        data: { clientName, phoneNumbers },
      });
    });

    actionBar.appendChild(btn);
  }

  // =========================================================
  // DOM observation
  // =========================================================

  const observer = new MutationObserver((mutations) => {
    // Check if any mutation added nodes that might be client records
    let shouldCheck = false;
    for (const mutation of mutations) {
      if (mutation.addedNodes.length > 0) {
        shouldCheck = true;
        break;
      }
    }
    if (shouldCheck) {
      extractAndSend();
      injectCallButton();
    }
  });

  // Start observing once the DOM is ready
  observer.observe(document.body, {
    childList: true,
    subtree: true,
  });

  // Initial extraction in case the page is already loaded
  extractAndSend();
  injectCallButton();
})();
