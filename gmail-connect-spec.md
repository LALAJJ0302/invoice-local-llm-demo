# Connect Gmail from the dashboard: spec

Status: **implemented 2026-10-15** on branch `neo/login-gmail`, not yet committed or pushed.
Option B from the group discussion of 2026-10-15.

## Decisions taken (Neo, 2026-10-15)

1. Key location: a generated key file `.mail_secret.key` (mode 0600, gitignored), with
   `MAIL_SECRET_KEY` in the environment overriding it.
2. Who can connect and disconnect: anyone signed in. `connected_by` records who. Roles are future work.
3. The `.env` fallback stays, so the current setup keeps working unchanged.
4. Sidebar position as in section 2.
5. `email_listener.py` is Luke's. The change is `connect_to_gmail()` plus the five test lambdas that
   mock it, and goes to him as a PR with evidence. Tell him first.

## Changes made on review (JJ, 2026-10-16)

1. **TLS verification.** `imaplib.IMAP4_SSL` builds a context that verifies nothing when it is given
   none, on Python 3.12 as well as 3.13. Both IMAP logins, the listener's and the dashboard's check,
   now pass `mail_connection.imap_ssl_context()`. Passing `ssl.create_default_context()` alone was
   tried first and is not enough here: on this machine's python.org Python for macOS it refuses
   Gmail itself (`unable to get local issuer certificate`), because that build ships no CA
   certificates. The context therefore loads the bundle from `certifi`, which is now a pinned direct
   dependency. Checked against real Gmail (handshake with verification succeeds) and against hosts
   with an expired and a mismatched certificate (both rejected).
2. **The `.env` fallback is shown, not hidden.** With no saved connection the listener still logs in
   from `EMAIL_USER` and `EMAIL_PASSWORD`, so "No mailbox is being read" was untrue on any machine
   with those set. The sidebar now says "Gmail read from .env" with the address, the connect form
   says that connecting replaces it, and the Disconnect dialog says where the listener goes next.
   The listener and the dashboard share one definition of the fallback, `env_credentials()`, so
   they cannot disagree. The other option, an explicit disabled state that suppresses the
   fallback, was not built: it needs a schema change and a way to turn the `.env` mailbox off
   from the dashboard, and the honest label fixes the wrong statement in every state, including
   the one before anything was ever connected. It remains an option if the group wants Disconnect to
   mean "stop reading".

## What differs from the draft below

- `last_ok_at` was dropped. Writing it on every poll would have redrawn the page every ten seconds,
  and nothing reads it. `status` and `last_error` carry what the sidebar needs, and the triggers
  watch only those and `email`.
- The dialog and the sidebar row live in `gmail_panel.py`, not in `app.py`. Streamlit's test runner
  cannot click inside a dialog, so the body had to be drawable on its own. `app.py` keeps the
  `st.dialog` wrapper.
- An empty database stops the page before the sidebar is drawn, and a fresh install is exactly the
  one that needs to connect a mailbox. The Gmail row is therefore also drawn in the page body there.
- The App Password is not held in `st.session_state` (section 5), because the widgets are unkeyed.
  Streamlit keeps an unkeyed widget's value for the session after a failed attempt. It is never
  rendered, logged or written, which the tests assert.
- A saved password that the current key cannot decrypt is reported on the sidebar and stops the
  listener's login, rather than silently falling back to `.env` and reading a different mailbox.
- Bumps `SCHEMA_VERSION` to 19: every teammate runs `migrations/019_mail_connection.py` once after
  pulling, as for 013 to 018.

## Evidence

41 tests in `tests/test_mail_connection.py`, 4 more in `tests/test_migration.py`. They pass with
outbound network blocked. One defect they found while being written: a default argument of
`imaplib.IMAP4_SSL` is bound at import, so a patched test still reached the real Gmail with a fake
password. The factory is now chosen at call time. The dialog was also driven in a browser through
the unreachable-host error path. **Not yet done:** one connection with a real App Password.

## 1. What it does

A signed-in reviewer can connect or disconnect the mailbox the listener reads, from the
dashboard, without editing `.env` or restarting anything. They enter a Gmail address and an App
Password in a dialog. The dashboard checks the pair by logging in to Gmail over IMAP. If Gmail
accepts it, the pair is stored and the listener uses it on its next poll.

It is not "Sign in with Google". There is no OAuth in this repo and none is added here. OAuth
needs a Google Cloud project and a consent screen, neither of which is set up, and it would change
how `email_listener.py` logs in. It goes in the report as future work.

## 2. Where it sits

A status row in the sidebar, between the navigation and the signed-in user block, not next to
Log out. It is system state, so it should be visible all the time, and next to Log out it invites
a mis-click. Clicking it opens a dialog, in the same way the review dialog opens.

| State | Row says | Click opens |
|---|---|---|
| No mailbox | `Gmail: not connected` | Connect form |
| Connected | `Gmail: connected as name@gmail.com` | Status and Disconnect |
| Last poll failed to log in | `Gmail: needs attention` with the reason | Connect form, address filled in |

The Connect form has two fields (Gmail address, App Password), a short note on how to create an App
Password, and one button. The note says the account needs 2-Step Verification, and that an
organisation account may have App Passwords turned off by its admin.

## 3. How it works

1. **Check before saving.** On Connect the app opens `imaplib.IMAP4_SSL("imap.gmail.com", 993)`
   with a 10 second timeout and calls `login`. Nothing is stored unless login succeeds.
2. **Store.** One row in a new `mail_connection` table. The App Password is encrypted before it is
   written (section 4).
3. **Listener.** `connect_to_gmail()` reads the stored connection first and falls back to
   `EMAIL_USER` and `EMAIL_PASSWORD` from `.env` when there is no row. It reads on every poll, so a
   new connection applies on the next cycle with no restart. Today `.env` is read once per call
   already, so the loop shape does not change.
4. **Failure.** If login fails during polling, the listener records the reason on the row and keeps
   its existing behaviour of printing and retrying. The sidebar then shows `needs attention`.
5. **Disconnect.** Deletes the encrypted secret and the address. The listener falls back to `.env`
   if that is set, otherwise it reports "not connected" and retries.

## 4. Data

New migration `019_mail_connection.py`, additive and idempotent like 013 to 018, with the usual
`.bak` copy first. Before numbering it, check every open branch for a 019: migration 013 was
claimed twice once already.

```
mail_connection        one row at most (id = 1, CHECK id = 1)
  id                   INTEGER PRIMARY KEY CHECK (id = 1)
  email                TEXT NOT NULL
  secret_encrypted     BLOB NOT NULL      the App Password, never plaintext
  status               TEXT NOT NULL      'connected' | 'error'
  last_error           TEXT               reason from the last failed login, no secret in it
  connected_by         INTEGER            users.id, so the record says who connected it
  connected_at         TEXT NOT NULL
```

**Encryption.** Fernet from the `cryptography` package (new dependency, pinned). The key comes from
`MAIL_SECRET_KEY` in `.env`, or, if unset, a generated key file `.mail_secret.key` created with
mode 0600 and gitignored. What this protects: a copy of `workflow_platform.db` (a backup, a
screenshot, an accidental commit) no longer contains a usable password. What it does not protect:
anyone who has both the database and the key on the same machine. That limit is stated in the
report, not hidden.

## 5. Rules for the secret

- Never shown after it is saved, never written to a log, never in an error message.
- Not rendered, not logged, not written anywhere but the encrypted column.
- `last_error` stores Gmail's reason, not the credentials.
- Error text is mapped to something a person can act on: refused login (2-Step Verification off,
  wrong App Password, App Passwords disabled by admin), no network, timeout.

## 6. Files touched

| File | Change | Lane |
|---|---|---|
| `mail_connection.py`, `gmail_panel.py` (new) | encrypt, decrypt, check login, the stored row; the dialog body and sidebar row | Neo+JJ |
| `storage.py`, `migrations/019_*.py` | table and accessors | Neo+JJ |
| `app.py` | sidebar row and dialog | Neo+JJ |
| `email_listener.py` | `connect_to_gmail()` asks `mail_connection` first, about 25 lines, and its five test mocks take `db_path` | **Luke: PR with evidence, tell him first** |
| `requirements.txt`, `.env.example`, `README.md`, `.gitignore` | dependency, key variable, key file | Neo+JJ |

## 7. Out of scope

OAuth. More than one mailbox. Choosing a label or folder. Starting or stopping the listener from the
dashboard (it stays a separate process). Roles and permissions beyond "signed in" (open question 2).

## 8. Tests

- Migration: creates the table, running it twice is a no-op, `.bak` is taken.
- Encryption: round trip works, a wrong key fails without leaking, ciphertext never contains the
  plaintext.
- Check login with a fake IMAP: success stores, refusal stores nothing and maps to the right message,
  timeout maps to the network message.
- `connect_to_gmail()`: uses the stored row, falls back to `.env`, raises the existing error when
  neither exists.
- Dashboard (AppTest): each of the three sidebar states renders, and the password never appears in
  the rendered page, the session state after submit, or the log output.
- No test talks to real Gmail. One manual check with a real App Password is recorded as evidence.

## 9. Acceptance

1. With nothing connected, the sidebar says so and the dialog opens.
2. A wrong App Password is refused with a readable reason and nothing is stored.
3. A correct one connects, the row turns to `connected as ...`, and the running listener picks it up
   on its next poll without a restart.
4. Disconnect removes the secret and the listener stops using it.
5. `grep` for the App Password in the database file, the logs and the page finds nothing.

## 10. Open questions (answered at the top of this file)

1. **Key location.** `.env` variable or a generated key file? A key file is easier for the group,
   an env variable is easier to rotate.
2. **Who can connect and disconnect.** Everyone signed in, or only certain users? `auth.py` has no
   roles today. The mailbox is shared, so one reviewer can disconnect it for everyone. Recommendation
   for now: anyone signed in, with `connected_by` recorded, and roles listed as future work.
3. **Keep the `.env` fallback?** Recommendation: yes, so Luke's current setup keeps working
   unchanged.
4. **Sidebar position** between navigation and the user block, as in section 2.
5. **Luke** has to hear about the `connect_to_gmail()` change before the PR.

## 11. Size

About one working day by estimate, not measured. The check, the storage and the sidebar are small
parts. The care goes into the secret handling and the tests for it.
