"""Conversation flow: extract -> ask for gaps -> confirm -> save. Nothing is saved unasked."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from . import billing
from .extract import CATEGORY_OF, Extraction, extract, parse_amount
from .ledger import Ledger, format_rupees
from .names import name_key
from .reminders import ghosts, normalize_phone

MODE_IDS = {"mode:bill": "bill", "mode:entry": "entry", "mode:udhaar": "udhaar"}
IN_W = {"bikri", "bikree", "sale", "sales", "sold", "becha", "bechi", "beche", "mila", "mile", "aaya", "aaye", "aya",
        "received", "income", "in", "jama", "बिक्री", "बेचा", "मिला", "मिले", "आया", "आए", "जमा", "ਵਿਕਰੀ", "ਮਿਲੇ"}
SALE_W = {"bikri", "bikree", "sale", "sales", "sold", "becha", "bechi", "beche", "बिक्री", "बेचा", "ਵਿਕਰੀ"}
OUT_W = {"kharcha", "kharch", "kharche", "expense", "spent", "paid", "bhara", "bhare", "diya", "diye", "out",
         "khareeda", "kharida", "kharide", "खर्च", "खर्चा", "भरा", "दिया", "दिए", "खरीदा", "ਖਰਚਾ"}
RATES_W = {"rates", "/rates", "rate list", "price list", "prices", "daam", "भाव", "रेट"}
MAX_ENTRY_PAISE = 10**7 * 100     # ₹1 crore: larger single entries are almost surely misreads
PHONE_W = {"number", "nambar", "numbar", "phone", "mobile", "whatsapp", "नंबर", "नम्बर", "फोन", "ਨੰਬਰ", "ਫੋਨ"}
PHONE_FILLER = {"ka", "ki", "ke", "is", "hai", "my", "of", "का", "की", "है", "ਦਾ", "ਦੀ", "ਹੈ", "no", "no."}
REMIND_W = {"remind", "reminder", "reminders", "yaad", "seance", "séance", "याद", "ਯਾਦ", "tagada", "तगादा"}

YES = {"haan", "han", "ha", "yes", "y", "ok", "okay", "confirm", "sahi", "theek", "thik", "हाँ", "हां",
       "हा", "सही", "ठीक", "ਹਾਂ", "ਠੀਕ", "✅", "1", "yes.", "save"}
NO = {"nahi", "nahin", "no", "n", "cancel", "nope", "नहीं", "नही", "रद्द", "ਨਹੀਂ", "ਨਹੀ", "❌", "0", "galat", "गलत"}
CREDIT_W = {"credit", "udhaar", "udhar", "उधार", "ਉਧਾਰ", "gave", "diya"}
# Answers to "which way did the money go?"
KIND_PAY = {"payment", "paid", "received", "jama", "जमा", "ਜਮ੍ਹਾ", "📥", "they", "he", "she", "unhone", "usne",
            "उन्होंने", "उसने", "mila", "mile", "मिले", "aaya", "aaye", "आए", "ਉਸਨੇ", "ਮਿਲੇ"}
KIND_CREDIT = {"credit", "udhaar", "udhar", "उधार", "ਉਧਾਰ", "📤", "i", "maine", "मैंने", "मैने", "ਮੈਂ", "gave",
               "diya", "diye", "दिए", "lent", "returned", "lautaye", "लौटाए", "bhara", "भरा"}
DOUBT_EXACT = {"?", "??", "kya", "kya?", "matlab", "matlab?", "samjhao", "explain", "what", "what?", "help me",
               "क्या", "क्या?", "मतलब", "मतलब?", "समझाओ", "ਕੀ", "ਕੀ?", "ਮਤਲਬ", "confused", "not sure", "pata nahi"}
# Whole-message commands
DUES_W = {"dues", "/dues", "udhaar", "/udhaar", "udhar", "lena dena", "lena-dena", "len den", "hisaab", "/hisaab",
          "hisab", "sab ka hisaab", "उधार", "लेन देन", "लेन-देन", "हिसाब", "ਉਧਾਰ", "ਹਿਸਾਬ", "ਲੈਣ ਦੇਣ"}
TODAY_W = {"today", "/today", "aaj", "/aaj", "aaj ka kharcha", "aaj ka kharch", "today's expenses",
           "todays expenses", "आज", "आज का खर्च", "आज का खर्चा", "ਅੱਜ", "ਅੱਜ ਦਾ ਖਰਚਾ"}
EXPENSE_ANSWER = {"expense", "kharcha", "kharch", "shop", "dukaan", "dukan", "mera", "meri", "apna", "apni",
                  "khud", "🧾", "खर्च", "खर्चा", "दुकान", "मेरा", "अपना", "ਖਰਚਾ", "ਦੁਕਾਨ", "ਮੇਰਾ", "mine", "my",
                  "me", "myself", "self", "i", "main", "mai", "mujhe", "maine", "hum", "hamara", "humara", "ours",
                  "मैं", "मै", "मुझे", "खुद", "हमारा", "ਮੈਂ", "ਖੁਦ", "ਸਾਡਾ"}
# Never a customer's name: answering "Who is the customer?" with one of these re-asks.
NOT_A_NAME = EXPENSE_ANSWER | {"you", "he", "she", "they", "him", "her", "woh", "wo", "vo", "usne", "unhone",
                               "वो", "उसने", "yes", "no", "haan", "nahi", "ok", "credit", "payment", "?"}
CATEGORY_NAME = {
    "electricity": ("Electricity", "बिजली", "ਬਿਜਲੀ"), "rent": ("Rent", "किराया", "ਕਿਰਾਇਆ"),
    "salary": ("Salary", "तनख्वाह", "ਤਨਖਾਹ"), "transport": ("Transport", "भाड़ा/पेट्रोल", "ਭਾੜਾ/ਪੈਟਰੋਲ"),
    "stock": ("Stock", "माल", "ਮਾਲ"), "phone": ("Phone/internet", "फ़ोन/इंटरनेट", "ਫ਼ੋਨ/ਇੰਟਰਨੈੱਟ"),
    "water": ("Water", "पानी", "ਪਾਣੀ"), "gas": ("Gas", "गैस", "ਗੈਸ"), "tea": ("Tea/snacks", "चाय-नाश्ता", "ਚਾਹ"),
    "repair": ("Repair", "मरम्मत", "ਮੁਰੰਮਤ"), "purchase": ("Purchase", "खरीद", "ਖਰੀਦ"),
    "sale": ("Sale", "बिक्री", "ਵਿਕਰੀ"),
    "other": ("Other", "अन्य", "ਹੋਰ"),
}
DOUBT_PHRASES = ("kya matlab", "matlab kya", "samajh nahi", "samjh nahi", "what does", "what is credit",
                 "what is payment", "which one", "kaunsa", "konsa", "क्या मतलब", "समझ नहीं", "ਸਮਝ ਨਹੀਂ")
PAY_W = {"payment", "paid", "received", "jama", "जमा", "wapas", "वापस", "ਜਮ੍ਹਾ"}

M = {
    "confirm_credit": {"en": "Save: 📤 you gave {name} {amt}{due}. After this, {after}. Reply yes / no, or send a new amount.",
                       "hi": "सेव करूँ: 📤 आपने {name} को {amt} दिए (उधार){due}। इसके बाद {after}। हाँ / नहीं, या नई रकम भेजें।",
                       "pa": "ਸੇਵ ਕਰਾਂ: 📤 ਤੁਸੀਂ {name} ਨੂੰ {amt} ਦਿੱਤੇ (ਉਧਾਰ){due}। ਇਸ ਤੋਂ ਬਾਅਦ {after}। ਹਾਂ / ਨਹੀਂ, ਜਾਂ ਨਵੀਂ ਰਕਮ ਭੇਜੋ।"},
    "confirm_payment": {"en": "Save: 📥 {name} gave you {amt}. After this, {after}. Reply yes / no, or send a new amount.",
                        "hi": "सेव करूँ: 📥 {name} ने आपको {amt} दिए। इसके बाद {after}। हाँ / नहीं, या नई रकम भेजें।",
                        "pa": "ਸੇਵ ਕਰਾਂ: 📥 {name} ਨੇ ਤੁਹਾਨੂੰ {amt} ਦਿੱਤੇ। ਇਸ ਤੋਂ ਬਾਅਦ {after}। ਹਾਂ / ਨਹੀਂ, ਜਾਂ ਨਵੀਂ ਰਕਮ ਭੇਜੋ।"},
    "saved": {"en": "Saved. Now {after}.", "hi": "सेव हो गया। अब {after}।", "pa": "ਸੇਵ ਹੋ ਗਿਆ। ਹੁਣ {after}।"},
    "confirm_expense": {"en": "Save: 🧾 shop expense {amt} ({cat}){note}? No customer's balance changes. "
                              "This month's expenses would be {month}. Reply yes / no, or send a new amount.",
                        "hi": "सेव करूँ: 🧾 दुकान का खर्च {amt} ({cat}){note}? किसी ग्राहक का हिसाब नहीं बदलेगा। "
                              "इस महीने का कुल खर्च {month} होगा। हाँ / नहीं, या नई रकम भेजें।",
                        "pa": "ਸੇਵ ਕਰਾਂ: 🧾 ਦੁਕਾਨ ਦਾ ਖਰਚਾ {amt} ({cat}){note}? ਕਿਸੇ ਗਾਹਕ ਦਾ ਹਿਸਾਬ ਨਹੀਂ ਬਦਲੇਗਾ। "
                              "ਇਸ ਮਹੀਨੇ ਦਾ ਕੁੱਲ ਖਰਚਾ {month} ਹੋਵੇਗਾ। ਹਾਂ / ਨਹੀਂ, ਜਾਂ ਨਵੀਂ ਰਕਮ ਭੇਜੋ।"},
    "confirm_expense_dated": {
        "en": "Save: 🧾 shop expense {amt} ({cat}){note}, dated {date} from the bill? It counts in {month_name}, not this "
              "month. Reply yes / no, or 'today' if you paid it today.",
        "hi": "सेव करूँ: 🧾 दुकान का खर्च {amt} ({cat}){note}, बिल की तारीख {date}? यह {month_name} में गिना जाएगा, इस "
              "महीने में नहीं। हाँ / नहीं, या आज भरा हो तो 'आज' भेजें।",
        "pa": "ਸੇਵ ਕਰਾਂ: 🧾 ਦੁਕਾਨ ਦਾ ਖਰਚਾ {amt} ({cat}){note}, ਬਿੱਲ ਦੀ ਤਾਰੀਖ {date}? ਇਹ {month_name} ਵਿੱਚ ਗਿਣਿਆ ਜਾਵੇਗਾ। "
              "ਹਾਂ / ਨਹੀਂ, ਜਾਂ ਅੱਜ ਭਰਿਆ ਹੋਵੇ ਤਾਂ 'ਅੱਜ' ਭੇਜੋ।"},
    "saved_expense_dated": {"en": "Saved 🧾 {cat} {amt}, dated {date} ({month_name}).",
                            "hi": "सेव हो गया 🧾 {cat} {amt}, तारीख {date} ({month_name})।",
                            "pa": "ਸੇਵ ਹੋ ਗਿਆ 🧾 {cat} {amt}, ਤਾਰੀਖ {date} ({month_name})।"},
    "saved_expense": {"en": "Saved 🧾 {cat} {amt}. This month's expenses: {month}.",
                      "hi": "सेव हो गया 🧾 {cat} {amt}। इस महीने का खर्च: {month}।",
                      "pa": "ਸੇਵ ਹੋ ਗਿਆ 🧾 {cat} {amt}। ਇਸ ਮਹੀਨੇ ਦਾ ਖਰਚਾ: {month}।"},
    "expense_report": {"en": "🧾 Shop expenses this month: {month}", "hi": "🧾 इस महीने दुकान का खर्च: {month}",
                       "pa": "🧾 ਇਸ ਮਹੀਨੇ ਦੁਕਾਨ ਦਾ ਖਰਚਾ: {month}"},
    "today_report": {"en": "🧾 Today's shop expenses: {total}", "hi": "🧾 आज का दुकान खर्च: {total}",
                     "pa": "🧾 ਅੱਜ ਦਾ ਦੁਕਾਨ ਖਰਚਾ: {total}"},
    "no_expenses_today": {"en": "No shop expenses recorded today.", "hi": "आज कोई खर्च दर्ज नहीं।",
                          "pa": "ਅੱਜ ਕੋਈ ਖਰਚਾ ਦਰਜ ਨਹੀਂ।"},
    "dues_get": {"en": "📤 Udhaar you gave (they owe you): {total}", "hi": "📤 आपने उधार दिया (आपको मिलना है): {total}",
                 "pa": "📤 ਤੁਸੀਂ ਉਧਾਰ ਦਿੱਤਾ (ਤੁਹਾਨੂੰ ਮਿਲਣਾ ਹੈ): {total}"},
    "dues_owe": {"en": "📥 Udhaar you took (you owe them): {total}", "hi": "📥 आपने उधार लिया (आपको देना है): {total}",
                 "pa": "📥 ਤੁਸੀਂ ਉਧਾਰ ਲਿਆ (ਤੁਸੀਂ ਦੇਣਾ ਹੈ): {total}"},
    "dues_net_get": {"en": "Net: you will receive {amt}.", "hi": "कुल मिलाकर: आपको {amt} मिलने हैं।",
                     "pa": "ਕੁੱਲ: ਤੁਹਾਨੂੰ {amt} ਮਿਲਣੇ ਹਨ।"},
    "dues_net_owe": {"en": "Net: you have to pay {amt}.", "hi": "कुल मिलाकर: आपको {amt} देने हैं।",
                     "pa": "ਕੁੱਲ: ਤੁਸੀਂ {amt} ਦੇਣੇ ਹਨ।"},
    "dues_net_zero": {"en": "Net: all square.", "hi": "कुल मिलाकर: हिसाब बराबर।", "pa": "ਕੁੱਲ: ਹਿਸਾਬ ਬਰਾਬਰ।"},
    "no_expenses": {"en": "No shop expenses recorded this month. Try: 'bijli ka bill 900'.",
                    "hi": "इस महीने कोई खर्च दर्ज नहीं। लिखें: 'बिजली का बिल 900'।",
                    "pa": "ਇਸ ਮਹੀਨੇ ਕੋਈ ਖਰਚਾ ਦਰਜ ਨਹੀਂ। ਲਿਖੋ: 'ਬਿਜਲੀ ਦਾ ਬਿੱਲ 900'।"},
    "ask_whose": {"en": "Whose bill was this {amt}? Send the person's name, or tap 🧾 Shop expense if the shop paid "
                        "its own cost (electricity, rent, stock...).",
                  "hi": "यह {amt} किसका बिल था? व्यक्ति का नाम भेजें, या अगर दुकान का अपना खर्च था (बिजली, किराया, माल...) "
                        "तो 🧾 दुकान का खर्च दबाएँ।",
                  "pa": "ਇਹ {amt} ਕਿਸਦਾ ਬਿੱਲ ਸੀ? ਵਿਅਕਤੀ ਦਾ ਨਾਮ ਭੇਜੋ, ਜਾਂ ਜੇ ਦੁਕਾਨ ਦਾ ਆਪਣਾ ਖਰਚਾ ਸੀ ਤਾਂ 🧾 ਦੁਕਾਨ ਦਾ ਖਰਚਾ ਦਬਾਓ।"},
    "explain_expense": {"en": "🧾 shop expense = the shop's own cost: electricity, rent, salary, stock, tea. It counts in "
                              "your monthly spending and changes no customer's balance.",
                        "hi": "🧾 दुकान का खर्च = दुकान की अपनी लागत: बिजली, किराया, तनख्वाह, माल, चाय। यह महीने के खर्च में "
                              "जुड़ता है, किसी ग्राहक का हिसाब नहीं बदलता।",
                        "pa": "🧾 ਦੁਕਾਨ ਦਾ ਖਰਚਾ = ਦੁਕਾਨ ਦੀ ਆਪਣੀ ਲਾਗਤ। ਇਹ ਮਹੀਨੇ ਦੇ ਖਰਚੇ ਵਿੱਚ ਜੁੜਦਾ ਹੈ, ਕਿਸੇ ਗਾਹਕ ਦਾ "
                              "ਹਿਸਾਬ ਨਹੀਂ ਬਦਲਦਾ।"},
    "choose_mode": {"en": "What is this?\n🧾 Bill: make a bill for a customer (items, qty, rate)\n"
                          "📒 Entry: record money in or out (sale, expense, purchase)\n"
                          "🤝 Udhaar: credit given or taken, and money returned",
                    "hi": "यह क्या है?\n🧾 बिल: ग्राहक का बिल बनाएँ (सामान, मात्रा, रेट)\n"
                          "📒 एंट्री: पैसे आए या गए (बिक्री, खर्च, खरीद)\n🤝 उधार: उधार दिया या लिया, और वापसी",
                    "pa": "ਇਹ ਕੀ ਹੈ?\n🧾 ਬਿੱਲ: ਗਾਹਕ ਦਾ ਬਿੱਲ ਬਣਾਓ\n📒 ਐਂਟਰੀ: ਪੈਸੇ ਆਏ ਜਾਂ ਗਏ\n🤝 ਉਧਾਰ: ਉਧਾਰ ਦਿੱਤਾ ਜਾਂ ਲਿਆ, ਅਤੇ ਵਾਪਸੀ"},
    "ask_direction": {"en": "Did this {amt} come in or go out?", "hi": "ये {amt} आए या गए?",
                      "pa": "ਇਹ {amt} ਆਏ ਜਾਂ ਗਏ?"},
    "confirm_entry_in": {"en": "Save: 📥 money in {amt} ({cat}){note}? No one's balance changes. Reply yes / no, "
                               "or send a new amount.",
                         "hi": "सेव करूँ: 📥 पैसे आए {amt} ({cat}){note}? किसी का हिसाब नहीं बदलेगा। हाँ / नहीं, "
                               "या नई रकम भेजें।",
                         "pa": "ਸੇਵ ਕਰਾਂ: 📥 ਪੈਸੇ ਆਏ {amt} ({cat}){note}? ਕਿਸੇ ਦਾ ਹਿਸਾਬ ਨਹੀਂ ਬਦਲੇਗਾ। ਹਾਂ / ਨਹੀਂ।"},
    "saved_entry_in": {"en": "Saved 📥 {cat} {amt}.", "hi": "सेव हो गया 📥 {cat} {amt}।",
                       "pa": "ਸੇਵ ਹੋ ਗਿਆ 📥 {cat} {amt}।"},
    "bill_pay": {"en": "Cash or udhaar?", "hi": "नकद या उधार?", "pa": "ਨਕਦ ਜਾਂ ਉਧਾਰ?"},
    "bill_who": {"en": "Whose udhaar is this bill? Send the customer's name.",
                 "hi": "यह बिल किसके उधार में लिखूँ? ग्राहक का नाम भेजें।",
                 "pa": "ਇਹ ਬਿੱਲ ਕਿਸਦੇ ਉਧਾਰ ਵਿੱਚ ਲਿਖਾਂ? ਗਾਹਕ ਦਾ ਨਾਮ ਭੇਜੋ।"},
    "bill_rate": {"en": "What is the rate for {name}? Send e.g. '{name} 45'.",
                  "hi": "{name} का रेट क्या है? जैसे '{name} 45' भेजें।", "pa": "{name} ਦਾ ਰੇਟ ਕੀ ਹੈ? ਜਿਵੇਂ '{name} 45'।"},
    "bill_empty": {"en": "Add items first, e.g. atta 2 kg 45.", "hi": "पहले सामान लिखें, जैसे atta 2 kg 45।",
                   "pa": "ਪਹਿਲਾਂ ਸਮਾਨ ਲਿਖੋ, ਜਿਵੇਂ atta 2 kg 45।"},
    "bill_cash": {"en": "✅ Bill No. {no} saved: cash sale {amt}. The PDF is below.",
                  "hi": "✅ बिल नंबर {no} सेव: नकद बिक्री {amt}। PDF नीचे है।",
                  "pa": "✅ ਬਿੱਲ ਨੰਬਰ {no} ਸੇਵ: ਨਕਦ ਵਿਕਰੀ {amt}। PDF ਹੇਠਾਂ ਹੈ।"},
    "bill_udhaar": {"en": "✅ Bill No. {no} saved as udhaar: {amt}. Now {after}. The PDF is below.",
                    "hi": "✅ बिल नंबर {no} उधार में सेव: {amt}। अब {after}। PDF नीचे है।",
                    "pa": "✅ ਬਿੱਲ ਨੰਬਰ {no} ਉਧਾਰ ਵਿੱਚ ਸੇਵ: {amt}। ਹੁਣ {after}। PDF ਹੇਠਾਂ ਹੈ।"},
    "bill_cancelled": {"en": "Bill cancelled. Nothing saved.", "hi": "बिल रद्द। कुछ सेव नहीं हुआ।",
                       "pa": "ਬਿੱਲ ਰੱਦ। ਕੁਝ ਸੇਵ ਨਹੀਂ ਹੋਇਆ।"},
    "rates": {"en": "🏷️ Your rates (they fill bills automatically):", "hi": "🏷️ आपके रेट (बिल में अपने आप भरते हैं):",
              "pa": "🏷️ ਤੁਹਾਡੇ ਰੇਟ:"},
    "no_rates": {"en": "No rates saved yet. They are remembered from your bills, or send 'rate atta 45'.",
                 "hi": "अभी कोई रेट सेव नहीं। बिल से अपने आप याद होते हैं, या 'rate atta 45' भेजें।",
                 "pa": "ਹਾਲੇ ਕੋਈ ਰੇਟ ਨਹੀਂ। 'rate atta 45' ਭੇਜੋ।"},
    "rate_saved": {"en": "🏷️ Rate saved: {name} {amt}.", "hi": "🏷️ रेट सेव: {name} {amt}।", "pa": "🏷️ ਰੇਟ ਸੇਵ: {name} {amt}।"},
    "now_label": {"en": "Right now: ", "hi": "अभी: ", "pa": "ਹੁਣ: "},
    "owes_you": {"en": "{name} owes you {amt}", "hi": "{name} पर {amt} बाकी", "pa": "{name} ਤੇ {amt} ਬਾਕੀ"},
    "you_owe": {"en": "you owe {name} {amt}", "hi": "आपको {name} को {amt} देने हैं",
                "pa": "ਤੁਸੀਂ {name} ਨੂੰ {amt} ਦੇਣੇ ਹਨ"},
    "settled": {"en": "{name}'s account is settled (₹0)", "hi": "{name} का हिसाब बराबर (₹0)",
                "pa": "{name} ਦਾ ਹਿਸਾਬ ਬਰਾਬਰ (₹0)"},
    "explain": {"en": "📤 credit = money or goods went from you to {name}: you gave udhaar, gave cash, paid their bill "
                      "for them, or returned their money. 📥 payment = money came from {name} to you: they paid you, "
                      "paid their bill, or returned your money. {now}",
                "hi": "📤 उधार = पैसा या सामान आपसे {name} को गया: उधार दिया, नकद दिया, उनका बिल भरा, या उनके पैसे लौटाए। "
                      "📥 जमा = पैसा {name} से आपको आया: उन्होंने पैसे दिए, बिल चुकाया, या आपके पैसे लौटाए। {now}",
                "pa": "📤 ਉਧਾਰ = ਪੈਸਾ ਜਾਂ ਸਮਾਨ ਤੁਹਾਡੇ ਤੋਂ {name} ਨੂੰ ਗਿਆ। 📥 ਜਮ੍ਹਾ = ਪੈਸਾ {name} ਤੋਂ ਤੁਹਾਨੂੰ ਆਇਆ। {now}"},
    "cancelled": {"en": "Cancelled. Nothing saved.", "hi": "रद्द किया। कुछ सेव नहीं हुआ।",
                  "pa": "ਰੱਦ ਕੀਤਾ। ਕੁਝ ਸੇਵ ਨਹੀਂ ਹੋਇਆ।"},
    "discarded": {"en": "(The unsaved entry was discarded.)", "hi": "(बिना सेव की एंट्री हटा दी गई।)",
                  "pa": "(ਬਿਨਾਂ ਸੇਵ ਕੀਤੀ ਐਂਟਰੀ ਹਟਾ ਦਿੱਤੀ ਗਈ।)"},
    "export": {"en": "Download your khata as Excel: {url}", "hi": "अपना खाता Excel में डाउनलोड करें: {url}",
               "pa": "ਆਪਣਾ ਖਾਤਾ Excel ਵਿੱਚ ਡਾਊਨਲੋਡ ਕਰੋ: {url}"},
    "export_doc": {"en": "Here is your khata as an Excel file.", "hi": "आपका खाता Excel फ़ाइल में।",
                   "pa": "ਤੁਹਾਡਾ ਖਾਤਾ Excel ਫ਼ਾਈਲ ਵਿੱਚ।"},
    "export_wa": {"en": "Excel export is available in the web app.", "hi": "Excel एक्सपोर्ट वेब ऐप में है।",
                  "pa": "Excel ਐਕਸਪੋਰਟ ਵੈੱਬ ਐਪ ਵਿੱਚ ਹੈ।"},
    "phone_saved": {"en": "Saved {name}'s WhatsApp number: {phone}.", "hi": "{name} का WhatsApp नंबर सेव किया: {phone}।",
                    "pa": "{name} ਦਾ WhatsApp ਨੰਬਰ ਸੇਵ ਕੀਤਾ: {phone}।"},
    "bad_phone": {"en": "That doesn't look like a mobile number. Send 10 digits, e.g. Ramesh ka number 98765 43210.",
                  "hi": "यह मोबाइल नंबर नहीं लगता। 10 अंक भेजें, जैसे: रमेश का नंबर 98765 43210",
                  "pa": "ਇਹ ਮੋਬਾਈਲ ਨੰਬਰ ਨਹੀਂ ਲੱਗਦਾ। 10 ਅੰਕ ਭੇਜੋ, ਜਿਵੇਂ: ਰਮੇਸ਼ ਦਾ ਨੰਬਰ 98765 43210"},
    "seance": {"en": "🕯️ Séance: tap a link to send that reminder from WhatsApp.",
               "hi": "🕯️ तगादा: लिंक दबाएँ, WhatsApp में संदेश तैयार मिलेगा।",
               "pa": "🕯️ ਤਗਾਦਾ: ਲਿੰਕ ਦਬਾਓ, WhatsApp ਵਿੱਚ ਸੁਨੇਹਾ ਤਿਆਰ ਮਿਲੇਗਾ।"},
    "no_dues": {"en": "Nobody owes you anything. The ghosts rest.", "hi": "किसी पर कुछ बाकी नहीं। भूत आराम कर रहे हैं।",
                "pa": "ਕਿਸੇ ਤੇ ਕੁਝ ਬਾਕੀ ਨਹੀਂ। ਭੂਤ ਆਰਾਮ ਕਰ ਰਹੇ ਹਨ।"},
    "too_big": {"en": "That amount looks too large (over ₹1 crore).", "hi": "यह रकम बहुत बड़ी लग रही है (₹1 करोड़ से ज़्यादा)।",
                "pa": "ਇਹ ਰਕਮ ਬਹੁਤ ਵੱਡੀ ਲੱਗਦੀ ਹੈ (₹1 ਕਰੋੜ ਤੋਂ ਵੱਧ)।"},
    "ask_customer": {"en": "Who is the customer?", "hi": "ग्राहक कौन है?", "pa": "ਗਾਹਕ ਕੌਣ ਹੈ?"},
    "ask_amount": {"en": "How much (₹)?", "hi": "कितने रुपये?", "pa": "ਕਿੰਨੇ ਰੁਪਏ?"},
    "ask_kind": {"en": "Which way did the {amt} go? 📤 credit: you gave it to {name} (udhaar, cash, paid their bill, "
                       "or returned their money). 📥 payment: {name} gave it to you. Not sure? Ask '?'",
                 "hi": "{amt} किस तरफ गए? 📤 उधार: आपने {name} को दिए (उधार, नकद, उनका बिल भरा, या पैसे लौटाए)। "
                       "📥 जमा: {name} ने आपको दिए। समझ न आए तो '?' भेजें।",
                 "pa": "{amt} ਕਿਸ ਪਾਸੇ ਗਏ? 📤 ਉਧਾਰ: ਤੁਸੀਂ {name} ਨੂੰ ਦਿੱਤੇ। 📥 ਜਮ੍ਹਾ: {name} ਨੇ ਤੁਹਾਨੂੰ ਦਿੱਤੇ। "
                       "ਸਮਝ ਨਾ ਆਵੇ ਤਾਂ '?' ਭੇਜੋ।"},
    "balance": {"en": "{after}.", "hi": "{after}।", "pa": "{after}।"},
    "nobody": {"en": "No entries yet.", "hi": "अभी कोई एंट्री नहीं।", "pa": "ਹਾਲੇ ਕੋਈ ਐਂਟਰੀ ਨਹੀਂ।"},
    "undone": {"en": "Undone: {what}.", "hi": "वापस किया: {what}।", "pa": "ਵਾਪਸ ਕੀਤਾ: {what}।"},
    "nothing_undo": {"en": "Nothing to undo.", "hi": "वापस करने को कुछ नहीं।", "pa": "ਵਾਪਸ ਕਰਨ ਲਈ ਕੁਝ ਨਹੀਂ।"},
    "unknown": {"en": "I didn't get that. Try: 'Ramesh ko 500 udhaar' or 'Sita ne 200 diye'. Type help.",
                "hi": "समझ नहीं आया। लिखें: 'रमेश को 500 उधार' या 'सीता ने 200 दिए'। help लिखें।",
                "pa": "ਸਮਝ ਨਹੀਂ ਆਇਆ। ਲਿਖੋ: 'ਰਮੇਸ਼ ਨੂੰ 500 ਉਧਾਰ'। help ਲਿਖੋ।"},
    "help": {"en": "Khata: say or type 'Ramesh ko 500 udhaar' (credit), 'Ramesh ne 200 diye' (payment), "
                   "'Ramesh ka kitna baaki', 'list', 'undo', 'export', 'Ramesh ka number 98765 43210', "
                   "'remind', 'bijli ka bill 900' (shop expense), 'kharcha'. You can also send a voice note or a bill photo. Every entry asks for confirmation.",
             "hi": "खाता: 'रमेश को 500 उधार', 'रमेश ने 200 दिए', 'रमेश का कितना बाकी', 'list', 'undo', "
                   "'रमेश का नंबर 98765 43210', 'याद', 'बिजली का बिल 900' (दुकान का खर्च), 'खर्चा'। वॉइस नोट या बिल की फोटो भी भेज सकते हैं। हर एंट्री पर पुष्टि होगी।",
             "pa": "ਖਾਤਾ: 'ਰਮੇਸ਼ ਨੂੰ 500 ਉਧਾਰ', 'ਰਮੇਸ਼ ਨੇ 200 ਦਿੱਤੇ', 'list', 'undo', 'ਰਮੇਸ਼ ਦਾ ਨੰਬਰ 98765 43210', "
                   "'ਯਾਦ', 'ਬਿਜਲੀ ਦਾ ਬਿੱਲ 900' (ਦੁਕਾਨ ਦਾ ਖਰਚਾ)। ਵੌਇਸ ਨੋਟ ਜਾਂ ਬਿੱਲ ਦੀ ਫੋਟੋ ਵੀ ਭੇਜ ਸਕਦੇ ਹੋ। ਹਰ ਐਂਟਰੀ ਤੇ ਪੁਸ਼ਟੀ ਹੋਵੇਗੀ।"},
}


def L(lang: str) -> str:
    return "hi" if lang in ("hi", "hinglish") else ("pa" if lang == "pa" else "en")


def _month_name(d: str) -> str:
    try:
        return date.fromisoformat(d).strftime("%B %Y")
    except ValueError:
        return d


@dataclass
class Pending:
    ex: Extraction
    source: str = "text"
    awaiting: str | None = None   # customer | amount | kind | confirm


@dataclass
class Bot:
    ledger: Ledger
    pending: dict[str, Pending] = field(default_factory=dict)
    base_url: str = ""
    # Owners (by id prefix, e.g. "tg-") who pick a mode for each new record: Bill, Entry or Udhaar.
    mode_prefixes: tuple = ()
    drafts: dict = field(default_factory=dict)          # owner -> billing.Draft
    waiting_mode: dict = field(default_factory=dict)    # owner -> (text, BillFields | None, source, lang)

    def say(self, key: str, lang: str, **kw) -> str:
        return M[key][L(lang)].format(**kw)

    def handle(self, owner: str, text: str, *, source: str = "text", today: date | None = None) -> dict:
        text = (text or "").strip()
        if not text:
            return {"reply": self.say("help", "en"), "buttons": []}
        with self.ledger.lock:    # web threads and WhatsApp background tasks share `pending`
            # Phone numbers first: otherwise a 10-digit number could be read as an amount edit.
            # A pending entry is left untouched so the shopkeeper can still confirm it.
            phone_cmd = self._phone_command(owner, text)
            if phone_cmd:
                return phone_cmd
            if owner in self.drafts:
                r = self._bill_step(owner, text, source)
                if r is not None:
                    return r
            w = self.waiting_mode.pop(owner, None)
            if w:
                if text in MODE_IDS:
                    return self._choose_mode(owner, MODE_IDS[text], *w)
                if self._is_doubt(text):
                    self.waiting_mode[owner] = w
                    return self._mode_prompt(w[3])
            p = self.pending.get(owner)
            if p and self._is_doubt(text):
                return self._explain(owner, p)        # answer the question, keep the entry waiting
            if p:
                r = self._continue(owner, p, text)
                if r is not None:
                    return r
                # Unrelated message: drop the unsaved entry so a later "yes" can't save it by surprise.
                self.pending.pop(owner, None)
                r = self._fresh(owner, text, source, today)
                r["reply"] = self.say("discarded", p.ex.lang) + "\n" + r["reply"]
                return r
            return self._fresh(owner, text, source, today)

    # ------------------------------------------------------------
    def _fresh(self, owner, text, source, today):
        low = text.lower().strip()
        if low in ("bill", "/bill", "बिल", "ਬਿੱਲ"):
            self.drafts[owner] = billing.Draft(lang=extract(text).lang)
            return self._bill_view(owner)
        if low in RATES_W:
            return self._rates(owner, extract(text).lang)
        if low.startswith("rate ") and parse_amount(text)[0]:
            return self._set_rate(owner, text)
        if set(low.replace("/", " ").split()) & REMIND_W:
            return self._remind(owner, extract(text).lang)
        if low in DUES_W:
            return self._r(self._dues(owner, extract(text).lang))
        if low in TODAY_W:
            return self._r(self._today_report(owner, extract(text).lang))
        if low in ("export", "/export", "एक्सपोर्ट", "ਐਕਸਪੋਰਟ"):
            lang = extract(text).lang
            if source == "telegram":
                return self._r(self.say("export_doc", lang), export=True)
            if source == "whatsapp":
                return self._r(self.say("export_wa", lang))
            return self._r(self.say("export", lang, url=self.base_url + "/api/export.xlsx"), export=True)
        ex = extract(text, today)
        lang = ex.lang
        if ex.intent == "help":
            return self._r(self.say("help", lang))
        if ex.intent == "undo":
            e = self.ledger.undo_last(owner)
            if not e:
                return self._r(self.say("nothing_undo", lang))
            what = (f"{'📥' if getattr(e, 'direction', 'out') == 'in' else '🧾'} {self._cat(lang, e.category)} "
                    f"{format_rupees(e.paise)}" if e.kind == "expense"
                    else f"{e.customer} {format_rupees(e.paise)} ({e.kind})")
            return self._r(self.say("undone", lang, what=what), changed=True)
        if ex.intent == "expense_report":
            if ex.period == "today":
                return self._r(self._today_report(owner, lang))
            return self._r(self._expense_report(owner, lang))
        if ex.intent == "list":
            return self._r(self._dues(owner, lang))
        if ex.intent == "balance":
            if ex.customer:
                real = self.ledger.find_customer(owner, ex.customer)
                if real:
                    return self._r(self.say("balance", lang,
                                            after=self._after(lang, real, self.ledger.balance(owner, real))))
            return self._r(self._dues(owner, lang))
        if self._modes(owner):                       # no guessing: the shopkeeper picks the mode
            self.waiting_mode[owner] = (text, None, source, lang)
            return self._mode_prompt(lang)
        if ex.intent in ("add_credit", "add_payment", "add_unknown", "add_expense"):
            if ex.customer:
                real = self.ledger.find_customer(owner, ex.customer)
                if real:
                    ex.customer = real
            p = Pending(ex, source)
            self.pending[owner] = p
            return self._advance(owner, p)
        return self._r(self.say("unknown", lang))

    def _phone_command(self, owner: str, text: str) -> dict | None:
        """'Ramesh ka number 98765 43210' stores a WhatsApp number. Not money, so no confirm step."""
        low = text.lower()
        if not any(w in low for w in PHONE_W):
            return None
        m = re.search(r"\+?\d[\d\s-]{8,16}\d", text)
        if not m:
            return None
        lang = extract(text).lang
        phone = normalize_phone(m.group(0))
        if not phone:
            return self._r(self.say("bad_phone", lang))
        rest = (text[:m.start()] + " " + text[m.end():]).split()
        name = " ".join(t for t in (w.strip(".,:;!?") for w in rest)
                        if t and t.lower() not in PHONE_W and t.lower() not in PHONE_FILLER)[:40]
        if not name_key(name):
            return self._r(self.say("ask_customer", lang))
        real = self.ledger.find_customer(owner, name) or name.title()
        stored = self.ledger.set_phone(owner, real, phone)
        return self._r(self.say("phone_saved", lang, name=stored, phone="+" + phone), changed=True)

    def _remind(self, owner: str, lang: str) -> dict:
        gs = ghosts(self.ledger, owner, lang if lang in ("hi", "pa", "en") else "hinglish")
        if not gs:
            return self._r(self.say("no_dues", lang))
        lines = [self.say("seance", lang)]
        for g in gs[:10]:
            late = f" ⚠️ {g.days_overdue}d" if g.days_overdue else ""
            lines.append(f"👻 {g.name} {format_rupees(g.paise)}{late}: {g.wa}")
        return self._r("\n".join(lines))

    # ---- modes: Bill / Entry / Udhaar ----------------------------------------------------
    def _modes(self, owner: str) -> bool:
        return bool(self.mode_prefixes) and owner.startswith(tuple(self.mode_prefixes))

    def _mode_prompt(self, lang: str) -> dict:
        return self._r(self.say("choose_mode", lang), buttons=list(MODE_IDS))

    def start_from_bill(self, owner: str, f, intro_text: str) -> dict:
        """A bill photo. With modes on, the shopkeeper picks what it is; else the old udhaar flow."""
        from .bills import to_extraction
        if not self._modes(owner):
            r = self.start_from_extraction(owner, to_extraction(f), "bill")
            return {**r, "reply": intro_text + "\n" + r["reply"]}
        self.pending.pop(owner, None)
        self.drafts.pop(owner, None)
        self.waiting_mode[owner] = (None, f, "bill", "en")
        r = self._mode_prompt("en")
        return {**r, "reply": intro_text + "\n\n" + r["reply"]}

    def _choose_mode(self, owner: str, mode: str, text, bill, source: str, lang: str) -> dict:
        from .bills import items_note, to_extraction
        self.pending.pop(owner, None)
        if mode == "bill":
            d = billing.Draft(lang=lang)
            if bill:
                for it in bill.items:
                    line = billing.parse_line(f"{it.name} {it.qty}") or billing.Line(it.name)
                    line.name = it.name
                    if it.rate and line.unit not in billing.AMOUNT_UNITS:
                        line.rate, line.total = it.rate, None
                    elif it.amount:
                        line.rate, line.total = None, it.amount
                    d.lines.append(line)
            if text:
                billing.apply(d, text, lambda nm: self.ledger.price(owner, nm))
            self.drafts[owner] = d
            return self._bill_view(owner)
        if mode == "entry":
            if bill:
                ex = Extraction("add_expense", paise=bill.paise, category="purchase", vendor=bill.vendor or "",
                                note=items_note(bill), entry_date=bill.bill_date, lang=lang, direction="out")
            else:
                ex = self._entry_extraction(text)
            p = Pending(ex, source)
            self.pending[owner] = p
            return self._advance(owner, p)
        # udhaar
        if bill:
            ex = to_extraction(bill)
            ex.maybe_expense = False
        else:
            ex = extract(text)
            if ex.intent not in ("add_credit", "add_payment", "add_unknown"):
                paise = ex.paise if ex.paise is not None else parse_amount(text)[0]
                ex = Extraction("add_unknown", customer=ex.customer if ex.intent != "add_expense" else (ex.vendor or None),
                                paise=paise, lang=ex.lang)
            if ex.customer:
                ex.customer = self.ledger.find_customer(owner, ex.customer) or ex.customer
        return self.start_from_extraction(owner, ex, source)

    def _entry_extraction(self, text: str) -> Extraction:
        """Entry mode: money in or out of the shop, no person's balance involved."""
        ex = extract(text)
        paise = ex.paise if ex.paise is not None else parse_amount(text)[0]
        words = set(text.lower().replace(",", " ").split())
        cats = [CATEGORY_OF[w] for w in text.lower().split() if w in CATEGORY_OF]
        if words & IN_W:
            direction, cat = "in", ("sale" if words & SALE_W else "other")
        elif words & OUT_W or cats or ex.intent == "add_expense":
            direction, cat = "out", (ex.category if ex.intent == "add_expense" else (cats[0] if cats else "other"))
        else:
            direction, cat = "", "other"
        vendor = ex.vendor if ex.intent == "add_expense" else (ex.customer or "")
        note = " ".join(w for w in text.split() if not any(ch.isdigit() for ch in w))[:60]
        return Extraction("add_expense", paise=paise, category=cat, vendor=vendor or "", note=note,
                          entry_date=ex.entry_date if ex.intent == "add_expense" else None, lang=ex.lang,
                          direction=direction)

    # ---- bill generator ------------------------------------------------------------------
    def _bill_view(self, owner: str, extra: str = "") -> dict:
        d = self.drafts[owner]
        text = billing.render_text(d) + (("\n" + extra) if extra else "")
        return self._r(text, buttons=["bill:done", "bill:cancel"])

    def _bill_step(self, owner: str, text: str, source: str) -> dict | None:
        d = self.drafts[owner]
        low = text.lower().strip().rstrip(".!।")
        lang = d.lang
        if low in billing.CANCEL_W or (low in NO and d.stage != "customer"):
            del self.drafts[owner]
            return self._r(self.say("bill_cancelled", lang))
        if d.stage == "customer":
            if set(low.split()) <= NOT_A_NAME or not name_key(text):
                return self._r(self.say("bill_who", lang))
            name = self.ledger.find_customer(owner, " ".join(text.split())[:40]) or " ".join(text.split())[:40].title()
            return self._finish_bill(owner, name, "udhaar", source)
        if d.stage == "payment":
            if low in ("pay:cash", "cash", "nakad", "naqad", "नकद", "ਨਕਦ"):
                return self._finish_bill(owner, None, "cash", source)
            if low in ("pay:udhaar", "udhaar", "udhar", "उधार", "ਉਧਾਰ", "credit"):
                d.stage = "customer"
                return self._r(self.say("bill_who", lang))
            d.stage = "editing"                      # anything else: back to editing
        if low in billing.DONE_W:
            if not d.lines:
                return self._bill_view(owner, self.say("bill_empty", lang))
            miss = d.missing_rates()
            if miss:
                return self._bill_view(owner, self.say("bill_rate", lang, name=miss[0].name))
            d.stage = "payment"
            return self._r(billing.render_text(d, "🧾 Bill") + "\n\n" + self.say("bill_pay", lang),
                           buttons=["pay:cash", "pay:udhaar", "bill:cancel"])
        if self._is_command(text):
            return None                              # e.g. /dues while drafting: answer it, keep the draft
        before = [(l.name, l.qty, l.unit, l.rate, l.total) for l in d.lines]
        billing.apply(d, text, lambda nm: self.ledger.price(owner, nm))
        if before == [(l.name, l.qty, l.unit, l.rate, l.total) for l in d.lines]:
            return self._bill_view(owner, "(I didn't find an item in that. Try: atta 2 kg 45)")
        return self._bill_view(owner)

    def _is_command(self, text: str) -> bool:
        low = text.lower().strip()
        if low in DUES_W or low in TODAY_W or low in RATES_W or set(low.replace("/", " ").split()) & REMIND_W:
            return True
        return extract(text).intent in ("help", "undo", "list", "balance", "expense_report")

    def _finish_bill(self, owner: str, customer: str | None, payment: str, source: str) -> dict:
        d = self.drafts.pop(owner)
        items = [{"name": l.name, "qty": l.qty_text(), "rate": l.rate, "amount": l.amount} for l in d.lines]
        for l in d.lines:                            # remember rates for next time
            if l.rate:
                self.ledger.set_price(owner, l.name, l.rate, l.unit)
        no = self.ledger.add_bill(owner, customer, items, d.total, payment)
        note = f"Bill {no}: " + "; ".join(f"{l.name} {l.qty_text()} {format_rupees(l.amount)}" for l in d.lines)
        note = note if len(note) <= 190 else note[:187] + "..."
        pdf = billing.build_pdf(d, bill_no=no, customer=customer, payment=payment)
        extra = {"pdf": pdf, "pdf_name": f"bill-{no}.pdf", "changed": True, "bill_no": no}
        if payment == "cash":
            self.ledger.add_expense(owner, "sale", d.total, note=note, direction="in", source="bill")
            return self._r(self.say("bill_cash", d.lang, no=no, amt=format_rupees(d.total)), **extra)
        self.ledger.add_entry(owner, customer, "credit", d.total, note=note, source="bill")
        after = self._after(d.lang, customer, self.ledger.balance(owner, customer))
        return self._r(self.say("bill_udhaar", d.lang, no=no, amt=format_rupees(d.total), after=after), **extra)

    def _rates(self, owner: str, lang: str) -> dict:
        rs = self.ledger.prices(owner)
        if not rs:
            return self._r(self.say("no_rates", lang))
        return self._r("\n".join([self.say("rates", lang)] +
                                  [f"• {n}: {format_rupees(r)}" + (f" / {u}" if u else "") for n, r, u in rs]))

    def _set_rate(self, owner: str, text: str) -> dict:
        line = billing.parse_line(text[5:])
        if not line or not line.rate:
            return self._r(self.say("no_rates", "en"))
        self.ledger.set_price(owner, line.name, line.rate, line.unit)
        return self._r(self.say("rate_saved", extract(text).lang, name=line.name, amt=format_rupees(line.rate)))

    def start_from_extraction(self, owner: str, ex: Extraction, source: str) -> dict:
        """Used by voice/bill adapters: always routes through the same confirm step."""
        if ex.customer:
            real = self.ledger.find_customer(owner, ex.customer)
            ex.customer = real or ex.customer
        p = Pending(ex, source)
        self.pending[owner] = p
        return self._advance(owner, p)

    # ---- shop expenses -----------------------------------------------------------------------
    @staticmethod
    def _cat(lang: str, category: str | None) -> str:
        names = CATEGORY_NAME.get(category or "other")
        if not names:
            return category or "other"
        return names[{"en": 0, "hi": 1, "pa": 2}[L(lang)]]

    def _today_report(self, owner: str, lang: str) -> str:
        today = date.today().isoformat()
        xs = self.ledger.expenses(owner, since=today, until=today, direction="out")
        if not xs:
            return self.say("no_expenses_today", lang)
        lines = [self.say("today_report", lang, total=format_rupees(sum(x.paise for x in xs)))]
        lines += [f"• {self._cat(lang, x.category)} {format_rupees(x.paise)}" + (f" ({x.vendor})" if x.vendor else "")
                  for x in xs]
        return "\n".join(lines)

    def _dues(self, owner: str, lang: str) -> str:
        """Who owes you (udhaar you gave) and whom you owe (udhaar you took), with totals."""
        b = {k: v for k, v in self.ledger.balances(owner).items() if v}
        if not b:
            return self.say("nobody", lang)
        give = [(k, v) for k, v in b.items() if v > 0]
        take = sorted(((k, -v) for k, v in b.items() if v < 0), key=lambda kv: -kv[1])
        lines = []
        if give:
            lines.append(self.say("dues_get", lang, total=format_rupees(sum(v for _, v in give))))
            lines += [f"• {k} {format_rupees(v)}" for k, v in give]
        if take:
            if lines:
                lines.append("")
            lines.append(self.say("dues_owe", lang, total=format_rupees(sum(v for _, v in take))))
            lines += [f"• {k} {format_rupees(v)}" for k, v in take]
        net = sum(b.values())
        lines += ["", self.say("dues_net_get" if net > 0 else "dues_net_owe" if net < 0 else "dues_net_zero", lang,
                               amt=format_rupees(abs(net)))]
        return "\n".join(lines)

    @staticmethod
    def _this_month(d: str | None) -> bool:
        return not d or d[:7] == date.today().isoformat()[:7]

    def _month_total(self, owner: str) -> int:
        return sum(self.ledger.expense_totals(owner, date.today().replace(day=1).isoformat()).values())

    def _expense_report(self, owner: str, lang: str) -> str:
        totals = self.ledger.expense_totals(owner, date.today().replace(day=1).isoformat())
        if not totals:
            return self.say("no_expenses", lang)
        lines = [self.say("expense_report", lang, month=format_rupees(sum(totals.values())))]
        lines += [f"• {self._cat(lang, c)}: {format_rupees(v)}" for c, v in totals.items()]
        return "\n".join(lines)

    # ---- plain-language balances and doubts ------------------------------------------------
    def _after(self, lang: str, name: str, bal: int) -> str:
        if bal > 0:
            return self.say("owes_you", lang, name=name, amt=format_rupees(bal))
        if bal < 0:
            return self.say("you_owe", lang, name=name, amt=format_rupees(-bal))
        return self.say("settled", lang, name=name)

    @staticmethod
    def _is_doubt(text: str) -> bool:
        low = text.lower().strip()
        return low in DOUBT_EXACT or any(p in low for p in DOUBT_PHRASES)

    def _ask_kind(self, lang: str, ex: Extraction) -> dict:
        return self._r(self.say("ask_kind", lang, name=ex.customer, amt=format_rupees(ex.paise)),
                       buttons=["credit", "payment", "?"])

    def _explain(self, owner: str, p: Pending) -> dict:
        """'?' while an entry waits: explain what credit/payment mean here, then ask again."""
        ex, lang = p.ex, p.ex.lang
        if ex.intent == "add_expense" or (ex.maybe_expense and not ex.customer):
            nxt = self._advance(owner, p)
            extra = "" if ex.intent == "add_expense" else "\n" + self.say(
                "explain", lang, name={"en": "a person", "hi": "किसी व्यक्ति", "pa": "ਕਿਸੇ ਵਿਅਕਤੀ"}[L(lang)], now="")
            return {**nxt, "reply": self.say("explain_expense", lang) + extra + "\n\n" + nxt["reply"]}
        name = ex.customer or {"en": "the customer", "hi": "ग्राहक", "pa": "ਗਾਹਕ"}[L(lang)]
        now = self._after(lang, ex.customer, self.ledger.balance(owner, ex.customer)) if ex.customer else ""
        now = (self.say("now_label", lang) + now) if now else ""
        nxt = self._advance(owner, p)
        return {**nxt, "reply": self.say("explain", lang, name=name, now=now) + "\n\n" + nxt["reply"]}

    def _advance(self, owner, p: Pending):
        ex, lang = p.ex, p.ex.lang
        if ex.intent == "add_expense":
            if ex.paise is not None and ex.paise > MAX_ENTRY_PAISE:
                ex.paise = None
                p.awaiting = "amount"
                return self._r(self.say("too_big", lang) + " " + self.say("ask_amount", lang))
            if ex.paise is None:
                p.awaiting = "amount"; return self._r(self.say("ask_amount", lang))
            if not ex.direction:
                p.awaiting = "direction"
                return self._r(self.say("ask_direction", lang, amt=format_rupees(ex.paise)), buttons=["dir:in", "dir:out"])
            p.awaiting = "confirm"
            note = f": {ex.vendor}" if ex.vendor else ""
            if ex.direction == "in":
                return self._r(self.say("confirm_entry_in", lang, amt=format_rupees(ex.paise),
                                        cat=self._cat(lang, ex.category), note=note), buttons=["yes", "no"], pending=True)
            if not self._this_month(ex.entry_date):        # e.g. an old bill photographed now
                return self._r(self.say("confirm_expense_dated", lang, amt=format_rupees(ex.paise),
                                        cat=self._cat(lang, ex.category), note=note, date=ex.entry_date,
                                        month_name=_month_name(ex.entry_date)),
                               buttons=["yes", "no", "today"], pending=True)
            month = format_rupees(self._month_total(owner) + ex.paise)
            return self._r(self.say("confirm_expense", lang, amt=format_rupees(ex.paise), cat=self._cat(lang, ex.category),
                                    note=note, month=month), buttons=["yes", "no"], pending=True)
        if not ex.customer:
            p.awaiting = "customer"
            if ex.maybe_expense:
                amt = format_rupees(ex.paise) if ex.paise else ""
                return self._r(self.say("ask_whose", lang, amt=amt).replace("  ", " "), buttons=["expense", "?"])
            return self._r(self.say("ask_customer", lang))
        if ex.paise is not None and ex.paise > MAX_ENTRY_PAISE:
            ex.paise = None
            p.awaiting = "amount"
            return self._r(self.say("too_big", lang) + " " + self.say("ask_amount", lang))
        if ex.paise is None:
            p.awaiting = "amount"; return self._r(self.say("ask_amount", lang))
        if ex.intent == "add_unknown":
            p.awaiting = "kind"
            return self._ask_kind(lang, ex)
        p.awaiting = "confirm"
        key = "confirm_credit" if ex.intent == "add_credit" else "confirm_payment"
        due = f" (due {ex.due_date})" if ex.due_date else ""
        delta = ex.paise if ex.intent == "add_credit" else -ex.paise
        after = self._after(lang, ex.customer, self.ledger.balance(owner, ex.customer) + delta)
        return self._r(self.say(key, lang, name=ex.customer, amt=format_rupees(ex.paise), due=due, after=after),
                       buttons=["yes", "no"], pending=True)

    def _continue(self, owner, p: Pending, text: str):
        low = text.lower().strip().rstrip(".!।॥")
        ex, lang = p.ex, p.ex.lang
        if low in NO:
            del self.pending[owner]
            return self._r(self.say("cancelled", lang))
        if p.awaiting == "customer":
            words = set(low.replace("🧾", " 🧾 ").split())
            if ex.maybe_expense and words & EXPENSE_ANSWER:
                ex.intent, ex.category = "add_expense", ex.category or ("purchase" if p.source == "bill" else "other")
                return self._advance(owner, p)
            if extract(text).intent in ("undo", "help", "list", "balance", "expense_report") \
                    or low in ("export", "/export") or set(low.split()) & REMIND_W:
                return None
            name = " ".join(text.split())[:40]
            if not name_key(name) or set(name.lower().split()) <= NOT_A_NAME:
                return self._advance(owner, p)        # ask the same question again
            
            ex.customer = name.title()
            real = self.ledger.find_customer(owner, ex.customer)
            ex.customer = real or ex.customer
            return self._advance(owner, p)
        if p.awaiting == "amount":
            amt, _ = parse_amount(text)
            if amt is None:
                return self._r(self.say("ask_amount", lang))
            ex.paise = amt
            return self._advance(owner, p)
        if p.awaiting == "kind":
            w = set(low.replace("📤", " 📤 ").replace("📥", " 📥 ").split())
            if w & KIND_PAY:                  # checked first: "they gave" also contains "gave"
                ex.intent = "add_payment"
            elif w & KIND_CREDIT:
                ex.intent = "add_credit"
            else:
                return self._ask_kind(lang, ex)
            return self._advance(owner, p)
        if p.awaiting == "direction":
            w = set(low.split())
            if low == "dir:in" or w & IN_W:
                ex.direction = "in"
                if ex.category not in ("sale", "other"):
                    ex.category = "other"
            elif low == "dir:out" or w & OUT_W:
                ex.direction = "out"
            else:
                return self._advance(owner, p)
            return self._advance(owner, p)
        # confirm stage
        if low in YES and ex.intent == "add_expense":
            x_id = self.ledger.add_expense(owner, ex.category or "other", ex.paise, note=ex.note,
                                           vendor=ex.vendor, entry_date=ex.entry_date, source=p.source,
                                           direction=ex.direction or "out")
            del self.pending[owner]
            if ex.direction == "in":
                return self._r(self.say("saved_entry_in", lang, cat=self._cat(lang, ex.category),
                                        amt=format_rupees(ex.paise)), changed=True, expense_id=x_id)
            if not self._this_month(ex.entry_date):
                return self._r(self.say("saved_expense_dated", lang, cat=self._cat(lang, ex.category),
                                        amt=format_rupees(ex.paise), date=ex.entry_date,
                                        month_name=_month_name(ex.entry_date)), changed=True, expense_id=x_id)
            return self._r(self.say("saved_expense", lang, cat=self._cat(lang, ex.category), amt=format_rupees(ex.paise),
                                    month=format_rupees(self._month_total(owner))), changed=True, expense_id=x_id)
        if ex.intent == "add_expense" and low in TODAY_W:
            ex.entry_date = None                              # paid today: date it today
            return self._advance(owner, p)
        if ex.intent == "add_expense":
            amt, _ = parse_amount(text)
            if amt is not None and len(low.split()) <= 3:
                ex.paise = amt
                return self._advance(owner, p)
            return None
        if low in YES:
            e_id = self.ledger.add_entry(
                owner, ex.customer, "credit" if ex.intent == "add_credit" else "payment",
                ex.paise, note=ex.note, entry_date=ex.entry_date, due_date=ex.due_date,
                source=p.source)
            del self.pending[owner]
            bal = self.ledger.balance(owner, ex.customer)
            return self._r(self.say("saved", lang, after=self._after(lang, ex.customer, bal)),
                           changed=True, entry_id=e_id)
        amt, _ = parse_amount(text)
        if amt is not None and len(low.split()) <= 4:    # edit amount, unless it names someone else
            other = extract(text).customer
            if other is None or self.ledger.find_customer(owner, other) == ex.customer \
                    or name_key(other) == name_key(ex.customer):
                ex.paise = amt
                return self._advance(owner, p)
            return None
        if low in ("credit", "udhaar", "उधार") or low in PAY_W:
            ex.intent = "add_credit" if low in ("credit", "udhaar", "उधार") else "add_payment"
            return self._advance(owner, p)
        return None  # treat as a brand-new message (old pending dropped by _fresh)

    @staticmethod
    def _r(reply, buttons=None, **extra):
        return {"reply": reply, "buttons": buttons or [], **extra}
