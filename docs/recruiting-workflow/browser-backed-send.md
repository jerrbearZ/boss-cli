# Browser-Backed Boss Reply Strategy

Date: 2026-07-09

## Decision

Use Boss APIs for reading and candidate selection, but use the official Boss Web chat app for normal message sending.

This is the chosen speed/reliability path. Rebuilding Boss's websocket message protocol directly in Python is possible, but it is slower to implement and more likely to break when Boss updates its web bundle.

## Current Send Logic

```text
boss recruiter reply-browser <friendId> <message>
  -> read candidate detail via Boss recruiter API
  -> resolve friendId, friendSource, encryptUid
  -> open Boss Web chat in a browser context
  -> wait for iBossRoot.chat and websocket connection
  -> call Boss Web's own chat send bridge
  -> poll latest-message API until the expected text appears
```

The browser bridge currently uses:

```text
iBossRoot.chat.sendMessage(message, "text", { uid, friendSource, encryptUid })
```

This keeps the in-page Boss app responsible for websocket formatting, authentication, and message delivery.

## Commands

Preview target resolution without sending:

```bash
boss recruiter reply-browser <friendId> "message" --dry-run --json
```

Send one approved message:

```bash
boss recruiter reply-browser <friendId> "message" -y --json
```

Preferred engine order:

```text
auto -> camoufox -> chrome
```

Camoufox is preferred because plain Playwright/Chrome may be detected by Boss Web and redirected to a blank page.

## Safety Rules

- Send one candidate at a time until live verification is proven stable.
- Resolve the candidate by `friendId` before browser send.
- Verify the latest message after sending.
- Keep bulk send behind an additional workflow layer with rate limits, state tracking, and approved templates.
- Stop on login prompts, captcha, websocket failure, or anti-automation page closure.

## Known Caveats

- `boss recruiter reply` is legacy and points at a fast-reply HTTP endpoint. It is not the reliable path for normal typed chat messages.
- Camoufox may need its browser runtime downloaded before first live use:

```bash
uv run python -m camoufox fetch
```

- The current implementation has passed unit tests and a real-account dry run. A live browser send still needs one explicit operator-approved trial.
