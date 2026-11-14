/* Bandeh shop: browser storage for two things ONLY.
 *
 * 1. Cart draft ("bandeh-cart-draft"): what the visitor tapped
 *    "add to cart" on. A DRAFT, not the cart. The server owns the
 *    real cart (cart topic) and checkout re-checks everything there.
 *    The draft only remembers which items and how many, so the cart
 *    page can pre-fill. It stores NO prices: prices live on the server.
 * 2. Theme choice ("bandeh-theme"): "dark" (default) or "light" (or "warm").
 *    Dark has a warm near-black background; light has a warm off-white background.
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
    // item: {id, name, qty?}. Tapping again grows the qty.
    var draft = getCartDraft();
    var found = null;
    for (var i = 0; i < draft.length; i++) {
      if (draft[i].id === item.id) {
        found = draft[i];
        break;
      }
    }
    var addQty = typeof item.qty === "number" && item.qty > 0 ? item.qty : 1;
    if (found) {
      found.qty += addQty;
    } else {
      draft.push({ id: item.id, name: item.name, qty: addQty });
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

  function setCartDraftQty(id, qty) {
    var newQty = parseInt(qty, 10);
    if (isNaN(newQty) || newQty <= 0) {
      return removeFromCartDraft(id);
    }
    var draft = getCartDraft();
    for (var i = 0; i < draft.length; i++) {
      if (draft[i].id === id) {
        draft[i].qty = newQty;
        break;
      }
    }
    saveCartDraft(draft);
    updateCartBadge();
    return draft;
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
    try {
      var stored = window.localStorage.getItem(THEME_KEY);
      if (stored === "light") return "light";
      if (stored === "warm") return "warm";
      return "dark";
    } catch (e) {
      return "dark";
    }
  }

  function applyTheme(name) {
    var isLight = name === "light";
    document.documentElement.setAttribute("data-theme", name);
    if (isLight) {
      document.documentElement.classList.remove("dark");
    } else {
      document.documentElement.classList.add("dark");
    }
    var button = document.getElementById("theme-toggle");
    if (button) {
      button.textContent = isLight ? "تیره" : "روشن";
      button.setAttribute(
        "aria-label",
        isLight ? "تغییر به حالت تیره" : "تغییر به حالت روشن"
      );
    }
  }

  function setTheme(name) {
    var next = name === "light" ? "light" : "dark";
    safeSet(THEME_KEY, next);
    applyTheme(next);
    return next;
  }

  function toggleTheme() {
    return setTheme(getTheme() === "light" ? "dark" : "light");
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

    // Quantity stepper wiring
    var stepperMinus = document.getElementById("stepper-minus");
    var stepperPlus = document.getElementById("stepper-plus");
    var stepperInput = document.getElementById("product-quantity");
    if (stepperInput && stepperMinus && stepperPlus) {
      var getMax = function () {
        return parseInt(stepperInput.getAttribute("max") || "99", 10);
      };
      stepperMinus.addEventListener("click", function () {
        var current = parseInt(stepperInput.value, 10) || 1;
        if (current > 1) {
          stepperInput.value = String(current - 1);
        }
      });
      stepperPlus.addEventListener("click", function () {
        var current = parseInt(stepperInput.value, 10) || 1;
        if (current < getMax()) {
          stepperInput.value = String(current + 1);
        }
      });
      stepperInput.addEventListener("change", function () {
        var val = parseInt(stepperInput.value, 10) || 1;
        var maxVal = getMax();
        if (val < 1) val = 1;
        if (val > maxVal) val = maxVal;
        stepperInput.value = String(val);
      });
    }

    // Add to cart buttons
    var buttons = document.querySelectorAll("[data-add-to-cart]");
    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        var qtyInput = document.getElementById("product-quantity");
        var qty = qtyInput ? parseInt(qtyInput.value, 10) || 1 : 1;
        addToCartDraft({
          id: button.getAttribute("data-product-id"),
          name: button.getAttribute("data-product-name"),
          qty: qty,
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

  // Exposed for the cart page and tests.
  window.BandehStore = {
    getCartDraft: getCartDraft,
    addToCartDraft: addToCartDraft,
    removeFromCartDraft: removeFromCartDraft,
    setCartDraftQty: setCartDraftQty,
    clearCartDraft: clearCartDraft,
    cartDraftCount: cartDraftCount,
    updateCartBadge: updateCartBadge,
    getTheme: getTheme,
    setTheme: setTheme,
    toggleTheme: toggleTheme,
  };
})();
