#!/bin/bash
# Test the Memory Lane ingest endpoint on maybelle
#
# Usage:
#   ./maybelle/scripts/test-ingest-endpoint.sh

ENDPOINT="https://memory-lane.maybelle.cryptograss.live/api/ingest/"

echo "Testing ingest endpoint: $ENDPOINT"
echo ""

# Create a valid test JSONL line
TEST_LINE=$(cat <<'EOF'
{"parentUuid":null,"type":"user","message":{"role":"user","content":"Test message from ingest script"},"uuid":"test-0000-1111-2222-333344445555","timestamp":"2025-11-28T20:00:00.000Z","sessionId":"test-session-001"}
EOF
)

echo "Sending test line..."
curl -s -X POST "$ENDPOINT" \
  -H "Content-Type: application/json" \
  -d "{\"line\": $(echo "$TEST_LINE" | jq -Rs .), \"username\": \"justin\", \"source\": \"test-script\"}" | jq .

echo ""
echo "Done."
