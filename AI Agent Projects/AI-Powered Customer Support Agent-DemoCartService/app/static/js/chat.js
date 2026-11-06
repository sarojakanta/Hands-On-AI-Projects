/* Customer chat UI controller (Phase 1).
 * Handles message sending, conversation history rendering, and the loading
 * indicator. OTP and escalation UI are layered on in later phases. The
 * conversation_id returned by the backend is reused to keep context. */

(function () {
  "use strict";

  const history = document.getElementById("chat-history");
  const form = document.getElementById("composer");
  const input = document.getElementById("message-input");
  const sendBtn = document.getElementById("send-btn");
  const clearSessionBtn = document.getElementById("dev-clear-session");

  let conversationId = null;

  function extractCustomerRef(text) {
    const m = text.match(/\b(CUST[-_]\d+)\b/i);
    return m ? m[1].toUpperCase() : "";
  }

  function extractOrderRef(text) {
    // Customer IDs also fit the broad AA-123 pattern, so exclude them before
    // deciding that a reference identifies an order.
    const m = text.match(/\b([A-Z]{2,5}-\d{3,})\b/i);
    if (!m || /^CUST[-_]/i.test(m[1])) return "";
    return m[1].toUpperCase();
  }

  function extractVerificationRef(text) {
    return extractCustomerRef(text) || extractOrderRef(text);
  }

  function timeLabel() {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function addMessage(text, sender) {
    const el = document.createElement("div");
    el.className = "msg " + sender;
    el.textContent = text;
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = (sender === "user" ? "You" : "Assistant") + " · " + timeLabel();
    el.appendChild(meta);
    history.appendChild(el);
    history.scrollTop = history.scrollHeight;
  }

  function showTyping() {
    const el = document.createElement("div");
    el.className = "typing";
    el.id = "typing-indicator";
    el.innerHTML = "<span></span><span></span><span></span>";
    history.appendChild(el);
    history.scrollTop = history.scrollHeight;
  }

  function hideTyping() {
    const el = document.getElementById("typing-indicator");
    if (el) el.remove();
  }

  function setBusy(busy) {
    const verifying = !!(
      window.OTP && window.OTP.isActive && window.OTP.isActive()
    );
    sendBtn.disabled = busy || verifying;
    input.disabled = busy || verifying;
  }

  if (clearSessionBtn) {
    clearSessionBtn.addEventListener("click", async function () {
      if (!window.confirm("Clear this app's session and start a fresh chat?")) return;

      clearSessionBtn.disabled = true;
      try {
        const res = await fetch("/api/auth/dev-clear-session", { method: "POST" });
        const data = await res.json();
        if (!res.ok || !data.success) {
          throw new Error(data.message || "Could not clear the app session.");
        }
        window.location.reload();
      } catch (err) {
        window.alert(err.message || "Could not clear the app session.");
        clearSessionBtn.disabled = false;
      }
    });
  }

  async function sendMessage(text) {
    addMessage(text, "user");
    setBusy(true);
    showTyping();

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: conversationId }),
      });
      const data = await res.json();
      hideTyping();

      if (!res.ok) {
        addMessage(data.error || "Something went wrong. Please try again.", "agent");
        return;
      }

      conversationId = data.conversation_id || conversationId;
      addMessage(data.reply, "agent");

      // If verification is required, preserve the pending request. An order
      // reference used for OTP can complete that request; a customer reference
      // verifies identity only, so the backend will ask for a specific order.
      if (data.authentication_required && window.OTP) {
        const pendingText = text;
        const prefill = extractVerificationRef(text);
        window.OTP.show({
          prefillRef: prefill,
          onVerified: function (_customerId, verificationRef, referenceType) {
            // A customer ID proves identity but is not an order ID. Do not
            // replay it into chat, where it could be interpreted as an order
            // lookup. The pending backend intent remains available for the
            // actual order ID entered next.
            if (referenceType === "customer") {
              addMessage(
                "Identity verified. Please enter the order ID you want to check.",
                "agent"
              );
              return;
            }

            addMessage("Identity verified. Continuing with your request.", "agent");
            let continuation = pendingText;
            if (verificationRef && !extractOrderRef(pendingText)) {
              continuation += " " + verificationRef;
            }
            sendMessage(continuation);
          },
        });
      }
    } catch (err) {
      hideTyping();
      addMessage("Network error. Please check your connection and try again.", "agent");
    } finally {
      setBusy(false);
      if (!input.disabled) input.focus();
    }
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    sendMessage(text);
  });

  // Greeting on load. Includes the AI disclosure (G-5): the customer is told
  // they're chatting with an AI and can reach a human at any time.
  addMessage(
    "Hi! You're chatting with DemoCartService's AI support assistant. I can help with " +
      "orders, delivery, store info, and our policies. You can ask for a human " +
      "agent at any time.",
    "agent"
  );
})();
