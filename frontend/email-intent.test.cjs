const assert = require("node:assert/strict");
const test = require("node:test");
const {
  parseEmailSendRequest,
  routeEmailSendRequest,
} = require("./email-intent.js");

const expected = {
  to: "visvaes1026@gmail.com",
  subject: "SASPAL Gmail MCP Test",
  body: "This is a test email from the SASPAL Gmail MCP.",
};

const normalRequest =
  'Send an email to visvaes1026@gmail.com with subject "SASPAL Gmail MCP Test" and message "This is a test email from the SASPAL Gmail MCP."';
const smartQuotedRequest =
  "\u201cSend an email to visvaes1026@gmail.com with subject \u201cSASPAL Gmail MCP Test\u201d and message \u201cThis is a test email from the SASPAL Gmail MCP.\u201d\u201d";
const escapedQuotedRequest = String.raw`Send an email to visvaes1026@gmail.com with subject \"SASPAL Gmail MCP Test\" and message \"This is a test email from the SASPAL Gmail MCP.\"`;

for (const [name, requestText] of [
  ["normal quotes", normalRequest],
  ["smart quotes", smartQuotedRequest],
  ["escaped quotes", escapedQuotedRequest],
]) {
  test(`extracts recipient, subject, and body with ${name}`, () => {
    const request = parseEmailSendRequest(requestText);
    assert.ok(request);
    assert.equal(request.to, expected.to);
    assert.equal(request.subject, expected.subject);
    assert.equal(request.body, expected.body);
  });
}

test("recognizes the supported explicit send phrases", () => {
  for (const phrase of [
    "Send an email to recipient@example.test",
    "Send mail to recipient@example.test",
    "Email recipient@example.test",
    "Email recipient@example.test with subject \"Hello\" and message \"Hi\"",
    "Compose an email to recipient@example.test",
    "Send an email to recipient@example.test",
    "Send a hi message to this mail visvaes1026@gmail.com",
  ]) {
    assert.ok(parseEmailSendRequest(phrase), phrase);
  }
});

test("shorthand send request becomes a draft request", () => {
  const request = parseEmailSendRequest("send a hi msg to recipient@example.test");
  assert.equal(request.to, "recipient@example.test");
  assert.match(request.instructions, /hi/i);
});

test("keeps subject and body marker words inside the email text", () => {
  const request = parseEmailSendRequest(
    'Send an email to recipient@example.test with subject "Email Subject" and message "The body includes the words subject, message, and body."'
  );
  assert.equal(request.subject, "Email Subject");
  assert.equal(request.body, "The body includes the words subject, message, and body.");
});

for (const [name, requestText] of [
  ["normal quotes", normalRequest],
  ["smart quotes", smartQuotedRequest],
]) {
  test(`send intent opens review and bypasses Gmail tools with ${name}`, async () => {
    const calls = { draft: 0, review: 0, readSearch: 0, send: 0 };
    let submittedDraft;
    const handled = await routeEmailSendRequest(requestText, {
      prepareDraft: async (request) => {
        calls.draft += 1;
        submittedDraft = request;
        return { ...expected, draft_id: "synthetic-draft-id" };
      },
      openReview: (draft) => {
        calls.review += 1;
        assert.equal(draft.draft_id, "synthetic-draft-id");
      },
      openRecipientEntry: () => assert.fail("Recipient should have been extracted"),
      callGmailReadSearch: () => { calls.readSearch += 1; },
      callSendEmail: () => { calls.send += 1; },
    });

    assert.equal(handled, true);
    assert.equal(submittedDraft.to, expected.to);
    assert.equal(submittedDraft.subject, expected.subject);
    assert.equal(submittedDraft.body, expected.body);
    assert.deepEqual(calls, { draft: 1, review: 1, readSearch: 0, send: 0 });
  });
}
