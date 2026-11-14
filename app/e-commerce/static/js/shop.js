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

  function toPersianDigits(num) {
    var fa = ["۰", "۱", "۲", "۳", "۴", "۵", "۶", "۷", "۸", "۹"];
    return String(num).replace(/[0-9]/g, function (d) {
      return fa[parseInt(d, 10)];
    });
  }

  /* --- Page wiring ----------------------------------------------------- */

  function updateCartBadge() {
    var badge = document.getElementById("cart-count");
    if (!badge) return;
    var count = cartDraftCount();
    badge.textContent = count > 0 ? toPersianDigits(count) : "";
    badge.style.display = count > 0 ? "" : "none";
  }

  /* --- Quantity stepper ------------------------------------------- */
  /* On listing pages (landing, search, category) an add-to-cart button
     becomes a small stepper (- qty +) once its product is in the cart
     draft, so the visitor can choose how many and reduce it. At qty 0
     the stepper turns back into the original + button. The product page
     keeps its own stepper input, so its add button is left alone. */

  function getDraftQty(id) {
    var draft = getCartDraft();
    for (var i = 0; i < draft.length; i++) {
      if (draft[i].id === id) return draft[i].qty || 0;
    }
    return 0;
  }

  var stepperOriginals = {};

  function showStepper(button, id, name) {
    var isRound = button.classList.contains("rounded-full");
    var wrap = document.createElement("div");
    wrap.className = isRound
      ? "flex items-center gap-0.5 rounded-full border border-neutral-700 theme-bg-card-subtle shrink-0 select-none"
      : "flex items-center gap-0.5 rounded-xl border border-neutral-700 theme-bg-card-subtle shrink-0 select-none";
    wrap.setAttribute("data-stepper-for", id);
    var minus = document.createElement("button");
    minus.type = "button";
    minus.className =
      "min-h-[36px] min-w-[36px] flex items-center justify-center rounded-full theme-btn-secondary text-base font-bold";
    minus.textContent = "−";
    minus.setAttribute("aria-label", "کاهش تعداد");
    var label = document.createElement("span");
    label.className = "min-w-[24px] text-center text-sm font-bold";
    label.textContent = toPersianDigits(getDraftQty(id));
    var plus = document.createElement("button");
    plus.type = "button";
    plus.className = minus.className;
    plus.textContent = "+";
    plus.setAttribute("aria-label", "افزایش تعداد");
    minus.addEventListener("click", function () {
      var current = getDraftQty(id);
      if (current <= 1) {
        removeFromCartDraft(id);
        var original = stepperOriginals[id];
        if (original && wrap.parentNode) {
          wrap.parentNode.replaceChild(original, wrap);
        }
      } else {
        setCartDraftQty(id, current - 1);
        label.textContent = toPersianDigits(current - 1);
      }
    });
    plus.addEventListener("click", function () {
      addToCartDraft({ id: id, name: name, qty: 1 });
      label.textContent = toPersianDigits(getDraftQty(id));
    });
    wrap.appendChild(minus);
    wrap.appendChild(label);
    wrap.appendChild(plus);
    stepperOriginals[id] = button;
    if (button.parentNode) {
      button.parentNode.replaceChild(wrap, button);
    }
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

    // Add to cart buttons. On listing pages the button becomes a
    // quantity stepper once the item is in the draft (see showStepper);
    // the product page has its own stepper input, so its button keeps
    // the plain checkmark behaviour.
    var hasPageStepper = !!document.getElementById("product-quantity");
    var addButtons = document.querySelectorAll("[data-add-to-cart]");
    addButtons.forEach(function (button) {
      var productId = button.getAttribute("data-product-id");
      var productName = button.getAttribute("data-product-name");
      button.addEventListener("click", function () {
        var qtyInput = document.getElementById("product-quantity");
        var qty = qtyInput ? parseInt(qtyInput.value, 10) || 1 : 1;
        addToCartDraft({ id: productId, name: productName, qty: qty });
        if (hasPageStepper) {
          if (button.classList.contains("rounded-full")) {
            button.textContent = "✓";
          } else {
            button.textContent = "✓ در سبد";
          }
        } else {
          showStepper(button, productId, productName);
        }
      });
      if (!hasPageStepper && getDraftQty(productId) > 0) {
        showStepper(button, productId, productName);
      }
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
    toPersianDigits: toPersianDigits,
  };
})();
