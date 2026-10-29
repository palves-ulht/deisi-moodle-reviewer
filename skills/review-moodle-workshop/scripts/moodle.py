"""Minimal Moodle client.

Standard library only, on purpose: this has to run anywhere with no install.

Everything goes over Moodle's REST web service with a personal token, the
same one the Moodle mobile app uses. `workshop.py login` gets it and stores it
in CONFIG; MOODLE_URL and MOODLE_TOKEN in the environment override the file.
"""
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://moodle.ensinolusofona.pt"
CONFIG = (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
          / "deisi-moodle-reviewer" / "config.json")

PHASES = {10: "setup", 20: "submission", 30: "assessment", 40: "evaluation", 50: "closed"}
PHASE_EVALUATION = 40


class MoodleError(RuntimeError):
    pass


def load_config():
    try:
        return json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return {}


def save_config(config):
    """Write the config readable by its owner only: it holds the token."""
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(config, f, indent=1)
    os.chmod(CONFIG, 0o600)


def fetch_token(base, username, password):
    """A web service token for the mobile app service, from login/token.php.
    The password is sent to Moodle once and kept nowhere."""
    body = urllib.parse.urlencode({"username": username, "password": password,
                                   "service": "moodle_mobile_app"}).encode()
    req = urllib.request.Request(f"{base.rstrip('/')}/login/token.php", data=body)
    with urllib.request.urlopen(req, timeout=60) as response:
        result = json.load(response)
    if "token" not in result:
        raise MoodleError(f"Moodle refused the login: {result.get('error') or result}")
    return result["token"]


def _flatten(prefix, value, out):
    """Encode nested lists/dicts the way Moodle's REST endpoint expects:
    data[0][name]=x&data[0][value]=y
    """
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]" if prefix else str(k), v, out)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    elif isinstance(value, bool):
        out[prefix] = "1" if value else "0"
    else:
        out[prefix] = value


class Moodle:
    def __init__(self, base=None, token=None):
        config = load_config()
        self.base = (base or os.environ.get("MOODLE_URL") or config.get("url")
                     or DEFAULT_URL).rstrip("/")
        self.token = token or os.environ.get("MOODLE_TOKEN") or config.get("token")
        if not self.token:
            raise MoodleError("no Moodle token yet: run `workshop.py login` "
                              "in your own terminal first")
        self._userid = None

    def ws(self, function, **params):
        payload = {"wstoken": self.token, "wsfunction": function,
                   "moodlewsrestformat": "json"}
        for key, value in params.items():
            _flatten(key, value, payload)
        req = urllib.request.Request(
            f"{self.base}/webservice/rest/server.php",
            data=urllib.parse.urlencode(payload).encode())
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
        if isinstance(result, dict) and "exception" in result:
            if result.get("errorcode") == "invalidtoken":
                raise MoodleError("the Moodle token is invalid or was revoked: "
                                  "run `workshop.py login` again")
            raise MoodleError(f"{function}: {result.get('errorcode')}: {result.get('message')}")
        return result

    @property
    def userid(self):
        if self._userid is None:
            self._userid = self.ws("core_webservice_get_site_info")["userid"]
        return self._userid

    def download(self, fileurl, dest):
        """Files referenced by the API need the token as a query parameter."""
        sep = "&" if "?" in fileurl else "?"
        url = fileurl + sep + urllib.parse.urlencode({"token": self.token})
        with urllib.request.urlopen(url, timeout=180) as response:
            dest.write_bytes(response.read())


# ------------------------------------------------------------------ rubric help

def rubric(form):
    """Describe the dimensions of an accumulative assessment form.

    Careful: grade__idx_N in form['fields'] is the dimension's MAXIMUM, not a
    value anyone awarded. The reviewer's own values live in form['current'].
    """
    fields = {f["name"]: f["value"] for f in form["fields"]}
    count = int(form["dimenssionscount"])  # Moodle's own spelling
    return [{
        "idx": i,
        "dimensionid": int(fields[f"dimensionid__idx_{i}"]),
        "max": float(fields[f"grade__idx_{i}"]),
        "weight": float(fields[f"weight__idx_{i}"]),
        "description": strip_html(fields.get(f"description__idx_{i}", "")),
    } for i in range(count)]


def assessment_grade(grades, dims):
    """The 0-100 grade Moodle's accumulative strategy gives these dimension
    grades: every dimension is normalised to its own maximum before weighting,
    so a 2-point dimension of weight 3 counts as much as a 6-point one.
    """
    total = sum(d["weight"] for d in dims)
    return 100 * sum(d["weight"] * g / d["max"] for g, d in zip(grades, dims)) / total


def current_grades(form):
    """The per-dimension grades and comments a reviewer actually gave, read
    from form['current'] of an assessment-mode definition. A dimension the
    reviewer never filled in is None.
    """
    current = {f["name"]: f["value"] for f in form.get("current", [])}
    count = int(form["dimenssionscount"])
    grades = []
    for i in range(count):
        value = current.get(f"grade__idx_{i}")
        grades.append(None if value in (None, "") else float(value))
    comments = [strip_html(current.get(f"peercomment__idx_{i}", ""))
                for i in range(count)]
    return grades, comments


def strip_html(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()
