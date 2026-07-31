"""Korean speech -> English captions, and the domain repair pass around it.
Run: .venv/bin/python tests/test_translate.py

Whisper is stubbed: the point under test is which task/kwargs the pipeline asks
for and what it does with the words that come back, none of which needs audio.
"""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import clipper as c


class _Word:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


class _Seg:
    def __init__(self, words):
        self.words = words


class _Info:
    def __init__(self, language, prob):
        self.language, self.language_probability = language, prob


class FakeWhisper:
    """Records every transcribe() call; returns `verbatim` first, then `english`
    if a translate pass is requested."""

    def __init__(self, language, prob, verbatim, english=None):
        self.language, self.prob = language, prob
        self.verbatim, self.english = verbatim, english or verbatim
        self.calls = []

    def transcribe(self, path, **kw):
        self.calls.append(kw)
        words = self.english if kw.get("task") == "translate" else self.verbatim
        segs = [_Seg([_Word(f" {w}", s, e) for w, s, e in words])]
        return iter(segs), _Info(self.language, self.prob)


def _run(model, translate=True):
    with tempfile.TemporaryDirectory() as d:
        clip = pathlib.Path(d) / "clip.mp4"
        clip.write_bytes(b"")
        orig, c._WHISPER = c._WHISPER, model
        try:
            ass, hook, transcript = c.make_dynamic_captions(
                clip, 2, 100, 66, translate=translate)
            text = ass.read_text(encoding="utf-8") if ass else ""
        finally:
            c._WHISPER = orig
    return text, hook, transcript


KOREAN = [("페이커가", 0.0, 0.5), ("바론을", 0.5, 1.0), ("스틸했어요", 1.0, 1.6)]
ENGLISH = [("Faker", 0.0, 0.5), ("stole", 0.5, 1.0), ("Baron", 1.0, 1.6)]


# ── the ask: Korean gets translated ──────────────────────────────────────────
def test_korean_speech_is_captioned_in_english():
    m = FakeWhisper("ko", 0.99, KOREAN, ENGLISH)
    text, hook, transcript = _run(m)
    assert len(m.calls) == 2, "should re-decode with the translate task"
    assert m.calls[1]["task"] == "translate", m.calls[1]
    assert m.calls[1]["language"] == "ko", "skip re-detecting a known language"
    assert "FAKER" in text and "BARON" in text, text
    assert "페이커가" not in text, "Korean survived into the captions"
    assert "Faker" in transcript, transcript


def test_english_speech_is_never_re_decoded():
    m = FakeWhisper("en", 0.99, ENGLISH)
    text, _, _ = _run(m)
    assert len(m.calls) == 1, "English should cost exactly one pass"
    assert "task" not in m.calls[0], m.calls[0]
    assert "FAKER" in text


def test_no_translate_flag_captions_verbatim():
    m = FakeWhisper("ko", 0.99, KOREAN, ENGLISH)
    text, _, _ = _run(m, translate=False)
    assert len(m.calls) == 1, "--no-translate must not trigger a translate pass"
    assert "페이커가" in text, "verbatim mode should keep the Korean"


def test_low_confidence_detection_does_not_trigger_translation():
    """Below LANG_MIN_PROB the detector is guessing, and translating on a guess
    would silently rewrite an English clip."""
    m = FakeWhisper("ko", 0.20, KOREAN, ENGLISH)
    _run(m)
    assert len(m.calls) == 1, "a low-confidence guess must not force a translation"


def test_translate_languages_is_just_korean_for_now():
    assert c.TRANSLATE_LANGS == {"ko"}, c.TRANSLATE_LANGS


# ── the domain passes wrapped around it ──────────────────────────────────────
def test_decoder_is_biased_toward_esports_vocabulary():
    m = FakeWhisper("en", 0.99, ENGLISH)
    _run(m)
    prompt = m.calls[0].get("initial_prompt", "")
    assert "Faker" in prompt and "Baron Nashor" in prompt, prompt


def test_mishears_are_repaired_before_they_reach_the_screen():
    m = FakeWhisper("en", 0.99,
                    [("fake", 0.0, 0.3), ("her", 0.3, 0.6),
                     ("with", 0.6, 0.9), ("the", 0.9, 1.1),
                     ("penta", 1.1, 1.4), ("kill", 1.4, 1.8)])
    text, _, transcript = _run(m)
    assert "FAKER" in text and "PENTAKILL" in text, text
    assert "FAKE HER" not in text
    assert transcript == "Faker with the Pentakill", transcript


def test_captioned_korean_source_still_gets_our_english_layer():
    """`has_existing_captions` exists to stop DUPLICATE layers. Korean burned-in
    text plus an English translation is not a duplicate — it is the feature."""
    import inspect
    src = inspect.getsource(c.cut_clip)
    assert "detect_speech_language" in src, \
        "cut_clip stopped asking what language a captioned source speaks"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("translate tests passed")
