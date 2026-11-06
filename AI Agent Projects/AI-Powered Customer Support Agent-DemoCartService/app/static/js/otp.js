/* OTP verification UI controller (Phase 2).
 * Exposes window.OTP with show()/hide() so the chat controller can trigger
 * verification when the backend signals authentication_required. Talks to the
 * /api/auth/* endpoints. In dev mode, displays the mock OTP returned by the
 * backend so the demo is self-contained. */

(function () {
  "use strict";

  const panel = document.getElementById("otp-panel");
  const stepIdentify = document.getElementById("otp-step-identify");
  const stepVerify = document.getElementById("otp-step-verify");
  const refInput = document.getElementById("otp-ref");
  const sendBtn = document.getElementById("otp-send-btn");
  const sentHint = document.getElementById("otp-sent-hint");
  const devHint = document.getElementById("otp-dev-hint");
  const codeInput = document.getElementById("otp-code");
  const verifyBtn = document.getElementById("otp-verify-btn");
  const resendBtn = document.getElementById("otp-resend-btn");
  const feedback = document.getElementById("otp-feedback");

  const devMode = !!(window.APP_CONFIG && window.APP_CONFIG.devMode);

  let currentCustomerId = null;
  let currentReference = null;
  let currentReferenceType = null;
  let onVerifiedCallback = null;

  function setFeedback(text, kind) {
    feedback.textContent = text || "";
    feedback.className = "otp-feedback" + (kind ? " " + kind : "");
  }

  // The main chat composer is disabled while verifying so there is only one
  // active input at a time (avoids the confusing "two input boxes" state).
  function setComposerDisabled(disabled) {
    const composerInput = document.getElementById("message-input");
    const composerBtn = document.getElementById("send-btn");
    if (composerInput) composerInput.disabled = disabled;
    if (composerBtn) composerBtn.disabled = disabled;
  }

  function show(options) {
    options = options || {};
    onVerifiedCallback = options.onVerified || null;
    currentReference = null;
    currentReferenceType = null;
    panel.hidden = false;
    setFeedback("");
    setComposerDisabled(true);

    if (options.prefillRef) {
      // We already know the order reference from the chat message, so skip the
      // "enter your reference" step and request the code straight away.
      refInput.value = options.prefillRef;
      sendCode();
    } else {
      stepIdentify.hidden = false;
      stepVerify.hidden = true;
      refInput.focus();
    }
  }

  function hide() {
    panel.hidden = true;
    setFeedback("");
    codeInput.value = "";
    setComposerDisabled(false);
  }

  function isActive() {
    return !panel.hidden;
  }

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(function () {
      return {};
    });
    return { ok: res.ok, data: data };
  }

  async function sendCode() {
    const ref = refInput.value.trim();
    if (!ref) {
      setFeedback("Please enter your order ID or customer ID.", "error");
      return;
    }
    if (sendBtn.disabled) return;

    sendBtn.disabled = true;
    setFeedback("Sending code...");

    // Accept either an order reference or a customer ID. Preserve which type
    // was used so the pending chat request can continue correctly after OTP.
    const isCustomerRef = /^CUST[-_]/i.test(ref);
    const body = isCustomerRef ? { customer_ref: ref } : { order_ref: ref };

    try {
      const { ok, data } = await postJSON("/api/auth/initiate", body);
      if (!ok || !data.success) {
        setFeedback(data.message || "Could not send a code. Please try again.", "error");
        return;
      }

      currentCustomerId = data.customer_id;
      currentReference = ref.toUpperCase();
      currentReferenceType = isCustomerRef ? "customer" : "order";
      stepIdentify.hidden = true;
      stepVerify.hidden = false;
      sentHint.textContent =
        "We sent a code to " + (data.masked_destination || "your registered number") + ".";

      if (devMode && data.dev_otp && devHint) {
        devHint.hidden = false;
        devHint.textContent = "DEV MODE: use code " + data.dev_otp;
      }
      setFeedback("");
      codeInput.value = "";
      codeInput.focus();
    } catch (err) {
      setFeedback("Network error while sending the code. Please try again.", "error");
    } finally {
      sendBtn.disabled = false;
    }
  }

  async function verifyCode() {
    const code = codeInput.value.trim();
    if (!code) {
      setFeedback("Enter the code you received.", "error");
      return;
    }
    verifyBtn.disabled = true;
    setFeedback("Verifying...");

    const { ok, data } = await postJSON("/api/auth/verify", {
      customer_id: currentCustomerId,
      code: code,
    });
    verifyBtn.disabled = false;

    if (ok && data.success) {
      setFeedback("Verified! You can continue.", "success");
      const cb = onVerifiedCallback;
      const verifiedCustomer = currentCustomerId;
      setTimeout(function () {
        hide();
        if (cb) cb(verifiedCustomer, currentReference, currentReferenceType);
      }, 700);
      return;
    }
    setFeedback(data.message || "Verification failed.", "error");
  }

  sendBtn.addEventListener("click", sendCode);
  refInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      sendCode();
    }
  });
  resendBtn.addEventListener("click", function () {
    stepVerify.hidden = true;
    stepIdentify.hidden = false;
    setFeedback("");
  });
  verifyBtn.addEventListener("click", verifyCode);
  codeInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") verifyCode();
  });

  window.OTP = { show: show, hide: hide, isActive: isActive };
})();
