"""Telegram channel tests. No network: `FakeHTTP` stands in for the httpx client."""
from __future__ import annotations

import logging
import traceback

import httpx
import pytest
from hypothesis import assume, given, settings, strategies as st

from khata.telegram import (ALLOWED_UPDATES, API_BASE, BUTTON_TITLES, COMMANDS, FILE_MAX,
                            TelegramAdapter, TelegramAPI, TelegramError, command_of,
                            owner_for_chat)

TOKEN = "123456:TEST-token_abc"


class FakeResponse:
    def __init__(self, body=None, content: bytes = b"", status_code: int = 200):
        self._body = body
        self.content = content
        self.status_code = status_code

    def json(self):
        return self._body


class FakeHTTP:
    """Records `post`/`get` calls and returns scripted responses.

    - `responses[method]`: a list of bodies (dicts) popped in order for that Bot API method;
      an unscripted method gets `{"ok": True, "result": default}` (`sendMessage` gets a
      fresh `message_id`).
    - `files[file_path]`: bytes served by `get` on the file URL (status 404 if missing).
    - `fail`: if set to an exception class, every call raises it with the full URL
      (token included) in its message, the way httpx does.
    """

    def __init__(self):
        self.calls: list[dict] = []
        self.responses: dict[str, list[dict]] = {}
        self.files: dict[str, bytes] = {}
        self.fail: type[Exception] | None = None
        self._next_mid = 100

    def script(self, method: str, result=None, ok: bool = True, description: str = ""):
        body = {"ok": ok, "result": result} if ok else {"ok": False, "description": description}
        self.responses.setdefault(method, []).append(body)

    def post(self, url, json=None, data=None, files=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        self.calls.append({"verb": "post", "url": url, "method": method, "json": json,
                           "data": data, "files": files, "timeout": timeout})
        if self.fail:
            raise self.fail(f"request to {url} failed")
        queue = self.responses.get(method)
        if queue:
            return FakeResponse(queue.pop(0))
        result = True
        if method == "sendMessage":
            self._next_mid += 1
            result = {"message_id": self._next_mid}
        return FakeResponse({"ok": True, "result": result})

    def get(self, url, timeout=None):
        self.calls.append({"verb": "get", "url": url, "timeout": timeout})
        if self.fail:
            raise self.fail(f"request to {url} failed")
        prefix = f"{API_BASE}/file/bot{TOKEN}/"
        path = url[len(prefix):] if url.startswith(prefix) else None
        if path is None or path not in self.files:
            return FakeResponse(content=b"not found", status_code=404)
        return FakeResponse(content=self.files[path])

    def posts(self, method: str) -> list[dict]:
        return [c for c in self.calls if c["verb"] == "post" and c["method"] == method]


class FakeAPI:
    """Stands in for `TelegramAPI` in adapter tests. Records every call in `calls` as
    `(method, args...)`; `send_message` returns increasing message ids from 1001.

    - `files[file_id]`: `(bytes, file_path)` served by `download` (`TelegramError` if missing).
    - `fail[method]`: an exception instance raised by that method.
    """

    def __init__(self):
        self.calls: list[tuple] = []
        self.files: dict[str, tuple[bytes, str]] = {}
        self.fail: dict[str, Exception] = {}
        self._next_mid = 1000

    def _record(self, method, *args):
        self.calls.append((method, *args))
        if method in self.fail:
            raise self.fail[method]

    def send_message(self, chat_id, text, buttons=None):
        self._record("send_message", chat_id, text, list(buttons) if buttons else None)
        self._next_mid += 1
        return self._next_mid

    def clear_buttons(self, chat_id, message_id):
        self._record("clear_buttons", chat_id, message_id)

    def answer_callback(self, callback_id):
        self._record("answer_callback", callback_id)

    def download(self, file_id):
        self._record("download", file_id)
        if file_id not in self.files:
            raise TelegramError("download: HTTP 404")
        return self.files[file_id]

    def send_document(self, chat_id, filename, data, caption=""):
        self._record("send_document", chat_id, filename, data, caption)

    def sent(self) -> list[tuple]:
        """`(chat_id, text, buttons)` for every `send_message` call."""
        return [c[1:] for c in self.calls if c[0] == "send_message"]


class RecordingBot:
    """Records `handle` calls as `(owner, text, source)` and replies with `reply` / `buttons`."""

    def __init__(self, reply: str = "ok", buttons: list[str] | None = None):
        self.calls: list[tuple[str, str, str]] = []
        self.reply, self.buttons = reply, buttons

    def handle(self, owner, text, *, source="text", today=None):
        self.calls.append((owner, text, source))
        r = {"reply": self.reply}
        if self.buttons:
            r["buttons"] = list(self.buttons)
        return r


SALT = "test-salt"
CHAT = 555_000_111


def make_adapter(bot=None, api=None, **kw):
    """A `TelegramAdapter` over fakes; returns `(adapter, bot, api)`. Build inside test bodies."""
    bot = bot if bot is not None else RecordingBot()
    api = api if api is not None else FakeAPI()
    kw.setdefault("on_image", None)
    kw.setdefault("export", None)
    kw.setdefault("salt", SALT)
    return TelegramAdapter(bot, kw.pop("voice", None), api, **kw), bot, api


def text_update(update_id: int, text: str, chat_id: int = CHAT, chat_type: str = "private"):
    return {"update_id": update_id,
            "message": {"message_id": update_id, "text": text,
                        "chat": {"id": chat_id, "type": chat_type}}}


@pytest.fixture
def http():
    return FakeHTTP()


@pytest.fixture
def api(http):
    return TelegramAPI(TOKEN, http=http)


# --- Request shapes (Requirement 1) ---------------------------------------------------

def test_method_and_file_urls(api, http):  # 1.1
    http.script("getFile", {"file_path": "voice/file_1.oga", "file_size": 3})
    http.files["voice/file_1.oga"] = b"abc"
    api.delete_webhook()
    api.download("F1")
    assert http.calls[0]["url"] == f"https://api.telegram.org/bot{TOKEN}/deleteWebhook"
    assert http.calls[1]["url"] == f"https://api.telegram.org/bot{TOKEN}/getFile"
    assert http.calls[2]["url"] == f"https://api.telegram.org/file/bot{TOKEN}/voice/file_1.oga"


def test_get_updates_params(api, http):  # 1.2
    http.script("getUpdates", [{"update_id": 7}])
    assert api.get_updates(42) == [{"update_id": 7}]
    body = http.posts("getUpdates")[0]["json"]
    assert body == {"offset": 42, "timeout": 25, "allowed_updates": ["message", "callback_query"]}
    assert ALLOWED_UPDATES == ["message", "callback_query"]
    assert http.posts("getUpdates")[0]["timeout"] > 25  # HTTP timeout outlasts the long poll


def test_send_message_inline_keyboard_row(api, http):  # 1.3, 1.4
    mid = api.send_message(5, "Save ₹50 credit for Ramesh?", ["yes", "no"])
    body = http.posts("sendMessage")[0]["json"]
    assert mid == 101
    assert body["chat_id"] == 5 and body["text"] == "Save ₹50 credit for Ramesh?"
    assert "parse_mode" not in body
    assert body["reply_markup"] == {"inline_keyboard": [[
        {"text": BUTTON_TITLES["yes"], "callback_data": "yes"},
        {"text": BUTTON_TITLES["no"], "callback_data": "no"},
    ]]}


def test_send_message_without_buttons_has_no_markup(api, http):
    api.send_message(5, "hello")
    assert "reply_markup" not in http.posts("sendMessage")[0]["json"]


def test_clear_buttons_body(api, http):  # 1.5
    api.clear_buttons(5, 101)
    call = http.posts("editMessageReplyMarkup")[0]
    assert call["json"] == {"chat_id": 5, "message_id": 101,
                            "reply_markup": {"inline_keyboard": []}}


def test_download_returns_bytes_and_path(api, http):  # 1.6
    http.script("getFile", {"file_path": "photos/file_2.jpg", "file_size": 4})
    http.files["photos/file_2.jpg"] = b"\xff\xd8ok"
    assert api.download("F2") == (b"\xff\xd8ok", "photos/file_2.jpg")
    assert http.posts("getFile")[0]["json"] == {"file_id": "F2"}


def test_download_rejects_reported_size_over_limit(api, http):  # 1.7
    http.script("getFile", {"file_path": "photos/big.jpg", "file_size": FILE_MAX + 1})
    http.files["photos/big.jpg"] = b"x"
    with pytest.raises(TelegramError, match="too large"):
        api.download("F3")
    assert not [c for c in http.calls if c["verb"] == "get"]  # never fetched


def test_download_rejects_content_over_limit(api, http):  # 1.7
    http.script("getFile", {"file_path": "photos/liar.jpg", "file_size": 10})
    http.files["photos/liar.jpg"] = b"x" * (FILE_MAX + 1)
    with pytest.raises(TelegramError, match="too large"):
        api.download("F4")


def test_download_http_error_raises(api, http):
    http.script("getFile", {"file_path": "photos/missing.jpg", "file_size": 10})
    with pytest.raises(TelegramError, match="HTTP 404"):
        api.download("F5")


def test_send_document_multipart(api, http):  # 1.8
    api.send_document(5, "khata.xlsx", b"PK\x03\x04", caption="khata.xlsx")
    call = http.posts("sendDocument")[0]
    assert call["json"] is None
    assert call["data"] == {"chat_id": "5", "caption": "khata.xlsx"}
    name, data, mime = call["files"]["document"]
    assert (name, data) == ("khata.xlsx", b"PK\x03\x04")
    assert mime.endswith("spreadsheetml.sheet")


def test_set_and_delete_webhook_bodies(api, http):  # 1.9
    api.set_webhook("https://khata.example/webhook/telegram", "s3cret")
    api.delete_webhook()
    body = http.posts("setWebhook")[0]["json"]
    assert body["url"] == "https://khata.example/webhook/telegram"
    assert body["secret_token"] == "s3cret"
    assert body["allowed_updates"] == ALLOWED_UPDATES
    assert http.posts("deleteWebhook")[0]["json"] == {}


def test_ok_false_raises_with_description(api, http):  # 1.10
    http.script("sendMessage", ok=False, description="Bad Request: chat not found")
    with pytest.raises(TelegramError) as err:
        api.send_message(5, "hi")
    assert str(err.value) == "sendMessage: Bad Request: chat not found"

# --- Properties -----------------------------------------------------------------------
# Feature: telegram-channel, Property 1: Outgoing and incoming text is capped
@settings(max_examples=150, deadline=None)
@given(text=st.one_of(st.text(max_size=200), st.text(min_size=900, max_size=1100),
                      st.text(min_size=4000, max_size=5000)))
def test_text_cap_property(text):
    """**Validates: Requirements 1.3, 4.1**"""
    # Outgoing: sendMessage carries text[:4096] and no parse_mode.
    http = FakeHTTP()  # built here: Hypothesis rejects function-scoped fixtures
    TelegramAPI(TOKEN, http=http).send_message(5, text)
    body = http.posts("sendMessage")[0]["json"]
    assert body["text"] == text[:4096]
    assert "parse_mode" not in body

    # Incoming: a private non-command text reaches Bot.handle as text[:1000].
    cmd = command_of(text)
    assume(cmd != "id" and cmd not in COMMANDS)  # commands are Property 6 / unit tests
    adapter, bot, api = make_adapter()
    adapter.handle_update(text_update(1, text))
    assert bot.calls == [(owner_for_chat(CHAT, SALT), text[:1000], "telegram")]
    assert api.sent() == [(CHAT, "ok", None)]


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


_FAILURES = [httpx.ConnectError, httpx.ReadTimeout, RuntimeError, ValueError]
_CALLS = {
    "get_updates": lambda api: api.get_updates(1),
    "send_message": lambda api: api.send_message(5, "hi", ["yes", "no"]),
    "answer_callback": lambda api: api.answer_callback("cb"),
    "clear_buttons": lambda api: api.clear_buttons(5, 1),
    "send_document": lambda api: api.send_document(5, "khata.xlsx", b"PK"),
    "set_webhook": lambda api: api.set_webhook("https://x.example/webhook/telegram", "s"),
    "delete_webhook": lambda api: api.delete_webhook(),
    "download": lambda api: api.download("F1"),
}
_METHOD = {"get_updates": "getUpdates", "send_message": "sendMessage",
           "answer_callback": "answerCallbackQuery", "clear_buttons": "editMessageReplyMarkup",
           "send_document": "sendDocument", "set_webhook": "setWebhook",
           "delete_webhook": "deleteWebhook", "download": "getFile"}


# Feature: telegram-channel, Property 2: The bot token never appears in errors or logs
@settings(max_examples=150, deadline=None)
@given(
    token=st.builds(lambda n, s: f"{n}:{s}", st.integers(10**5, 10**10),
                    st.text("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-",
                            min_size=20, max_size=40)),
    call=st.sampled_from(sorted(_CALLS)),
    mode=st.sampled_from(["raise", "raise_on_file_get", "ok_false"]),
    failure=st.sampled_from(_FAILURES),
)
def test_token_never_leaks_property(token, call, mode, failure):
    """**Validates: Requirements 1.11**"""
    http = FakeHTTP()  # built here: Hypothesis rejects function-scoped fixtures (caplog too)
    api = TelegramAPI(token, http=http)
    if mode == "raise":
        http.fail = failure
    elif mode == "raise_on_file_get":
        call = "download"
        http.script("getFile", {"file_path": "voice/f.oga", "file_size": 3})

        def get(url, timeout=None):
            raise failure(f"request to {url} failed")
        http.get = get
    else:
        http.script(_METHOD[call], ok=False,
                    description=f"Unauthorized: bot{token} at {API_BASE}/bot{token}/x")

    handler = _ListHandler()
    logger = logging.getLogger("khata.telegram")
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        with pytest.raises(TelegramError) as err:
            _CALLS[call](api)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    assert handler.lines  # the failure was logged
    rendered = "".join(traceback.format_exception(err.value))
    assert token not in str(err.value)
    assert token not in rendered
    assert token not in "\n".join(handler.lines)


# --- Pure helpers (task 3.1) ---

from khata.telegram import command_of, owner_for_chat, parse_allowed, pick_photo


def test_owner_for_chat_format_and_errors():  # 10.1, 10.2
    o = owner_for_chat(987654321, "salt")
    assert o == owner_for_chat("987654321", "salt")
    assert o.startswith("tg-") and len(o) == 27 and "987654321" not in o
    assert o != owner_for_chat(987654321, "other")
    for bad in ("12.5", "abc", "", 1.0, True, None):
        with pytest.raises(ValueError):
            owner_for_chat(bad, "salt")


def test_parse_allowed():  # 10.5
    assert parse_allowed(None) == frozenset() == parse_allowed("  ")
    assert parse_allowed("123, -456,") == {123, -456}
    with pytest.raises(ValueError):
        parse_allowed("123, abc")


def test_command_of():  # 4.2, 4.5
    assert command_of("/Export@KhataBot extra") == "export"
    assert command_of("/list") == "list"
    assert command_of("hello /list") is None
    assert command_of("/") == ""


def test_pick_photo():  # 8.1, 8.3
    sizes = [{"file_id": "s", "width": 90, "height": 90, "file_size": 100},
             {"file_id": "m", "width": 800, "height": 600, "file_size": 5000},
             {"file_id": "l", "width": 1280, "height": 960, "file_size": 9000}]
    assert pick_photo(sizes, 10_000)["file_id"] == "l"
    assert pick_photo(sizes, 6000)["file_id"] == "m"
    assert pick_photo(sizes, 50) is None
    assert pick_photo([{"file_id": "x", "width": 1, "height": 1}], 0)["file_id"] == "x"


import re

# Realistic chat ids (|id| >= 6 digits): a short id like 7 can appear in a hex digest by chance.
_CHAT_ID = st.one_of(st.integers(10**5, 10**13), st.integers(-(10**13), -(10**5)))
_SALT = st.text(min_size=1, max_size=40)


# Feature: telegram-channel, Property 12: Owner id format and privacy
@settings(max_examples=200, deadline=None)
@given(cid=_CHAT_ID, other=_CHAT_ID, salt=_SALT, salt2=_SALT)
def test_owner_for_chat_property(cid, other, salt, salt2):
    """**Validates: Requirements 10.1, 10.2**"""
    o = owner_for_chat(cid, salt)
    assert o == owner_for_chat(cid, salt) == owner_for_chat(str(cid), salt)
    assert re.fullmatch(r"tg-[0-9a-f]{24}", o)
    assert str(cid) not in o and str(abs(cid)) not in o
    if other != cid:
        assert owner_for_chat(other, salt) != o
    if salt2 != salt:
        assert owner_for_chat(cid, salt2) != o
from khata.telegram import COMMANDS


@st.composite
def _cased(draw, word):
    flips = draw(st.lists(st.booleans(), min_size=len(word), max_size=len(word)))
    return "".join(c.upper() if f else c for c, f in zip(word, flips))


# Feature: telegram-channel, Property 6: Command suffix normalization
@settings(max_examples=200, deadline=None)
@given(data=st.data(),
       cmd=st.sampled_from(sorted(set(COMMANDS) | {"id"})),
       name=st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_",
                    min_size=1, max_size=32),
       args=st.one_of(st.just(""),
                      st.tuples(st.sampled_from([" ", "  ", "\t", "\n"]), st.text(max_size=40))
                      .map("".join)))
def test_command_suffix_normalization_property(data, cmd, name, args):
    """**Validates: Requirements 4.2, 4.5**"""
    typed = data.draw(_cased(cmd))
    plain = command_of("/" + typed)
    assert plain == cmd
    assert command_of("/" + typed + "@" + name + args) == plain
    assert command_of("/" + typed + args) == plain
_PHOTO_SIZE = st.fixed_dictionaries(
    {"file_id": st.text(min_size=1, max_size=8),
     "width": st.integers(1, 5000), "height": st.integers(1, 5000)},
    optional={"file_size": st.integers(0, 20 * 1024 * 1024)})
# Feature: telegram-channel, Property 10: Photo size selection
@settings(max_examples=200, deadline=None)
@given(sizes=st.lists(_PHOTO_SIZE, min_size=1, max_size=6), limit=st.integers(0, 20 * 1024 * 1024))
def test_pick_photo_property(sizes, limit):
    """**Validates: Requirements 8.1, 8.3**"""
    fits = [s for s in sizes if s.get("file_size", 0) <= limit]
    got = pick_photo(sizes, limit)
    if not fits:
        assert got is None
        return
    assert got is not None and any(got is s for s in sizes)
    assert got.get("file_size", 0) <= limit
    assert got["width"] * got["height"] == max(s["width"] * s["height"] for s in fits)


# Feature: telegram-channel, Property 13: Allow-list gate
@settings(max_examples=200, deadline=None)
@given(allowed=st.frozensets(_CHAT_ID, max_size=5), data=st.data())
def test_allow_list_gate_property(allowed, data):
    """**Validates: Requirements 10.5**"""
    pick = [st.sampled_from(sorted(allowed))] if allowed else []
    cid = data.draw(st.one_of(_CHAT_ID, *pick))
    adapter, bot, api = make_adapter(allowed=allowed)
    adapter.handle_update(text_update(1, "ramesh 50 udhaar", chat_id=cid))
    if not allowed or cid in allowed:
        assert bot.calls == [(owner_for_chat(cid, SALT), "ramesh 50 udhaar", "telegram")]
        assert api.sent() == [(cid, "ok", None)]
    else:
        assert bot.calls == []
        assert api.calls == []  # no reply, no button clear, nothing


from khata.telegram import RATE_PER_MIN


# Gaps between messages: mostly bursts (0 s or under a second) so the limit is hit, with
# occasional long pauses that let the 60 s window slide past earlier messages.
_GAP = st.one_of(st.just(0.0), st.floats(0, 1), st.floats(0, 5),
                 st.sampled_from([59.0, 60.0, 61.0]), st.floats(0, 90))


# Feature: telegram-channel, Property 14: Rate limit
@settings(max_examples=150, deadline=None)
@given(gaps=st.lists(_GAP, min_size=1, max_size=100), start=st.floats(0, 10**6))
def test_rate_limit_property(gaps, start):
    """**Validates: Requirements 10.6**"""
    times, t = [], start
    for g in gaps:
        t += g
        times.append(t)
    ticks = list(times)  # list-backed clock: each call returns the next timestamp

    def clock():
        return ticks.pop(0)

    adapter, bot, api = make_adapter(clock=clock)
    for i in range(len(times)):
        adapter.handle_update(text_update(i + 1, f"msg {i}"))
    assert ticks == []  # one clock read per message

    admitted = [int(c[1].split()[1]) for c in bot.calls]
    assert len(api.sent()) == len(admitted)  # a dropped message gets no reply
    at = [times[i] for i in admitted]
    # At most 30 messages reach the Bot within any 60-second window.
    for j, s in enumerate(at):
        assert sum(1 for x in at[j:] if x - s < 60) <= RATE_PER_MIN
    # A message is dropped only when 30 already reached the Bot in the preceding 60 seconds.
    for i, ti in enumerate(times):
        prior = sum(1 for k in admitted if k < i and ti - times[k] < 60)
        assert (i in admitted) == (prior < RATE_PER_MIN)
from khata.flow import Bot
from khata.ledger import Ledger
_DUP_TEXTS = ["Ramesh ko 500 udhaar", "Sita ne 200 diye", "yes", "no", "600", "credit",
              "payment", "list", "hello", "/help", "/id", "500 udhaar", "Mohan"]
_DUP_KIND = st.one_of(
    st.tuples(st.just("text"), st.sampled_from(_DUP_TEXTS)),
    st.tuples(st.just("callback"), st.sampled_from(["yes", "no", "credit", "payment"])),
)
def _dup_update(uid: int, kind: tuple[str, str], chat_id: int) -> dict:
    if kind[0] == "text":
        return text_update(uid, kind[1], chat_id=chat_id)
    return {"update_id": uid,
            "callback_query": {"id": f"cb{uid}", "data": kind[1],
                               "message": {"message_id": 1001 + uid % 7,
                                           "chat": {"id": chat_id, "type": "private"}}}}
# Feature: telegram-channel, Property 15: Duplicate updates are idempotent
@settings(max_examples=100, deadline=None)
@given(kinds=st.lists(st.tuples(_DUP_KIND, st.sampled_from([CHAT, CHAT + 1])),
                      min_size=1, max_size=15),
       repeats=st.lists(st.tuples(st.integers(0, 10**6), st.integers(0, 10**6)), max_size=15),
       base=st.integers(1, 10**9))
def test_duplicate_updates_property(kinds, repeats, base):
    """**Validates: Requirements 10.7**"""
    updates = [_dup_update(base + i, k, cid) for i, (k, cid) in enumerate(kinds)]
    # With repeats: after delivering update i, re-deliver an already-seen update (copied, so
    # the adapter cannot rely on object identity).
    extra: dict[int, list[int]] = {}
    for pos, pick in repeats:
        i = pos % len(updates)
        extra.setdefault(i, []).append(pick % (i + 1))
    noisy = []
    for i, u in enumerate(updates):
        noisy.append(u)
        noisy.extend({**updates[j]} for j in extra.get(i, []))
    def deliver(stream):
        lg = Ledger()  # real Bot on an in-memory Ledger, fresh per run
        adapter, _, api = make_adapter(bot=Bot(lg))
        for u in stream:
            adapter.handle_update(u)
        owners = [owner_for_chat(c, SALT) for c in (CHAT, CHAT + 1)]
        return api.calls, [lg.entries(o, include_cancelled=True) for o in owners]
    once_calls, once_ledger = deliver(updates)
    noisy_calls, noisy_ledger = deliver(noisy)
    assert noisy_calls == once_calls
    assert noisy_ledger == once_ledger


# --- Messages and commands (task 3.10) ------------------------------------------------

from khata.bills import BillReader, intro, to_extraction
from khata.guard import PaidGuard
from khata.telegram import ERROR_REPLY, PRIVATE_ONLY, TOO_LARGE, UNSUPPORTED
from khata.voice import VoicePipeline

_JPG = b"\xff\xd8\xff\xe0" + b"\0" * 64
_OWNER = owner_for_chat(CHAT, SALT)


def _msg_update(update_id: int, **fields):
    return {"update_id": update_id,
            "message": {"message_id": update_id, "chat": {"id": CHAT, "type": "private"},
                        **fields}}


class _SuffixSTT:
    """Transcriber that records the audio file suffix it was given."""

    def __init__(self, text="Ramesh ko 500 udhaar"):
        self.text, self.suffixes = text, []

    def transcribe(self, path):
        self.suffixes.append(path.suffix)
        return self.text


class _Vision:
    def __init__(self, out):
        self.out = out

    def read(self, image, mime):
        return self.out


def test_start_and_help_reply_with_bot_help():  # 4.3
    help_text = Bot(Ledger()).handle(_OWNER, "help", source="telegram")["reply"]
    for i, cmd in enumerate(["/start", "/help"], 1):
        adapter, _, api = make_adapter(bot=Bot(Ledger()))
        adapter.handle_update(text_update(i, cmd))
        assert api.sent() == [(CHAT, help_text, None)]


def test_id_replies_with_chat_id_without_calling_bot():  # 4.4
    adapter, bot, api = make_adapter()
    adapter.handle_update(text_update(1, "/id"))
    assert bot.calls == []
    [(chat, text, buttons)] = api.sent()
    assert chat == CHAT and str(CHAT) in text and buttons is None


def test_command_with_bot_suffix_reaches_bot_plain():  # 4.2, 4.5
    adapter, bot, _ = make_adapter()
    adapter.handle_update(text_update(1, "/List@KhataBot"))
    assert bot.calls == [(_OWNER, "list", "telegram")]


def test_bot_exception_gives_error_reply_and_type_only_log(caplog):  # 4.6
    class Boom:
        def handle(self, owner, text, *, source="text", today=None):
            raise RuntimeError("secret detail ramesh 500")

    adapter, _, api = make_adapter(bot=Boom())
    with caplog.at_level(logging.WARNING, logger="khata.telegram"):
        adapter.handle_update(text_update(1, "ramesh 500 udhaar"))
    assert api.sent() == [(CHAT, ERROR_REPLY, None)]
    assert "RuntimeError" in caplog.text and "secret detail" not in caplog.text


def test_group_chat_refused_and_bot_not_called():  # 10.4
    adapter, bot, api = make_adapter()
    adapter.handle_update(text_update(1, "ramesh 500 udhaar", chat_type="group"))
    assert bot.calls == []
    assert api.sent() == [(CHAT, PRIVATE_ONLY, None)]


def test_voice_heard_then_confirm_nothing_saved():  # 7.1, 7.2
    lg = Ledger()
    stt = _SuffixSTT()
    voice = VoicePipeline(PaidGuard(key="k", budget_calls=9, killed=False), stt)
    api = FakeAPI()
    api.files["V1"] = (b"OggS" + b"\0" * 32, "voice/file_1.oga")
    adapter, _, _ = make_adapter(bot=Bot(lg), api=api, voice=voice)
    adapter.handle_update(_msg_update(1, voice={"file_id": "V1", "duration": 2}))
    assert stt.suffixes == [".ogg"]       # Telegram's .oga is renamed: Groq rejects that extension
    heard, confirm = api.sent()
    assert heard == (CHAT, "🎙️ Heard: “Ramesh ko 500 udhaar”", None)
    assert confirm[2] == ["yes", "no"]
    assert lg.entries(_OWNER, include_cancelled=True) == []


def test_voice_demo_mode_reply():  # 7.3, 7.4
    stt = _SuffixSTT()
    voice = VoicePipeline(PaidGuard(key="", budget_calls=9, killed=False), stt)
    api = FakeAPI()
    api.files["V1"] = (b"OggS" + b"\0" * 32, "voice/file_1.oga")
    adapter, bot, _ = make_adapter(api=api, voice=voice)
    adapter.handle_update(_msg_update(1, voice={"file_id": "V1"}))
    assert stt.suffixes == [] and bot.calls == []
    [(chat, text, buttons)] = api.sent()
    assert "demo mode" in text and buttons is None


def _image_adapter(limit=10_000):
    calls = []

    def on_image(owner, data, mime):
        calls.append((owner, data, mime))
        return {"reply": "bill ok", "buttons": []}

    api = FakeAPI()
    adapter, _, _ = make_adapter(api=api, on_image=on_image, image_limit=limit)
    return adapter, api, calls


def test_photo_uses_largest_allowed_size():  # 8.1
    adapter, api, calls = _image_adapter(limit=6000)
    api.files["m"] = (_JPG, "photos/m.jpg")
    adapter.handle_update(_msg_update(1, photo=[
        {"file_id": "s", "width": 90, "height": 90, "file_size": 100},
        {"file_id": "m", "width": 800, "height": 600, "file_size": 5000},
        {"file_id": "l", "width": 1280, "height": 960, "file_size": 9000}]))
    assert ("download", "m") in api.calls and ("download", "l") not in api.calls
    assert calls == [(_OWNER, _JPG, "image/jpeg")]
    assert api.sent() == [(CHAT, "bill ok", None)]


def test_image_document_passes_its_mime():  # 8.2
    adapter, api, calls = _image_adapter()
    api.files["D1"] = (b"\x89PNG", "documents/bill.png")
    adapter.handle_update(_msg_update(1, document={"file_id": "D1", "mime_type": "image/png",
                                                   "file_size": 4}))
    assert calls == [(_OWNER, b"\x89PNG", "image/png")]


def test_every_photo_size_too_large():  # 8.3
    adapter, api, calls = _image_adapter(limit=50)
    adapter.handle_update(_msg_update(1, photo=[
        {"file_id": "s", "width": 90, "height": 90, "file_size": 100}]))
    assert calls == [] and not [c for c in api.calls if c[0] == "download"]
    assert api.sent() == [(CHAT, TOO_LARGE, None)]


def test_bill_photo_asks_customer_then_kind():  # 8.4
    lg = Ledger()
    bot = Bot(lg)
    reader = BillReader(PaidGuard(key="k", budget_calls=9, killed=False),
                        _Vision({"vendor": "Shop", "total_rupees": 846.5,
                                 "customer": "Mallory", "kind": "credit"}))

    def on_image(owner, data, mime):
        f = reader.read(data, mime)
        r = bot.start_from_extraction(owner, to_extraction(f), "bill")
        return {**r, "reply": intro(f) + "\n" + r["reply"]}

    api = FakeAPI()
    api.files["p"] = (_JPG, "photos/p.jpg")
    adapter, _, _ = make_adapter(bot=bot, api=api, on_image=on_image)
    adapter.handle_update(_msg_update(1, photo=[
        {"file_id": "p", "width": 800, "height": 600, "file_size": len(_JPG)}]))
    reply = api.sent()[-1][1]
    assert "₹846.50" in reply and "Whose bill was this" in reply
    adapter.handle_update(text_update(2, "Ramesh"))
    assert api.sent()[-1][2] == ["credit", "payment", "?"]  # credit/payment is asked, not taken from the bill
    assert lg.entries(_OWNER, include_cancelled=True) == []


def test_sticker_unsupported():  # 8.5
    adapter, bot, api = make_adapter()
    adapter.handle_update(_msg_update(1, sticker={"file_id": "S1"}))
    assert bot.calls == []
    assert api.sent() == [(CHAT, UNSUPPORTED, None)]


# --- Confirm step (task 4.2) -----------------------------------------------------------
_P7_TEXTS = ["Ramesh ko 500 udhaar", "Sita ne 200 diye", "500 udhaar", "Mohan ko 300", "600",
             "Mohan", "credit", "payment", "list", "hello", "/help", "yes", "haan", "ok", "no"]
_P7_TEXT_YES = {"yes", "haan", "ok"}  # members of flow.YES in _P7_TEXTS
_P7_STEP = st.one_of(
    st.tuples(st.just("text"), st.sampled_from(_P7_TEXTS)),
    st.tuples(st.just("text"), st.sampled_from(_P7_TEXTS[:4])),  # extra weight on entries
    st.tuples(st.just("tap"), st.sampled_from(["yes", "no", "credit", "payment"]),
              st.sampled_from(["live", "live", "stale", "random"]), st.integers(0, 10**6)),
    st.tuples(st.just("dup"), st.integers(0, 10**6)),
)


# Feature: telegram-channel, Property 7: Nothing is saved without an accepted confirmation
@settings(max_examples=150, deadline=None)
@given(steps=st.lists(_P7_STEP, min_size=1, max_size=30))
def test_nothing_saved_without_accepted_confirmation_property(steps):
    """**Validates: Requirements 5.1, 5.2, 5.3, 5.4**"""
    lg = Ledger()
    bot = Bot(lg)
    ticks = iter(range(0, 10**9, 61))  # each update a minute apart: the rate limit never drops one
    adapter, _, api = make_adapter(bot=bot, clock=lambda: next(ticks))
    owner = owner_for_chat(CHAT, SALT)
    delivered: list[dict] = []
    accepted = 0
    for i, step in enumerate(steps):
        live = adapter.keyboards.get(CHAT)
        if step[0] == "dup" and delivered:
            u = {**delivered[step[1] % len(delivered)]}  # already seen: must be ignored
            is_yes = False
        elif step[0] == "tap":
            _, data, which, n = step
            mids = [m for m in _sent_mids(api) if m != live]
            if which == "live" and live is not None:
                mid = live
            elif which == "stale" and mids:
                mid = mids[n % len(mids)]
            else:
                mid = n
            u = {"update_id": i + 1,
                 "callback_query": {"id": f"cb{i}", "data": data,
                                    "message": {"message_id": mid,
                                                "chat": {"id": CHAT, "type": "private"}}}}
            is_yes = data == "yes" and live is not None and mid == live
        else:
            text = step[1] if step[0] == "text" else "hello"
            u = text_update(i + 1, text)
            is_yes = text in _P7_TEXT_YES
        p = bot.pending.get(owner)
        at_confirm = p is not None and p.awaiting == "confirm"
        before = len(lg.entries(owner))
        adapter.handle_update(u)
        delivered.append(u)
        if is_yes and at_confirm:
            accepted += 1
        assert len(lg.entries(owner)) - before == (1 if is_yes and at_confirm else 0), (step, u)
    assert len(lg.entries(owner)) == accepted
    assert len(lg.entries(owner, include_cancelled=True)) == accepted


def _sent_mids(api: FakeAPI):
    """Message ids `FakeAPI.send_message` handed out so far (it counts up from 1001)."""
    n = sum(1 for c in api.calls if c[0] == "send_message")
    return range(1001, 1001 + n)


# --- Keyboard rule (task 4.3) -----------------------------------------------------------
class _ScriptBot:
    """Replies with whatever buttons the test set in `next_buttons` (None: plain reply)."""

    def __init__(self):
        self.next_buttons: list[str] | None = None

    def handle(self, owner, text, *, source="text", today=None):
        r = {"reply": f"re: {text}"}
        if self.next_buttons:
            r["buttons"] = list(self.next_buttons)
        return r


_P8_CHATS = [CHAT, CHAT + 1]
_P8_GROUP = -100_200_300
_P8_BUTTONS = st.one_of(st.none(), st.just([]), st.sampled_from(
    [["yes", "no"], ["credit", "payment"], ["yes"]]))
_P8_STEP = st.one_of(
    st.tuples(st.just("text"), st.sampled_from(_P8_CHATS), _P8_BUTTONS),
    st.tuples(st.just("tap"), st.sampled_from(_P8_CHATS), _P8_BUTTONS,
              st.sampled_from(["live", "live", "stale", "random"]), st.integers(0, 10**6)),
    st.tuples(st.just("notice"), st.sampled_from(_P8_CHATS + [_P8_GROUP]),
              st.sampled_from(["/id", "sticker", "group"])),
    st.tuples(st.just("dup"), st.integers(0, 10**6)),
)


def _live_keyboards(api: FakeAPI) -> dict[int, set[int]]:
    """Per chat, ids of sent messages with buttons that no `clear_buttons` call has cleared.

    Replays `api.calls` in order; `send_message` ids count up from 1001 (see `FakeAPI`).
    """
    live: dict[int, set[int]] = {}
    mid = 1000
    for c in api.calls:
        if c[0] == "send_message":
            mid += 1
            if c[3]:
                live.setdefault(c[1], set()).add(mid)
        elif c[0] == "clear_buttons":
            live.get(c[1], set()).discard(c[2])
    return live


# Feature: telegram-channel, Property 8: At most one live keyboard per chat
@settings(max_examples=150, deadline=None)
@given(steps=st.lists(_P8_STEP, min_size=1, max_size=30))
def test_at_most_one_live_keyboard_property(steps):
    """**Validates: Requirements 6.1, 6.2, 6.3**"""
    bot = _ScriptBot()
    ticks = iter(range(0, 10**9, 61))  # a minute apart: the rate limit never drops an update
    adapter, _, api = make_adapter(bot=bot, clock=lambda: next(ticks))
    delivered: list[dict] = []
    for i, step in enumerate(steps):
        uid = i + 1
        if step[0] == "dup" and delivered:
            u = {**delivered[step[1] % len(delivered)]}  # already seen: ignored
        elif step[0] == "text":
            _, cid, bot.next_buttons = step
            u = text_update(uid, f"entry {i}", chat_id=cid)
        elif step[0] == "tap":
            _, cid, bot.next_buttons, which, n = step
            live = adapter.keyboards.get(cid)
            others = [m for m in _sent_mids(api) if m != live]
            if which == "live" and live is not None:
                mid = live
            elif which == "stale" and others:
                mid = others[n % len(others)]
            else:
                mid = n
            u = {"update_id": uid,
                 "callback_query": {"id": f"cb{i}", "data": "yes",
                                    "message": {"message_id": mid,
                                                "chat": {"id": cid, "type": "private"}}}}
        elif step[0] == "notice":
            _, cid, what = step
            if what == "/id":
                u = text_update(uid, "/id", chat_id=cid)
            elif what == "sticker":
                u = {"update_id": uid, "message": {"message_id": uid, "sticker": {"file_id": "S"},
                                                   "chat": {"id": cid, "type": "private"}}}
            else:
                u = text_update(uid, "hi", chat_id=_P8_GROUP, chat_type="group")
        else:
            u = text_update(uid, "hello", chat_id=CHAT)
        adapter.handle_update(u)
        delivered.append(u)

        live = _live_keyboards(api)
        for cid in _P8_CHATS + [_P8_GROUP]:
            ids = live.get(cid, set())
            assert len(ids) <= 1, (cid, ids, step)
            assert adapter.keyboards.get(cid) == (next(iter(ids)) if ids else None), (cid, step)
            sends = [c for c in api.calls if c[0] == "send_message" and c[1] == cid]
            if sends and not sends[-1][3]:
                assert cid not in adapter.keyboards  # last reply had no buttons (6.3)


# --- Stale callbacks (task 4.4) ---------------------------------------------------------
import copy

_P9_TEXTS = ["Ramesh ko 500 udhaar", "Sita ne 200 diye", "500 udhaar", "Mohan ko 300", "600",
             "Mohan", "credit", "yes", "no", "list", "hello"]
_P9_STEP = st.one_of(
    st.tuples(st.just("text"), st.sampled_from(_P9_TEXTS)),
    st.tuples(st.just("tap"), st.sampled_from(["yes", "no", "credit", "payment"]), st.booleans()),
)


class _CountingBot(Bot):
    """A real `Bot` that counts `handle` calls."""
    handled = 0  # `+=` below makes it a per-instance count

    def handle(self, owner, text, **kw):
        self.handled += 1
        return super().handle(owner, text, **kw)


# Feature: telegram-channel, Property 9: Stale callbacks have no effect
@settings(max_examples=150, deadline=None)
@given(prefix=st.lists(_P9_STEP, max_size=12),
       data=st.sampled_from(["yes", "no", "credit", "payment"]),
       which=st.sampled_from(["old", "random"]), n=st.integers(0, 10**6))
def test_stale_callbacks_have_no_effect_property(prefix, data, which, n):
    """**Validates: Requirements 6.4, 5.4**"""
    lg = Ledger()
    bot = _CountingBot(lg)
    ticks = iter(range(0, 10**9, 61))  # a minute apart: the rate limit never drops an update
    adapter, _, api = make_adapter(bot=bot, clock=lambda: next(ticks))
    owner = owner_for_chat(CHAT, SALT)
    uid = 0

    def tap(mid, d):
        nonlocal uid
        uid += 1
        adapter.handle_update({"update_id": uid, "callback_query": {
            "id": f"cb{uid}", "data": d,
            "message": {"message_id": mid, "chat": {"id": CHAT, "type": "private"}}}})

    for step in prefix:  # build an arbitrary adapter and Bot state
        if step[0] == "text":
            uid += 1
            adapter.handle_update(text_update(uid, step[1]))
        elif step[2] and adapter.keyboards.get(CHAT) is not None:
            tap(adapter.keyboards[CHAT], step[1])  # a live tap moves the state on

    live = adapter.keyboards.get(CHAT)
    old = [m for m in _sent_mids(api) if m != live]
    mid = old[n % len(old)] if which == "old" and old else n
    assume(mid != live)

    p = bot.pending.get(owner)
    asking_customer = p is not None and p.awaiting == "customer"
    pending_before = copy.deepcopy(p)
    entries_before = lg.entries(owner, include_cancelled=True)
    keyboards_before = dict(adapter.keyboards)
    handled_before, calls_before = bot.handled, len(api.calls)

    tap(mid, data)

    assert bot.handled == handled_before                       # Bot not called
    assert api.calls[calls_before:] == [("answer_callback", f"cb{uid}"),
                                        ("clear_buttons", CHAT, mid)]
    assert bot.pending.get(owner) == pending_before            # pending entry unchanged
    assert lg.entries(owner, include_cancelled=True) == entries_before
    assert adapter.keyboards == keyboards_before
    if asking_customer and data == "yes":
        assert "Yes" not in lg.balances(owner) and "yes" not in lg.balances(owner)
        assert bot.pending[owner].awaiting == "customer"


# --- Chat isolation (task 4.5) ----------------------------------------------------------
_P16_TEXTS = ["Ramesh ko 500 udhaar", "Sita ne 200 diye", "500 udhaar", "Mohan ko 300", "600",
              "Mohan", "credit", "payment", "yes", "no", "list", "hello"]
_P16_STEP = st.one_of(
    st.tuples(st.just("text"), st.integers(0, 1), st.sampled_from(_P16_TEXTS)),
    st.tuples(st.just("text"), st.integers(0, 1), st.sampled_from(_P16_TEXTS[:4])),  # entries
    # where: this chat's live keyboard, the other chat's live keyboard, or an unknown id
    st.tuples(st.just("tap"), st.integers(0, 1), st.sampled_from(["yes", "no", "credit", "payment"]),
              st.sampled_from(["own", "own", "other", "unknown"])),
)
_P16_UNKNOWN_MID = 10**8  # never handed out by FakeAPI, which counts up from 1001


class _Chat:
    """One adapter over a real `Bot(Ledger())`, with its own fast clock and update ids."""

    def __init__(self):
        self.ledger = Ledger()
        self.bot = Bot(self.ledger)
        ticks = iter(range(0, 10**9, 61))  # a minute apart: the rate limit never drops one
        self.adapter, _, self.api = make_adapter(bot=self.bot, clock=lambda: next(ticks))
        self.uid = 0

    def text(self, cid, text):
        self.uid += 1
        self.adapter.handle_update(text_update(self.uid, text, chat_id=cid))

    def tap(self, cid, data, mid):
        self.uid += 1
        self.adapter.handle_update({"update_id": self.uid, "callback_query": {
            "id": f"cb{self.uid}", "data": data,
            "message": {"message_id": mid, "chat": {"id": cid, "type": "private"}}}})

    def state(self, cid):
        """What the chat can observe: its entries (ids aside), pending entry and replies."""
        owner = owner_for_chat(cid, SALT)
        entries = [(e.customer, e.kind, e.paise, e.note, e.entry_date, e.due_date, e.status)
                   for e in self.ledger.entries(owner, include_cancelled=True)]
        replies = [(t, b) for c, t, b in self.api.sent() if c == cid]
        return entries, copy.deepcopy(self.bot.pending.get(owner)), replies, \
            cid in self.adapter.keyboards


# Feature: telegram-channel, Property 16: Chats are isolated
@settings(max_examples=100, deadline=None)
@given(steps=st.lists(_P16_STEP, min_size=1, max_size=30),
       cids=st.lists(st.integers(1, 10**12), min_size=2, max_size=2, unique=True))
def test_chats_are_isolated_property(steps, cids):
    """**Validates: Requirements 10.8**"""
    both = _Chat()                       # both chats interleaved on one adapter and Bot
    alone = {cid: _Chat() for cid in cids}  # each chat on its own
    for step in steps:
        cid, other = cids[step[1]], cids[1 - step[1]]
        other_before = both.state(other)
        other_keyboard = both.adapter.keyboards.get(other)
        calls_before = len(both.api.calls)
        if step[0] == "text":
            both.text(cid, step[2])
            alone[cid].text(cid, step[2])
        else:
            _, _, data, where = step
            if where == "own":
                both_mid = both.adapter.keyboards.get(cid, _P16_UNKNOWN_MID)
                alone_mid = alone[cid].adapter.keyboards.get(cid, _P16_UNKNOWN_MID)
            elif where == "other":  # the other chat's live keyboard: stale for this chat
                both_mid = both.adapter.keyboards.get(other, _P16_UNKNOWN_MID)
                alone_mid = _P16_UNKNOWN_MID
            else:
                both_mid = alone_mid = _P16_UNKNOWN_MID
            both.tap(cid, data, both_mid)
            alone[cid].tap(cid, data, alone_mid)
        # one chat's update never touches the other chat's entries, pending entry or keyboard
        assert both.state(other) == other_before, step
        assert both.adapter.keyboards.get(other) == other_keyboard, step
        assert all(c[1] == cid for c in both.api.calls[calls_before:] if c[0] != "answer_callback")
        # and the chat is exactly where it would be had it run alone
        assert both.state(cid) == alone[cid].state(cid), step


# --- Stale-button scenarios (task 4.6) --------------------------------------------------
import threading
import time


class _Stale:
    """A real `Bot(Ledger())` behind an adapter, with update ids and a fast clock."""

    def __init__(self, bot_cls=Bot):
        self.ledger = Ledger()
        self.bot = bot_cls(self.ledger)
        ticks = iter(range(0, 10**9, 61))  # a minute apart: the rate limit never drops one
        self.adapter, _, self.api = make_adapter(bot=self.bot, clock=lambda: next(ticks))
        self.uid = 0

    def text(self, text):
        self.uid += 1
        self.adapter.handle_update(text_update(self.uid, text))

    def tap(self, data, mid):
        self.uid += 1
        self.adapter.handle_update({"update_id": self.uid, "callback_query": {
            "id": f"cb{self.uid}", "data": data,
            "message": {"message_id": mid, "chat": {"id": CHAT, "type": "private"}}}})

    def saved(self):
        return [(e.customer, e.paise) for e in self.ledger.entries(_OWNER, include_cancelled=True)]


def test_text_gives_buttons_and_live_yes_saves_exactly_once():  # 5.1, 5.2, 5.4
    s = _Stale()
    s.text("Ramesh 500 credit")
    assert s.api.sent()[-1][2] == ["yes", "no"] and s.saved() == []      # 5.1
    mid = s.adapter.keyboards[CHAT]
    s.tap("yes", mid)
    assert s.saved() == [("Ramesh", 50000)]                                # 5.2
    assert ("clear_buttons", CHAT, mid) in s.api.calls and CHAT not in s.adapter.keyboards
    s.tap("yes", mid)                                                      # same Yes again
    assert s.saved() == [("Ramesh", 50000)]                                # 5.4


def test_old_yes_cannot_become_customer_name():  # 6.4
    s = _Stale()
    s.text("Ramesh 500 credit")
    old = s.adapter.keyboards[CHAT]
    s.text("list")                     # unrelated: discards Ramesh, clears the old keyboard
    s.text("500 credit")
    assert s.api.sent()[-1][1] == "Who is the customer?"
    assert ("clear_buttons", CHAT, old) in s.api.calls
    handled = len(s.api.sent())
    s.tap("yes", old)
    assert len(s.api.sent()) == handled                                    # Bot not called
    assert s.api.calls[-1] == ("clear_buttons", CHAT, old)
    assert s.saved() == []
    assert "Yes" not in s.ledger.balances(_OWNER) and "yes" not in s.ledger.balances(_OWNER)
    p = s.bot.pending[_OWNER]
    assert p.awaiting == "customer" and p.ex.customer is None and p.ex.paise == 50000


def test_old_yes_cannot_confirm_a_different_entry():  # 6.1, 6.4
    s = _Stale()
    s.text("Ramesh 500 credit")
    a = s.adapter.keyboards[CHAT]
    s.text("Mohan 300 credit")
    b = s.adapter.keyboards[CHAT]
    assert a != b and ("clear_buttons", CHAT, a) in s.api.calls           # one live keyboard
    s.tap("yes", a)
    assert s.saved() == []
    p = s.bot.pending[_OWNER]
    assert p.awaiting == "confirm" and (p.ex.customer, p.ex.paise) == ("Mohan", 30000)
    assert s.adapter.keyboards[CHAT] == b
    s.tap("yes", b)
    assert s.saved() == [("Mohan", 30000)]


def test_clear_buttons_failure_still_finishes_update():  # 6.5
    s = _Stale()
    s.api.fail["clear_buttons"] = TelegramError("message is not modified")
    s.text("Ramesh 500 credit")
    s.text("Mohan 300 credit")              # clearing Ramesh's keyboard fails
    assert s.api.sent()[-1][2] == ["yes", "no"]
    s.tap("yes", s.adapter.keyboards[CHAT])  # clearing the live keyboard fails too
    assert s.saved() == [("Mohan", 30000)]
    assert "Mohan" in s.api.sent()[-1][1] and CHAT not in s.adapter.keyboards


class _OverlapBot(Bot):
    """A real `Bot` that records whether two `handle` calls ever ran at once."""
    inside = 0
    overlap = False

    def handle(self, owner, text, **kw):
        self.inside += 1
        if self.inside > 1:
            self.overlap = True
        time.sleep(0.002)  # widen the window a missing lock would leave open
        try:
            return super().handle(owner, text, **kw)
        finally:
            self.inside -= 1


def test_concurrent_updates_never_overlap_inside_bot():  # 6.6
    s = _Stale(_OverlapBot)
    start = threading.Barrier(2)

    def worker(base):
        start.wait()
        for i in range(20):
            s.adapter.handle_update(text_update(base + i, "list"))

    threads = [threading.Thread(target=worker, args=(b,)) for b in (1, 1001)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(s.api.sent()) == 40 and s.bot.overlap is False


# ---- 5.3 Property 3: Poller offset and order ----

@settings(max_examples=100, deadline=None)
@given(ids=st.lists(st.integers(min_value=0, max_value=10**9), min_size=1, max_size=40,
                    unique=True),
       data=st.data())
def test_poller_offset_and_order_property(ids, data):
    """**Validates: Requirements 2.3, 2.5**

    Property 3: each update is handled exactly once, ascending by update_id within a batch,
    even when handlers raise; offset ends at the largest update_id seen + 1.
    """
    from khata.telegram import Poller

    # Telegram returns ids above the current offset, so batches cover ascending id ranges;
    # within a batch the order is arbitrary.
    ids = sorted(ids)
    cuts = sorted(data.draw(st.sets(st.integers(1, len(ids) - 1), max_size=5))
                  if len(ids) > 1 else set())
    bounds = [0, *cuts, len(ids)]
    batches = [data.draw(st.permutations(ids[a:b])) for a, b in zip(bounds, bounds[1:])]
    failing = data.draw(st.sets(st.sampled_from(ids)))

    class FakeAPI:
        def __init__(self):
            self.offsets: list = []
            self.queue = [[{"update_id": i} for i in b] for b in batches]

        def get_updates(self, offset, timeout):
            self.offsets.append(offset)
            return self.queue.pop(0)

    class FakeAdapter:
        def __init__(self):
            self.seen: list[int] = []

        def handle_update(self, update):
            self.seen.append(update["update_id"])
            if update["update_id"] in failing:
                raise RuntimeError("handler boom")

    api, adapter = FakeAPI(), FakeAdapter()
    poller = Poller(adapter, api, timeout=0)
    expected_offset = None
    for batch in batches:
        before = len(adapter.seen)
        assert poller.run_once() == len(batch)
        assert api.offsets[-1] == expected_offset
        assert adapter.seen[before:] == sorted(batch)
        expected_offset = max(batch) + 1
        assert poller.offset == expected_offset

    assert adapter.seen == ids
    assert poller.offset == max(ids) + 1


# ---- 5.4 Property 4: Poller backoff ----

@settings(max_examples=100, deadline=None)
@given(outcomes=st.lists(st.sampled_from([True, False, False, False]), max_size=80))
def test_poller_backoff_property(outcomes):
    """**Validates: Requirements 2.4**

    Property 4: for any sequence of getUpdates outcomes (True = success, False = failure),
    the k-th consecutive failure (k from 0) waits min(60, 2**k) seconds and a success resets
    the run. deleteWebhook is called once, and the loop ends when the stop event is set.
    """
    from khata.telegram import Poller

    class FakeEvent:
        """Records waits instead of sleeping."""
        def __init__(self):
            self.waits: list[float] = []
            self._set = False

        def is_set(self):
            return self._set

        def set(self):
            self._set = True

        def wait(self, timeout=None):
            self.waits.append(timeout)
            return self._set

    event = FakeEvent()

    class FakeAPI:
        def __init__(self):
            self.queue = list(outcomes)
            self.calls: list[str] = []

        def delete_webhook(self):
            self.calls.append("deleteWebhook")

        def get_updates(self, offset, timeout):
            self.calls.append("getUpdates")
            if not self.queue:
                event.set()  # script exhausted: ask the loop to stop
                return []
            if self.queue.pop(0):
                return []
            raise TelegramError("getUpdates: boom")

    class FakeAdapter:
        def handle_update(self, update):
            raise AssertionError("no updates are delivered")

    api = FakeAPI()
    Poller(FakeAdapter(), api, timeout=0, stop=event).run()

    expected, streak = [], 0
    for ok in outcomes:
        if ok:
            streak = 0
        else:
            expected.append(min(60, 2 ** streak))
            streak += 1

    assert event.waits == expected
    assert api.calls[0] == "deleteWebhook"
    assert api.calls.count("deleteWebhook") == 1
    # One call per scripted outcome, plus the final call that sets the stop event.
    assert api.calls.count("getUpdates") == len(outcomes) + 1


# --- Webhook secret (task 5.5) --------------------------------------------------------

from fastapi import FastAPI
from fastapi.testclient import TestClient

from khata.telegram import make_router, webhook_token


class _RecordingAdapter:
    """Stands in for `TelegramAdapter` behind the router: records every update it receives."""

    def __init__(self):
        self.updates: list[dict] = []

    def handle_update(self, update):
        self.updates.append(update)


# Raw secrets lean on the characters Telegram rejects in secret_token (base64 `+ / =`) and on
# non-ASCII text, mixed with ordinary letters and digits.
_RAW_SECRET = st.one_of(
    st.text(alphabet="ABCxyz019+/=", min_size=1, max_size=64),
    st.text(min_size=1, max_size=64),
)
# Header values a caller could send: printable ASCII (what an HTTP header carries).
_HEADER = st.text(alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E), max_size=80)


# Feature: telegram-channel, Property 5: Webhook secret
@settings(max_examples=100, deadline=None)
@given(secret=_RAW_SECRET, data=st.data())
def test_webhook_secret_property(secret, data):
    """**Validates: Requirements 3.4, 3.8**"""
    token = webhook_token(secret)
    assert token == webhook_token(secret)
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token)

    # Built here: Hypothesis rejects function-scoped fixtures.
    adapter = _RecordingAdapter()
    app = FastAPI()
    app.include_router(make_router(adapter, secret))
    client = TestClient(app)
    update = {"update_id": 1, "message": {"message_id": 1, "text": "hi",
                                          "chat": {"id": CHAT, "type": "private"}}}
    url = "/webhook/telegram"

    # Any other header value: the raw secret, near misses of the token, or arbitrary text.
    other = data.draw(st.one_of(
        st.just(secret), st.just(token.upper()), st.just(token[:-1]), st.just(token + "0"),
        st.just(""), _HEADER))
    assume(other != token)
    r = client.post(url, json=update,
                    headers={"X-Telegram-Bot-Api-Secret-Token": other.encode("utf-8")})
    assert r.status_code == 401
    r = client.post(url, json=update)  # header missing
    assert r.status_code == 401
    assert adapter.updates == []

    # Only the exact derived token is accepted and reaches the adapter.
    r = client.post(url, json=update, headers={"X-Telegram-Bot-Api-Secret-Token": token})
    assert r.status_code == 200
    assert adapter.updates == [update]


# --- Poller/router units, app wiring, export, owner precedence (tasks 5.6, 7.2, 8.3, 9.3) ---

def test_router_status_codes():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from khata.telegram import make_router as tg_router, webhook_token

    got = []

    class Stub:
        def handle_update(self, u):
            got.append(u)

    off = FastAPI()
    off.include_router(tg_router(None, "s"))
    assert TestClient(off).post("/webhook/telegram", json={}).status_code == 503
    app = FastAPI()
    app.include_router(tg_router(Stub(), "s"))
    c = TestClient(app)
    good = {"X-Telegram-Bot-Api-Secret-Token": webhook_token("s")}
    assert c.post("/webhook/telegram", json={"update_id": 1}).status_code == 401
    assert c.post("/webhook/telegram", json={"update_id": 1},
                  headers={"X-Telegram-Bot-Api-Secret-Token": "s"}).status_code == 401
    assert c.post("/webhook/telegram", content=b"{bad", headers=good).status_code == 400
    assert c.post("/webhook/telegram", json=[1], headers=good).status_code == 400
    assert c.post("/webhook/telegram", json={"update_id": 1}, headers=good).status_code == 200
    assert got == [{"update_id": 1}]


def test_poller_survives_error_then_handles_next_batch():
    from khata.telegram import Poller, TelegramError

    class Stop:
        def __init__(self):
            self.set_ = False
            self.waits = []

        def is_set(self):
            return self.set_

        def wait(self, d):
            self.waits.append(d)

        def set(self):
            self.set_ = True

    stop = Stop()
    handled = []

    class API:
        n = 0

        def delete_webhook(self):
            pass

        def get_updates(self, offset, timeout=25):
            API.n += 1
            if API.n == 1:
                raise TelegramError("getUpdates: ConnectError")
            if API.n == 2:
                return [{"update_id": 9}]
            stop.set()
            return []

    class Ad:
        def handle_update(self, u):
            handled.append(u["update_id"])

    p = Poller(Ad(), API(), stop=stop)
    p.run()
    assert stop.waits == [1] and handled == [9] and p.offset == 10


def test_app_wiring_polling_is_off_in_tests_and_full_flow(monkeypatch):
    from fastapi.testclient import TestClient
    from khata.app import create_app
    from khata.guard import PaidGuard

    api = FakeAPI()
    app = create_app(":memory:", PaidGuard(key="", budget_calls=0, killed=False), telegram_api=api)
    assert app.state.telegram is not None
    c = TestClient(app)                       # no `with`: lifespan (poller thread) never starts
    assert c.get("/health").json()["telegram"] == "polling"
    lab = {p["part"]: p for p in c.get("/api/lab").json()["parts"]}
    assert lab["Voice box"]["alive"] and "Telegram" in lab["Voice box"]["impl"]

    tg = app.state.telegram
    tg.handle_update(text_update(1, "Ramesh ko 500 udhaar"))
    assert api.sent()[-1][2] == ["mode:bill", "mode:entry", "mode:udhaar"]     # pick a mode first
    tg.handle_update({"update_id": 90, "callback_query": {
        "id": "m", "data": "mode:udhaar", "message": {"message_id": tg.keyboards[CHAT],
                                                      "chat": {"id": CHAT, "type": "private"}}}})
    mid = tg.keyboards[CHAT]
    tg.handle_update({"update_id": 2, "callback_query": {
        "id": "q", "data": "yes", "message": {"message_id": mid, "chat": {"id": CHAT, "type": "private"}}}})
    owner = owner_for_chat(CHAT, "")
    assert app.state.ledger.balance(owner, "Ramesh") == 50000
    tg.handle_update(text_update(3, "/export@Ledgy637Bot"))
    docs = [cl for cl in api.calls if cl[0] == "send_document"]
    assert len(docs) == 1 and docs[0][3][:2] == b"PK"


def test_no_token_means_telegram_off():
    from fastapi.testclient import TestClient
    from khata.app import create_app
    from khata.guard import PaidGuard

    app = create_app(":memory:", PaidGuard(key="", budget_calls=0, killed=False))
    assert app.state.telegram is None
    assert TestClient(app).get("/health").json()["telegram"] == "off"


def test_sheet_owner_precedence(monkeypatch):
    from khata.app import sheet_owner
    from khata.whatsapp import owner_for

    assert sheet_owner() is None
    monkeypatch.setenv("KHATA_SHEET_OWNER_NUMBER", "919876543210")
    monkeypatch.setenv("WA_APP_SECRET", "w")
    assert sheet_owner() == owner_for("919876543210", "w")
    monkeypatch.setenv("KHATA_SHEET_OWNER_TELEGRAM_ID", "123456789")
    monkeypatch.setenv("KHATA_OWNER_SALT", "salty")
    assert sheet_owner() == owner_for_chat(123456789, "salty")


def test_mcp_owner_from_telegram(monkeypatch):
    from khata.mcp_server import resolve_owner

    monkeypatch.delenv("KHATA_MCP_OWNER", raising=False)
    monkeypatch.setenv("KHATA_SHEET_OWNER_TELEGRAM_ID", "123456789")
    monkeypatch.setenv("KHATA_OWNER_SALT", "salty")
    assert resolve_owner() == owner_for_chat(123456789, "salty")
