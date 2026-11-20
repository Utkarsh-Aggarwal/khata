#!/usr/bin/env bash
# Smoke test a running Khata server over real HTTP. Usage: scripts/smoke.sh [base_url]
set -euo pipefail
B="${1:-http://localhost:8765}"
J="$(mktemp)"; trap 'rm -f "$J"' EXIT
chat() { curl -sf -b "$J" -H 'Content-Type: application/json' -d "{\"text\":\"$1\"}" "$B/api/chat"; echo; }
fail() { echo "FAIL: $1"; exit 1; }

curl -sf "$B/health" | grep -q '"ok":true' || fail health
curl -sf -o /dev/null "$B/" || fail index
curl -sf -o /dev/null "$B/static/sample-bill.svg" || fail svg
curl -sf "$B/api/lab" | grep -q '"part":"Ledger"' || fail lab
curl -sf -c "$J" -X POST "$B/api/demo-login" >/dev/null || fail login
curl -sf -b "$J" "$B/api/ledger" | grep -q '"overdue_count":1' || fail seed_overdue
chat "ਸੀਤਾ ਨੂੰ 300 ਉਧਾਰ" | grep -q '"buttons":\["yes","no"\]' || fail confirm_pa
chat "ਹਾਂ" | grep -q 'ਸੇਵ' || fail save_pa
chat "Ramesh ka number 98765 43210" | grep -q '919876543210' || fail phone
chat "remind" | grep -q 'wa.me/919876543210' || fail remind
curl -sf -b "$J" -X POST "$B/api/bill/sample" | grep -q '846.50' || fail bill
chat "Kamla" >/dev/null; chat "credit" >/dev/null
chat "yes" | grep -q 'Saved' || fail bill_save
chat "undo" | grep -q 'Undone' || fail undo
curl -sf -b "$J" -F "file=@/dev/null;filename=n.webm" "$B/api/voice" | grep -q 'demo mode\|Empty' || fail voice
curl -sf -b "$J" -o /dev/null "$B/api/export.xlsx" || fail export
[ "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$B/webhook/whatsapp" -d '{}')" != "200" ] || fail webhook_unsigned
echo "SMOKE OK"
