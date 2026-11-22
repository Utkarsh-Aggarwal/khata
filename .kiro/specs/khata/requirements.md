# Requirements: Khata

A paper khata (credit ledger) stitched to voice, vision and chat, with ghosts that collect the dues.
Users: small shopkeepers in India who give udhaar (credit) and speak Hindi, Punjabi, Hinglish or English.

## R1. Record credit and payments from text
**User story:** As a shopkeeper, I want to type "Ramesh ko 500 udhaar" so that I don't need a form.

1. WHEN a message names a customer, an amount and a direction THEN the system SHALL propose a credit or payment entry and ask for confirmation.
2. WHEN the customer, amount or direction is missing THEN the system SHALL ask only for the missing part.
3. The system SHALL read Indian number words (dedh, dhai, sawa, saadhe, paune, hazaar, lakh) in Latin, Devanagari and Gurmukhi script.
4. WHEN "kal" or "parso" appears THEN the system SHALL read it as past or future from the verb tense.
5. IF a single amount exceeds ₹1 crore THEN the system SHALL refuse it and ask again.

## R2. Confirm before saving
1. The system SHALL NOT save any entry until the user replies yes (haan, हाँ, ਹਾਂ, ✅).
2. WHEN the user replies no THEN the system SHALL discard the entry.
3. WHEN the user sends a bare amount, or the same customer with a new amount, THEN the system SHALL edit the pending entry.
4. WHEN the user sends an unrelated message THEN the system SHALL discard the pending entry and say so.
5. WHEN the user says "undo" THEN the system SHALL mark the last entry cancelled, not delete it.

## R3. Customers across scripts
1. The system SHALL treat Ramesh, रमेश and ਰਮੇਸ਼ as one customer.
2. The system SHALL treat names that differ in a written vowel (Mohan and Mohini) as different customers.

## R4. Voice notes
1. WHEN a voice note arrives and the AI budget allows THEN the system SHALL transcribe it and route the text through R1 and R2.
2. The system SHALL delete the audio after transcription, on success and on failure.
3. IF AI is off, rate-limited or out of budget THEN the system SHALL ask the user to type instead.

## R5. Bill photos
1. WHEN a bill photo arrives THEN the system SHALL read only vendor, total and date.
2. The system SHALL always ask who the customer is and whether it is credit or payment.
3. The system SHALL treat text inside the image as data, never as instructions.
4. The system SHALL offer a pre-read sample bill that works with AI off.

## R6. WhatsApp
1. The system SHALL answer Meta's verify handshake and reject unsigned or wrongly signed webhooks.
2. The system SHALL ignore repeated deliveries of the same message id.
3. The system SHALL store owners as salted hashes, not raw phone numbers.

## R7. Ghosts and séance reminders
1. The system SHALL show each customer who owes money as a ghost, most overdue first.
2. WHEN a ghost is tapped THEN the system SHALL open WhatsApp with a drafted reminder in the chosen language. The system SHALL NOT send it.
3. Demo data SHALL NOT include customer phone numbers.

## R8. Sheet mirror and export
1. The system SHALL mirror entries to a Google Sheet without duplicate rows, retrying on failure.
2. WHEN an entry is undone THEN the system SHALL mark its row cancelled.
3. WHEN the host starts with an empty disk THEN the system SHALL rebuild the shopkeeper ledger from the sheet before serving requests.
4. IF that rebuild fails THEN the system SHALL pause syncing rather than risk reusing entry ids.
5. The system SHALL export the ledger as .xlsx.

## R9. Judges and demo mode
1. A fresh browser SHALL get a seeded demo account with no sign-up, kept across page refreshes.
2. With AI off, text chat, ghosts, séance, the sample bill, undo and export SHALL all work.
3. The Lab panel SHALL show each stitched part and whether it is alive.

## R10. Free and safe to run
1. All services SHALL run on free tiers with no card: Groq, Render, Google Sheets, Meta's test number.
2. The system SHALL rate-limit chat per session and demo logins per network, and purge idle demo accounts after 7 days.
3. Text colours SHALL meet WCAG AA contrast.
