#!/bin/bash
# Test the Memory Lane ingest endpoint on maybelle
#
# Usage:
#   ./maybelle/scripts/test-ingest-endpoint.sh

ENDPOINT="https://memory-lane.maybelle.cryptograss.live/api/ingest/"

echo "Testing ingest endpoint: $ENDPOINT"
echo ""

# Generate a proper UUID
TEST_UUID=$(python3 -c "import uuid; print(uuid.uuid4())")
SESSION_UUID=$(python3 -c "import uuid; print(uuid.uuid4())")

# Create a valid test JSONL line
TEST_LINE=$(cat <<EOF
{"parentUuid":null,"type":"user","message":{"role":"user","content":"Test message from ingest script"},"uuid":"${TEST_UUID}","timestamp":"2025-11-28T20:00:00.000Z","sessionId":"${SESSION_UUID}"}
EOF
)

echo "Sending test line..."
curl -s -X POST "$ENDPOINT" \
  -H "Content-Type: application/json" \
  -d "{\"line\": $(echo "$TEST_LINE" | jq -Rs .), \"username\": \"justin\", \"source\": \"test-script\"}" | jq .

echo ""
echo "Done."
