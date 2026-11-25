import os

import pytest

import khata.voice

# Telegram settings a developer's .env may set. Tests must never start a real poller or call the network.
TELEGRAM_ENV = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_MODE",
    "TELEGRAM_WEBHOOK_SECRET",
    "TELEGRAM_ALLOWED_CHAT_IDS",
    "KHATA_OWNER_SALT",
    "KHATA_SHEET_OWNER_TELEGRAM_ID",
    # Google Sheets: without credentials the app makes no sheet pool, so no test reads a real sheet.
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    "KHATA_SHEET_ID",
    "KHATA_REGISTRY_SHEET_ID",
)


# khata/app.py builds `app = create_app()` on import. Strip the settings now, before any test
# module imports it, or a shell with .env loaded would start a mirror on the real Google Sheet.
for _name in TELEGRAM_ENV + ("KHATA_DB",):
    os.environ.pop(_name, None)


@pytest.fixture(autouse=True)
def _no_telegram_env(monkeypatch):
    """Remove Telegram settings for every test; tests that need them set them with monkeypatch."""
    for name in TELEGRAM_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _skip_ffmpeg_for_fake_audio(request, monkeypatch):
    """Most tests send fake audio bytes that real ffmpeg rightly rejects. Skip conversion for them,
    so results don't depend on whether ffmpeg is installed. Tests marked real_ffmpeg use the real one."""
    if request.node.get_closest_marker("real_ffmpeg") is None:
        monkeypatch.setattr(khata.voice, "to_wav", lambda src, dst: src)
