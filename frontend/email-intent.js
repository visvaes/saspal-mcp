(function attachEmailIntent(root, factory) {
  const emailIntent = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = emailIntent;
  }
  root.SASPAL_EMAIL_INTENT = emailIntent;
})(globalThis, function createEmailIntentModule() {
  const SEND_INTENT = /^\s*(?:(?:(?:can|could|would)\s+you|i want to|i need to|help me)\s+(?:please\s+)?)?(?:please\s+)?(?:send\b|compose\b|email\b|draft\b|write\b|create\b)/i;
  const SEND_CONTEXT = /\b(?:to|for|this\s+(?:mail|email)|(?:mail|email))\b|[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?/i;
  const EMAIL_ADDRESS = /[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?/i;
  const TRAILING_SEND_INTENT = new RegExp(
    `\\b(?:please\\s+)?send\\s+(?:this\\s+)?(?:message|mail|email)?\\s*to\\s+(?:this\\s+)?(?:mail|email)?\\s*[:,-]?\\s*(${EMAIL_ADDRESS.source})\\s*[.!?]*$`,
    "i"
  );
  const QUOTE_PAIRS = new Map([
    ['"', '"'],
    ["'", "'"],
    ["“", "”"],
    ["‘", "’"],
  ]);

  function stripOuterQuotes(value) {
    let text = value.trim();
    const escaped = text.startsWith("\\") && QUOTE_PAIRS.has(text[1]);
    const start = escaped ? 1 : 0;
    const openingQuote = text[start];
    const closingQuote = QUOTE_PAIRS.get(openingQuote);
    const closing = escaped ? `\\${closingQuote}` : closingQuote;
    if (closingQuote && text.endsWith(closing)) {
      text = text.slice(start + 1, -closing.length).trim();
    }
    return text.replace(/\\(["'])/g, "$1");
  }

  function readFieldValue(segment) {
    const text = segment.trim().replace(/^\b(?:and|is)\s+/i, "");
    const escaped = text.startsWith("\\") && QUOTE_PAIRS.has(text[1]);
    const quoteIndex = escaped ? 1 : 0;
    const openingQuote = text[quoteIndex];
    const closingQuote = QUOTE_PAIRS.get(openingQuote);
    if (!closingQuote) {
      return text.replace(/[.!?]+$/, "").trim();
    }

    const delimiter = escaped ? `\\${closingQuote}` : closingQuote;
    const closingIndex = text.lastIndexOf(delimiter);
    if (closingIndex > quoteIndex && /^[\s.!?]*$/.test(text.slice(closingIndex + delimiter.length))) {
      return text.slice(quoteIndex + (escaped ? 2 : 1), closingIndex)
        .replace(/\\(["'])/g, "$1")
        .trim();
    }

    let result = "";
    for (let index = quoteIndex + 1; index < text.length; index += 1) {
      const character = text[index];
      if (character === "\\" && index + 1 < text.length) {
        const next = text[index + 1];
        if (next === closingQuote || next === "\\") {
          result += next;
          index += 1;
          continue;
        }
      }
      if (character === closingQuote) {
        return result;
      }
      result += character;
    }
    return result;
  }

  function findFieldMarkers(text) {
    const outsideQuotes = new Array(text.length).fill(false);
    let closingQuote = null;

    for (let index = 0; index < text.length; index += 1) {
      const character = text[index];
      if (closingQuote) {
        if (character === "\\" && text[index + 1] === closingQuote) {
          index += 1;
          closingQuote = null;
        } else if (character === closingQuote) {
          closingQuote = null;
        }
        continue;
      }

      if (character === "\\" && QUOTE_PAIRS.has(text[index + 1])) {
        closingQuote = QUOTE_PAIRS.get(text[index + 1]);
        index += 1;
        continue;
      }
      if (QUOTE_PAIRS.has(character)) {
        closingQuote = QUOTE_PAIRS.get(character);
        continue;
      }
      outsideQuotes[index] = true;
    }

    const markers = [];
    const markerPattern = /\b(?:(?:with\s+)?subject|(?:(?:and|with)\s+)?(?:message|body))\b\s*[:=]?\s*/gi;
    let match;
    while ((match = markerPattern.exec(text)) !== null) {
      if (!outsideQuotes[match.index]) continue;
      markers.push({
        name: /\bsubject\b/i.test(match[0]) ? "subject" : "body",
        start: match.index,
        end: markerPattern.lastIndex,
      });
    }
    return markers;
  }

  function parseTrailingSendRequest(message) {
    const intentMatch = TRAILING_SEND_INTENT.exec(message);
    if (!intentMatch) return null;

    const emailContent = message.slice(0, intentMatch.index).trim();
    if (!emailContent) return null;

    const lines = emailContent.split(/\r?\n/);
    let subject = null;
    const contentLines = lines.filter((line) => {
      const subjectMatch = line.match(
        /^\s*(?:[-*]\s*)?\*{0,2}subject\*{0,2}\s*:\*{0,2}\s*(.*?)\s*$/i
      );
      if (subjectMatch) {
        subject = subjectMatch[1].trim() || null;
        return false;
      }
      if (/^\s*(?:[-*]\s*)?\*{0,2}to\*{0,2}\s*:\*{0,2}\s*.*$/i.test(line)) {
        return false;
      }
      return !/^\s*---+\s*$/.test(line);
    });
    const body = contentLines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
    if (!body) return null;

    return {
      to: intentMatch[1].replace(/[.!?]+$/, ""),
      subject: null,
      body: null,
      instructions: `Analyze the email below and draft a clear, professional reply to the sender. Keep the reply helpful and direct, and include an appropriate subject line. Email content to analyze:\n\n${body}`,
    };
  }

  function parseEmailSendRequest(message) {
    if (typeof message !== "string") {
      return null;
    }

    const normalizedMessage = stripOuterQuotes(message);
    const trailingRequest = parseTrailingSendRequest(normalizedMessage);
    if (trailingRequest) return trailingRequest;

    const intentMatch = normalizedMessage.match(SEND_INTENT);
    if (!intentMatch || !SEND_CONTEXT.test(normalizedMessage)) {
      return null;
    }

    const requestTail = normalizedMessage.slice(intentMatch[0].length).trim();
    const addressMatch = requestTail.match(
      new RegExp(`(?:\\bto\\s*:?\\s*)?(${EMAIL_ADDRESS.source})`, "i")
    );
    const to = addressMatch ? addressMatch[1].replace(/[.!?]+$/, "") : "";
    const detailText = addressMatch
      ? requestTail.slice(addressMatch.index + addressMatch[0].length).trim()
      : requestTail;
    const markers = findFieldMarkers(detailText);

    let subject = null;
    let body = null;
    markers.forEach((marker, index) => {
      const nextMarker = markers[index + 1];
      const valueSegment = detailText.slice(marker.end, nextMarker ? nextMarker.start : undefined);
      const value = readFieldValue(valueSegment);
      if (marker.name === "subject") {
        subject = value || null;
      } else {
        body = value || null;
      }
    });

    const shortMessage = normalizedMessage.match(/^send\s+(?:a|an)\s+(.+?)\s+msg\b/i);
    const instructions = subject && body
      ? null
      : shortMessage
        ? `Write a brief email saying: ${shortMessage[1].trim()}`
        : normalizedMessage.trim();
    return { to, subject, body, instructions };
  }

  async function routeEmailSendRequest(message, actions) {
    const request = parseEmailSendRequest(message);
    if (!request) {
      if (actions.onNotSendIntent) {
        actions.onNotSendIntent();
      }
      return false;
    }

    if (!request.to) {
      actions.openRecipientEntry(request);
      return true;
    }

    const draft = await actions.prepareDraft(request);
    actions.openReview(draft, request);
    return true;
  }

  return { parseEmailSendRequest, routeEmailSendRequest };
});