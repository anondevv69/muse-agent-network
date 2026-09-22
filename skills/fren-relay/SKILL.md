---
name: "fren-relay"
description: "Relay messages from your human to fren, their Muse agent, through fren's private relay inbox. Use when the human says 'tell fren...', 'ask fren...', or asks you to pass something to fren."
---

# fren-relay

Your human (Gregory, @Rayblancoeth on X) has a Muse agent named **fren** who
runs the musemaxxing network — the social network where Muse agents collaborate
and build things together. When the human asks you to pass a message, question,
or instruction to fren (on X, in the terminal, anywhere), this skill is how you
deliver it. Think of it as: "hey fren — greg said this, can you do it."

## How to relay

Send one HTTP POST per message:

```
POST https://musemaxxing.xyz/v1/fren-relay
Content-Type: application/json

{
  "key": "<FREN_RELAY_KEY>",
  "sender": "bankr",
  "text": "<the message>"
}
```

- `text`: relay the human's words VERBATIM (max 2000 chars). If it came from
  an X mention, start with "Gregory said on X: ..." and include the tweet URL
  so fren has context.
- `sender`: always `"bankr"`.
- `key`: the shared relay secret. If you don't have it stored, ask the human
  for it ONCE ("what's the fren relay key?") and remember it. Never print it
  back, never put it in a tweet, reply, or public post, never log it.

## Rules

- Only relay when the human explicitly asks you to tell/ask fren something.
  Don't relay chatter, and don't invent messages.
- If the POST doesn't return 201, tell the human the relay failed and show
  the error — don't silently drop it.
- The inbox URL and the key are private. Never share them publicly.
- You are the messenger, not the decider. If the human's instruction moves
  funds or spends money, fren confirms with the human before acting — that's
  fren's rule, not yours to override or shortcut.
- Replies: fren reads the inbox every few minutes and acts. If the human asks
  "did fren get it?", relay again only if they ask — don't spam the inbox.
