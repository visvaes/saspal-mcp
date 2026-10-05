const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const sendButton = document.querySelector("#send-button");
const clearConversationButton = document.querySelector("#clear-conversation");
const logoutButton = document.querySelector("#logout-button");
const conversation = document.querySelector("#conversation");
const composeEmailButton = document.querySelector("#compose-email-button");
const emailDialog = document.querySelector("#email-dialog");
const emailDraftForm = document.querySelector("#email-draft-form");
const emailComposeFields = document.querySelector("#email-compose-fields");
const emailReview = document.querySelector("#email-review");
const emailDialogStatus = document.querySelector("#email-dialog-status");
const draftEmailButton = document.querySelector("#draft-email-button");
const confirmSendEmailButton = document.querySelector("#confirm-send-email");
const cancelEmailDraftButton = document.querySelector("#cancel-email-draft");
const emailIntent = window.SASPAL_EMAIL_INTENT;
let pendingEmailDraftId = null;
const history = [];
const SAFE_CHAT_STATUSES = new Set([
  "Understanding your request...",
  "Checking Gmail...",
  "Checking available information...",
  "Analyzing results...",
  "Preparing response...",
]);

function redactHistoryText(value) {
  const text = String(value || "");
  if (/\b(?:credentials|token)\.json\b/i.test(text)) return "[REDACTED]";
  return text
    .replace(
      /\b((?:verification|security|authentication|auth|login|sign[- ]in)\s+(?:code|passcode)|one[- ]time\s+(?:code|password)|passcode|otp)(\s*(?:is|was|:|=|#)\s*|\s+)([A-Z0-9_-]{4,16})\b/gi,
      "$1$2[REDACTED]",
    )
    .replace(
      /\b(api[\s_-]*key|access[\s_-]*token|refresh[\s_-]*token|oauth[\s_-]*token|auth[\s_-]*token|client[\s_-]*secret|password|passwd|passphrase)(\s*(?:is|:|=)\s*)(?:"[^"]+"|'[^']+'|[^\s,;]+)/gi,
      "$1$2[REDACTED]",
    )
    .replace(
      /(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|oauth[_-]?token|auth[_-]?token|client[_-]?(?:secret|id)|private[_-]?key|id[_-]?token|token|password|passwd|passphrase)["']?\s*:\s*)(?:"[^"]*"|'[^']*'|[^,\s}\]]+)/gi,
      '$1"[REDACTED]"',
    )
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, "Bearer [REDACTED]");
}

function rememberConversation(userMessage, assistantMessage, emails = []) {
  const emailContext = emails.length
    ? `\nRetrieved email records: ${JSON.stringify(emails.slice(0, 10))}`
    : "";
  history.push(
    { role: "user", content: redactHistoryText(userMessage).slice(0, 12000) },
    {
      role: "assistant",
      content: redactHistoryText(`${assistantMessage}${emailContext}`).slice(0, 12000),
    },
  );
  if (history.length > 24) history.splice(0, history.length - 24);
}

function updatePendingStatus(pending, status) {
  if (!SAFE_CHAT_STATUSES.has(status)) return;
  const label = pending.querySelector(".loading-status");
  if (label) label.textContent = status;
}

async function readChatStream(response, pending) {
  if (!response.body) throw new Error("The server returned an empty response.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  let emails = [];

  function consumeEvent(block) {
    let eventName = "message";
    const dataLines = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) return;
    const payload = JSON.parse(dataLines.join("\n"));

    if (eventName === "status") updatePendingStatus(pending, payload.status);
    else if (eventName === "emails" && Array.isArray(payload.emails)) emails = payload.emails;
    else if (eventName === "done") result = payload;
    else if (eventName === "error") throw new Error(payload.message || "The chat request failed.");
  }

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
    let separator;
    while ((separator = buffer.indexOf("\n\n")) !== -1) {
      consumeEvent(buffer.slice(0, separator));
      buffer = buffer.slice(separator + 2);
    }
    if (done) break;
  }

  if (buffer.trim()) consumeEvent(buffer);
  if (!result || typeof result.response !== "string") {
    throw new Error("The server ended the response before it was complete.");
  }
  return { ...result, emails };
}

function cleanText(value) {
  return String(value || "")
    .replace(/\*\*/g, "")
    .replace(/^[\s\-–—]+|[\s\-–—]+$/g, "")
    .trim();
}

function createField(label, value) {
  const row = document.createElement("div");
  row.className = "email-field";

  const fieldLabel = document.createElement("span");
  fieldLabel.className = "email-label";
  fieldLabel.textContent = label;

  const fieldValue = document.createElement("strong");
  fieldValue.className = "email-value";
  fieldValue.textContent = cleanText(value) || "—";

  row.append(fieldLabel, fieldValue);
  return row;
}

function renderEmailCards(responseText) {
  const text = responseText.trim();
  const cards = [];
  const regex = /(?:^|\n)\s*(\d+)\.\s*(?:\*\*)?From(?:\*\*)?:\s*(.+?)\s*(?:\*\*)?Subject(?:\*\*)?:\s*(.+?)\s*(?:\*\*)?Date(?:\*\*)?:\s*(.+?)\s*(?:\*\*)?Snippet(?:\*\*)?:\s*(.+?)(?=\n\s*(?:\d+\.|(?:If you need)|(?:Let me know)|(?:Email Summary)|(?:Needs Attention)|$))/gs;

  let match;
  while ((match = regex.exec(text)) !== null) {
    const [, index, sender, subject, date, snippet] = match;
    cards.push({ index, sender, subject, date, snippet });
  }

  if (cards.length === 0) return null;

  const container = document.createElement("div");
  container.className = "response-body";

  const list = document.createElement("div");
  list.className = "email-card-list";

  cards.forEach(({ index, sender, subject, date, snippet }) => {
    const card = document.createElement("article");
    card.className = "email-card";

    const header = document.createElement("div");
    header.className = "email-card-header";

    const number = document.createElement("span");
    number.className = "email-index";
    number.textContent = `#${index}`;

    const itemDate = document.createElement("span");
    itemDate.className = "email-date";
    itemDate.textContent = cleanText(date);

    header.append(number, itemDate);

    const stack = document.createElement("div");
    stack.className = "email-card-grid";
    stack.append(
      createField("Sender", sender),
      createField("Subject", subject),
      createField("Date", date),
      createField("Summary", snippet)
    );

    card.append(header, stack);
    list.append(card);
  });

  const attentionMatch = text.match(/Needs Attention\s*:?\s*([\s\S]*)$/i);
  const attentionText = attentionMatch ? cleanText(attentionMatch[1]) : "None";
  const attentionBox = document.createElement("div");
  attentionBox.className = "attention-box";
  const title = document.createElement("strong");
  title.textContent = "Needs Attention";
  const textNode = document.createElement("div");
  textNode.textContent = attentionText || "No obvious action required.";
  attentionBox.append(title, textNode);

  container.append(list, attentionBox);
  return container;
}

function renderAssistantResponse(responseText) {
  const text = String(responseText || "").trim();
  const wrapper = document.createElement("div");
  wrapper.className = "response-body";

  const emailList = renderEmailCards(text);
  if (emailList) {
    wrapper.append(emailList);
    return wrapper;
  }

  const body = document.createElement("div");
  body.className = "plain-response";
  body.textContent = text || "No response received.";
  wrapper.append(body);
  return wrapper;
}

function createEmailCard(email, { includeOpenButton = true } = {}) {
  const card = document.createElement("article");
  card.className = "email-card";

  const header = document.createElement("div");
  header.className = "email-card-header";

  const sender = cleanText(email.sender || email.from || "Unknown sender");
  const bodyText = cleanText(email.body || email.snippet || "");
  const subject = cleanText(email.subject || "No subject");
  const dateText = cleanText(email.date || "Unknown date");
  const needsAttention = /reply|response|follow up|invoice|payment|verification|urgent|meeting|invitation|review|action/i.test(`${subject}\n${bodyText}`);

  const label = document.createElement("span");
  label.className = "email-index";
  label.textContent = email.message_id ? "MESSAGE" : "EMAIL";

  const time = document.createElement("span");
  time.className = "email-date";
  time.textContent = dateText || "No date";

  header.append(label, time);

  const fields = document.createElement("div");
  fields.className = "email-card-grid";
  fields.append(
    createField("Sender", sender),
    createField("Subject", subject),
    createField("Date", dateText),
    createField("Summary", bodyText || "No summary available.")
  );

  if (needsAttention) {
    const attention = document.createElement("div");
    attention.className = "attention-inline";
    attention.textContent = "Needs Attention";
    fields.append(attention);
  }

  if (email.message_id) {
    const openButton = document.createElement("button");
    openButton.type = "button";
    openButton.className = "email-open-button";
    openButton.textContent = "Open full email";
    openButton.addEventListener("click", () => openEmailDetail(email.message_id));
    fields.append(openButton);
  }

  card.append(header, fields);
  return card;
}

function createResultPanel(title, rows) {
  const container = document.createElement("div");
  container.className = "response-body";

  const heading = document.createElement("div");
  heading.className = "result-header";
  heading.textContent = title;

  const list = document.createElement("div");
  list.className = "email-card-list";

  if (!rows || rows.length === 0) {
    const empty = document.createElement("div");
    empty.className = "plain-response";
    empty.textContent = "No matching emails were found.";
    container.append(heading, empty);
    return container;
  }

  rows.forEach((email) => list.append(createEmailCard(email)));
  container.append(heading, list);
  return container;
}

function renderEmailDetail(email) {
  const container = document.createElement("div");
  container.className = "response-body";

  const heading = document.createElement("div");
  heading.className = "result-header";
  heading.textContent = "Selected email";

  const body = document.createElement("article");
  body.className = "email-card";

  const meta = document.createElement("div");
  meta.className = "email-card-grid";
  meta.append(
    createField("Sender", email.sender || email.from || "Unknown sender"),
    createField("Subject", email.subject || "No subject"),
    createField("Date", email.date || "Unknown date"),
    createField("Recipient", email.recipient || "Unknown recipient")
  );

  const content = document.createElement("div");
  content.className = "email-body";
  const bodyText = cleanText(email.body || email.snippet || "No message body available.");
  content.textContent = bodyText;

  const attention = document.createElement("div");
  attention.className = "attention-box";
  const attentionTitle = document.createElement("strong");
  attentionTitle.textContent = "Needs Attention";
  const attentionText = document.createElement("div");
  const needsAttention = /reply|response|follow up|invoice|payment|verification|urgent|meeting|invitation|review|action/i.test(bodyText);
  attentionText.textContent = needsAttention ? "This email appears to require a reply or action." : "No obvious action required.";
  attention.append(attentionTitle, attentionText);

  body.append(meta, content, attention);
  container.append(heading, body);
  return container;
}

function appendMessage(role, content, { loading = false, statusText = "Thinking...", richContent = null } = {}) {
  const row = document.createElement("div");
  row.className = `message-row ${role === "assistant" ? "assistant-row" : "user-row"}`;

  const avatar = document.createElement("div");
  avatar.className = `avatar ${role === "assistant" ? "assistant-avatar" : "user-avatar"}`;
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = role === "assistant" ? "S" : "Y";

  const body = document.createElement("div");
  body.className = "message-content";

  const meta = document.createElement("div");
  meta.className = "message-meta";
  const name = document.createElement("strong");
  name.textContent = role === "assistant" ? "SASPAL Assistant" : "You";
  const time = document.createElement("span");
  time.textContent = "NOW";
  meta.append(name, time);

  const bubble = document.createElement("div");
  bubble.className = `bubble ${role === "assistant" ? "assistant-bubble" : "user-bubble"}`;

  if (loading) {
    bubble.classList.add("loading-bubble");
    bubble.setAttribute("aria-label", "Assistant is thinking");

    const loadingContent = document.createElement("div");
    loadingContent.className = "loading-content";

    const dots = document.createElement("div");
    dots.className = "loading-dots";
    for (let index = 0; index < 3; index += 1) {
      const dot = document.createElement("span");
      dots.append(dot);
    }

    const label = document.createElement("span");
    label.className = "loading-status";
    label.textContent = statusText;

    loadingContent.append(dots, label);
    bubble.append(loadingContent);
  } else if (richContent) {
    bubble.append(richContent);
  } else {
    bubble.textContent = content;
  }

  body.append(meta, bubble);
  if (role === "assistant") row.append(avatar, body);
  else row.append(body, avatar);
  conversation.append(row);
  conversation.scrollTop = conversation.scrollHeight;
  return row;
}

function resetEmailDialog() {
  emailDraftForm.reset();
  emailComposeFields.hidden = false;
  emailReview.hidden = true;
  emailDialogStatus.textContent = "";
  draftEmailButton.disabled = false;
  confirmSendEmailButton.disabled = false;
  cancelEmailDraftButton.disabled = false;
}

async function discardEmailDraft(draftId) {
  try {
    await fetchJson("/api/email-drafts/cancel", {
      method: "POST",
      body: JSON.stringify({ draft_id: draftId }),
    });
  } catch {
    // The draft expires automatically if the server cannot be reached to discard it.
  }
}

function closeEmailDialog() {
  const draftId = pendingEmailDraftId;
  pendingEmailDraftId = null;
  emailDialog.close();
  resetEmailDialog();
  if (draftId) void discardEmailDraft(draftId);
}

function showEmailReview(draft) {
  pendingEmailDraftId = draft.draft_id;
  document.querySelector("#review-email-to").textContent = draft.to;
  document.querySelector("#review-email-subject").textContent = draft.subject;
  document.querySelector("#review-email-body").textContent = draft.body;
  emailComposeFields.hidden = true;
  emailReview.hidden = false;
  emailDialogStatus.textContent = "Review the recipient and message before confirming.";
  confirmSendEmailButton.focus();
}

async function requestEmailDraft(request) {
  return fetchJson("/api/email-drafts", {
    method: "POST",
    body: JSON.stringify({
      to: request.to,
      instructions: request.instructions || "",
      subject: request.subject,
      body: request.body,
    }),
  });
}

async function createEmailDraft(to, instructions) {
  draftEmailButton.disabled = true;
  emailDialogStatus.textContent = "Preparing draft...";
  try {
    const draft = await requestEmailDraft({ to, instructions });
    showEmailReview(draft);
  } catch (error) {
    emailComposeFields.hidden = false;
    emailReview.hidden = true;
    emailDialogStatus.textContent = error.message || "Could not prepare the draft.";
    draftEmailButton.disabled = false;
  }
}

async function fetchJson(url, options = {}) {
  const csrfCookie = document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith("saspal_csrf="));
  const csrfToken = csrfCookie ? decodeURIComponent(csrfCookie.slice("saspal_csrf=".length)) : "";
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
      ...options.headers,
    },
    ...options,
  });

  const payload = await response.json().catch(() => null);
  if (response.status === 401) {
    window.location.assign("/login?next=/");
    throw new Error("Your session has expired. Please sign in again.");
  }
  if (!response.ok) {
    const detail = payload && payload.detail ? payload.detail : "The request failed.";
    throw new Error(detail);
  }

  return payload;
}

async function openEmailDetail(messageId) {
  const pendingId = `${Date.now()}-open-email`;
  const pending = appendMessage("assistant", "", { loading: true, statusText: "Reading email..." });
  pending.id = pendingId;

  try {
    const email = await fetchJson(`/api/email/${encodeURIComponent(messageId)}`);
    pending.remove();
    appendMessage("assistant", "", { richContent: renderEmailDetail(email) });
  } catch (error) {
    pending.remove();
    appendMessage("assistant", `I couldn't open that email. ${error.message}`);
  }
}

composeEmailButton.addEventListener("click", () => {
  resetEmailDialog();
  emailDialog.showModal();
  document.querySelector("#email-to").focus();
});

document.querySelector("#close-email-dialog").addEventListener("click", closeEmailDialog);
document.querySelector("#cancel-email-compose").addEventListener("click", closeEmailDialog);
cancelEmailDraftButton.addEventListener("click", closeEmailDialog);
emailDialog.addEventListener("close", () => {
  if (pendingEmailDraftId) {
    const draftId = pendingEmailDraftId;
    pendingEmailDraftId = null;
    void discardEmailDraft(draftId);
  }
});

emailDraftForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await createEmailDraft(
    document.querySelector("#email-to").value,
    document.querySelector("#email-instructions").value,
  );
});

confirmSendEmailButton.addEventListener("click", async () => {
  if (!pendingEmailDraftId || confirmSendEmailButton.disabled) return;
  const draftId = pendingEmailDraftId;
  confirmSendEmailButton.disabled = true;
  cancelEmailDraftButton.disabled = true;
  emailDialogStatus.textContent = "Sending the reviewed email...";
  try {
    await fetchJson("/api/email-drafts/confirm", {
      method: "POST",
      body: JSON.stringify({ draft_id: draftId }),
    });
    pendingEmailDraftId = null;
    emailDialog.close();
    resetEmailDialog();
    appendMessage("assistant", "Email sent.");
  } catch (error) {
    emailDialogStatus.textContent = error.message || "Gmail could not send the email.";
    confirmSendEmailButton.disabled = false;
    cancelEmailDraftButton.disabled = false;
    pendingEmailDraftId = null;
  }
});

async function sendMessage(value = input.value) {
  const message = value.trim();
  if (!message || sendButton.disabled) return;

  appendMessage("user", message);
  input.value = "";
  input.style.height = "auto";
  sendButton.disabled = true;

  const pending = appendMessage("assistant", "", {
    loading: true,
    statusText: "Understanding your request...",
  });

  try {
    const emailRequestHandled = await emailIntent.routeEmailSendRequest(message, {
      prepareDraft: async (request) => {
        pending.remove();
        resetEmailDialog();
        emailDialog.showModal();
        document.querySelector("#email-to").value = request.to;
        document.querySelector("#email-instructions").value = request.instructions || "";
        draftEmailButton.disabled = true;
        emailDialogStatus.textContent = "Preparing draft...";
        try {
          return await requestEmailDraft(request);
        } catch (error) {
          emailDialogStatus.textContent = error.message || "Could not prepare the draft.";
          draftEmailButton.disabled = false;
          return null;
        }
      },
      openReview: (draft) => {
        if (draft) showEmailReview(draft);
      },
      openRecipientEntry: (request) => {
        pending.remove();
        resetEmailDialog();
        emailDialog.showModal();
        document.querySelector("#email-instructions").value = request.instructions || "";
        emailDialogStatus.textContent = "Enter the recipient to prepare this email for review.";
        document.querySelector("#email-to").focus();
      },
    });
    if (emailRequestHandled) {
      return;
    }

    const response = await fetch("/chat/stream", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": readCsrfToken(),
      },
      body: JSON.stringify({ message, history }),
    });

    if (!response.ok) {
      if (response.status === 401) {
        window.location.assign("/login?next=/");
        return;
      }
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        throw new Error("The server returned an invalid response.");
      }
      throw new Error(payload.detail || "The chat request failed.");
    }

    const payload = await readChatStream(response, pending);
    pending.remove();
    const renderedResponse = renderAssistantResponse(payload.response);
    if (payload.emails.length) {
      const combined = document.createElement("div");
      combined.className = "response-body";
      combined.append(renderedResponse, createResultPanel("Gmail results", payload.emails));
      appendMessage("assistant", "", { richContent: combined });
    } else {
      appendMessage("assistant", "", { richContent: renderedResponse });
    }
    rememberConversation(message, payload.response, payload.emails);
  } catch (error) {
    pending.remove();
    appendMessage("assistant", `I couldn't complete that request. ${error.message}`);
  } finally {
    sendButton.disabled = false;
    if (!emailDialog.open) input.focus();
  }
}

function resetConversation() {
  history.length = 0;
  conversation.innerHTML = "";
  const welcome = document.createElement("div");
  welcome.className = "message-row assistant-row";
  welcome.innerHTML = `
    <div class="avatar assistant-avatar" aria-hidden="true">S</div>
    <div class="message-content">
      <div class="message-meta"><strong>SASPAL Assistant</strong><span>NOW</span></div>
      <div class="bubble assistant-bubble">Hi! I'm your SASPAL Gmail Assistant. I can help you search, summarize, and understand your emails, or answer general questions.</div>
    </div>
  `;
  conversation.append(welcome);
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage();
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.prompt));
});

if (clearConversationButton) {
  clearConversationButton.addEventListener("click", () => {
    resetConversation();
  });
}

async function updateGmailConnectionStatus() {
  const status = document.querySelector("#gmail-connection-status");
  if (!status) return;
  const warning = document.querySelector("#gmail-access-warning");
  const accessRow = document.querySelector("#gmail-access-row");
  const accessLabel = document.querySelector("#gmail-access-label");
  const accessIndicator = document.querySelector("#gmail-access-indicator");

  function renderConnectionState(connected) {
    status.classList.toggle("is-disconnected", !connected);
    if (warning) warning.hidden = connected;
    if (accessRow) accessRow.classList.toggle("is-disconnected", !connected);
    if (accessLabel) accessLabel.textContent = connected ? "Send requires confirmation" : "Connection required";
    if (accessIndicator) {
      accessIndicator.textContent = connected ? "✓" : "!";
      accessIndicator.setAttribute("aria-label", connected ? "Connected" : "Not connected");
    }
    if (composeEmailButton) {
      composeEmailButton.disabled = !connected;
      composeEmailButton.title = connected ? "" : "Connect Gmail to compose emails";
    }
  }

  try {
    const response = await fetch("/api/gmail-status", {
      credentials: "same-origin",
      headers: {
        "X-CSRF-Token": readCsrfToken(),
      },
    });
    const payload = await response.json().catch(() => ({ connected: false }));
    const connected = response.ok && Boolean(payload.connected);
    renderConnectionState(connected);
    status.innerHTML = connected
      ? '<span class="status-dot"></span> Gmail connected <span class="status-separator">·</span> Send requires confirmation'
      : '<span class="status-dot"></span> Gmail access required <span class="status-separator">·</span> <a href="/connect-gmail">Connect Gmail</a>';
  } catch {
    renderConnectionState(false);
    status.innerHTML = '<span class="status-dot"></span> Gmail access required <span class="status-separator">·</span> <a href="/connect-gmail">Connect Gmail</a>';
  }
}

function readCsrfToken() {
  const cookie = document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith("saspal_csrf="));
  return cookie ? decodeURIComponent(cookie.slice("saspal_csrf=".length)) : "";
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    void updateGmailConnectionStatus();
  });
} else {
  void updateGmailConnectionStatus();
}

if (logoutButton) {
  logoutButton.addEventListener("click", async () => {
    logoutButton.disabled = true;
    try {
      await fetchJson("/auth/logout", {
        method: "POST",
        body: JSON.stringify({}),
      });
    } catch {
      // Always leave the protected view; the server session will expire if logout failed.
    } finally {
      window.location.assign("/login");
    }
  });
}
