"""Real Groq Whisper transcripts from a live test (macOS 'say' voices). Whisper ends Hindi
sentences with the danda (।) and may switch a Latin name into Devanagari."""
from khata.extract import extract
from khata.flow import Bot
from khata.ledger import Ledger

LIVE = [
    ("रमेश को पाँच सौ रुपये उधार।", "add_credit", "रमेश", 50000),
    ("सीता ने 200 रुपये दिये।", "add_payment", "सीता", 20000),
    ("मोहन को dhai hazaar udhaar.", "add_credit", "मोहन", 250000),
]


def test_live_whisper_transcripts():
    for text, intent, cust, paise in LIVE:
        e = extract(text)
        assert (e.intent, e.customer, e.paise) == (intent, cust, paise), text


def test_danda_reply_counts_as_yes():
    lg = Ledger()
    b = Bot(lg)
    b.handle("o", "Ramesh ko 500 udhaar")
    b.handle("o", "हाँ।")
    assert lg.balance("o", "रमेश") == 50000



def test_first_person_is_not_part_of_the_name():
    """From a real Telegram voice note after the Hindi retry."""
    for text in ["मैंने रमेश को 500 रुपये उधार दिए।", "मैं ने रमेश को 500 रुपये उधार दिए।"]:
        e = extract(text)
        assert (e.intent, e.customer, e.paise) == ("add_credit", "रमेश", 50000), text
    e = extract("Maine Ramesh ko 500 udhaar diye")
    assert (e.intent, e.customer, e.paise) == ("add_credit", "Ramesh", 50000)
