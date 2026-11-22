# Extraction eval (rule-based demo mode)

Exact match on intent + customer + amount (+ dates where labelled).

## golden (developed against, expect overfit): **32/32** (100%)

| language | correct |
|---|---|
| en | 8/8 |
| hi | 4/4 |
| hinglish | 17/17 |
| pa | 3/3 |

Wrong entry proposed straight to the confirm step: **0/32**. Other failures make the bot ask a follow-up question instead.

## dev2 (was held-out v1: scored 6/15 = 40% before fixes, then tuned on): **15/15** (100%)

| language | correct |
|---|---|
| en | 4/4 |
| hi | 2/2 |
| hinglish | 7/7 |
| pa | 2/2 |

Wrong entry proposed straight to the confirm step: **0/15**. Other failures make the bot ask a follow-up question instead.

## blind (written before running, never tuned on): **16/20** (80%)

| language | correct |
|---|---|
| en | 3/5 |
| hi | 3/3 |
| hinglish | 8/10 |
| pa | 2/2 |

Wrong entry proposed straight to the confirm step: **0/20**. Other failures make the bot ask a follow-up question instead.

Failures:

- `Put 90 on Farhan's account`: intent: got 'add_unknown', want 'add_credit'; customer: got 'Put', want 'Farhan'
- `how much does Farhan owe`: customer: got 'How Much Does', want 'Farhan'
- `Ravi ne 500 GPay kiya`: intent: got 'add_unknown', want 'add_payment'
- `kaun kaun baaki hai`: intent: got 'add_credit', want 'list'; customer: got 'Kaun Kaun', want None

All cases are hand-written text. Real voice transcripts will score lower.
