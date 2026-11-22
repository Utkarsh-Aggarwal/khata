import pytest

import khata.voice


@pytest.fixture(autouse=True)
def _skip_ffmpeg_for_fake_audio(request, monkeypatch):
    """Most tests send fake audio bytes that real ffmpeg rightly rejects. Skip conversion for them,
    so results don't depend on whether ffmpeg is installed. Tests marked real_ffmpeg use the real one."""
    if request.node.get_closest_marker("real_ffmpeg") is None:
        monkeypatch.setattr(khata.voice, "to_wav", lambda src, dst: src)
