# Connecting to Moodle

The scripts talk to Moodle with a **personal web service token**: the same
kind of token the Moodle mobile app uses. It acts as the teacher, with exactly
the teacher's permissions, so it can read and grade only the workshops of the
courses they teach.

## What the teacher needs

- A teacher (editing teacher) role in the course of the workshop.
- Python 3.9 or later. On macOS `python3` comes with the Xcode command line
  tools (`xcode-select --install` if it is missing). On Windows, install it
  from python.org and use `py` instead of `python3`.

## Getting the token

Guide the teacher through this; do not do it for them.

1. Give them the exact command, with the absolute path of this skill's
   `scripts/` folder filled in, for example:

   ```
   python3 "/Users/<them>/.claude/plugins/.../scripts/workshop.py" login
   ```

2. Ask them to run it **in their own terminal window**, not through you and
   not with the `!` prefix. The command asks for the username and password
   interactively, with the password hidden; that only works in a real
   terminal, and it keeps the password out of this conversation.

3. They type the username they use to sign in to Moodle and their password.
   The script sends them once to `https://moodle.ensinolusofona.pt/login/token.php`
   (service `moodle_mobile_app`), receives the token, checks it, and saves it
   to `~/.config/deisi-moodle-reviewer/config.json`, readable only by them.
   The password is never stored.

4. It prints "Signed in as <name>". Then run `W list` yourself to confirm.

Never ask the teacher to paste their password or the token into the chat. If
they do anyway, tell them to revoke that token (below) and run `login` again.

## If the login is refused

- **Wrong username or password**: Moodle answers "Invalid login"; try again.
- **The account signs in through an external login** (e.g. a Microsoft
  button) and has no Moodle password: `token.php` cannot work for it. The
  teacher can ask the Moodle administrators for a token for the "Moodle
  mobile web service", then run `workshop.py login --token`, which asks for
  the token hidden in the same way.
- **"Web services are not enabled"** or a similar error: the site
  configuration changed; the administrators have to re-enable the mobile
  web service.

## Revoking and renewing

The token stays valid until revoked. The teacher can see and revoke their
tokens at `https://moodle.ensinolusofona.pt/user/managetoken.php`. After
revoking, or when the scripts say the token is invalid, run `login` again.

## Another Moodle site or a temporary token

`login --url https://other.moodle` stores another address. The environment
variables `MOODLE_URL` and `MOODLE_TOKEN`, when set, take precedence over the
saved configuration.
