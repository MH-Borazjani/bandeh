/* Bandeh shop: browser storage for two things ONLY.
 *
 * 1. Cart draft ("bandeh-cart-draft"): what the visitor tapped
 *    "add to cart" on. A DRAFT, not the cart. The server owns the
 *    real cart (cart topic) and checkout re-checks everything there.
 *    The draft only remembers which items and how many, so the cart
 *    page can pre-fill. It stores NO prices: prices live on the server.
 * 2. Theme choice ("bandeh-theme"): "dark" or "warm". Both are dark
 *    (white hurts eyes); warm is just a warmer dark.
 *
 * Nothing else goes in browser storage. Login state and anything the
 * server must trust live on the server. The allowlist below enforces
 * that: safeSet refuses any key not on the list, so even future code
 * cannot stash something else here by accident.
 */
(function () {
  "use strict";

  var ALLOWED_KEYS = ["bandeh-cart-draft", "bandeh-theme"];
  var CART_KEY = "bandeh-cart-draft";
  var THEME_KEY = "bandeh-theme";

  function safeSet(key, value) {
    if (ALLOWED_KEYS.indexOf(key) === -1) {
      throw new Error("refused to store key: " + key);
    }
    window.localStorage.setItem(key, value);
  }

  /* --- Cart draft ---------------------------------------------------- */

  function getCartDraft() {
    try {
      var raw = window.localStorage.getItem(CART_KEY);
      if (!raw) return [];
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      return [];
    }
  }

  function saveCartDraft(draft) {
    safeSet(CART_KEY, JSON.stringify(draft));
  }

  function addToCartDraft(item) {
    // item: {id, name}. Tapping again grows the qty.
    var draft = getCartDraft();
    var found = null;
    for (var i = 0; i < draft.length; i++) {
      if (draft[i].id === item.id) {
        found = draft[i];
        break;
      }
    }
    if (found) {
      found.qty += 1;
    } else {
      draft.push({ id: item.id, name: item.name, qty: 1 });
    }
    saveCartDraft(draft);
    updateCartBadge();
    return draft;
  }

  function removeFromCartDraft(id) {
    var draft = getCartDraft().filter(function (line) {
      return line.id !== id;
    });
    saveCartDraft(draft);
    updateCartBadge();
    return draft;
  }

  function clearCartDraft() {
    saveCartDraft([]);
    updateCartBadge();
  }

  function cartDraftCount() {
    var total = 0;
    getCartDraft().forEach(function (line) {
      total += line.qty || 0;
    });
    return total;
  }

  /* --- Theme ---------------------------------------------------------- */

  function getTheme() {
    var stored = window.localStorage.getItem(THEME_KEY);
    return stored === "warm" ? "warm" : "dark";
  }

  function applyTheme(name) {
    document.documentElement.setAttribute("data-theme", name);
    var button = document.getElementById("theme-toggle");
    if (button) {
      button.textContent = name === "warm" ? "تیره" : "گرم";
    }
  }

  function setTheme(name) {
    var next = name === "warm" ? "warm" : "dark";
    safeSet(THEME_KEY, next);
    applyTheme(next);
    return next;
  }

  function toggleTheme() {
    return setTheme(getTheme() === "warm" ? "dark" : "warm");
  }

  /* --- Page wiring ----------------------------------------------------- */

  function updateCartBadge() {
    var badge = document.getElementById("cart-count");
    if (!badge) return;
    var count = cartDraftCount();
    badge.textContent = count > 0 ? String(count) : "";
    badge.style.display = count > 0 ? "" : "none";
  }

  function wirePage() {
    applyTheme(getTheme());
    updateCartBadge();
    var toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        toggleTheme();
      });
    }
    var buttons = document.querySelectorAll("[data-add-to-cart]");
    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        addToCartDraft({
          id: button.getAttribute("data-product-id"),
          name: button.getAttribute("data-product-name"),
        });
        button.textContent = "✓ در سبد";
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wirePage);
  } else {
    wirePage();
  }

  // Exposed for the cart page (cart topic) and for tests.
  window.BandehStore = {
    getCartDraft: getCartDraft,
    addToCartDraft: addToCartDraft,
    removeFromCartDraft: removeFromCartDraft,
    clearCartDraft: clearCartDraft,
    cartDraftCount: cartDraftCount,
    getTheme: getTheme,
    setTheme: setTheme,
    toggleTheme: toggleTheme,
  };
})();
