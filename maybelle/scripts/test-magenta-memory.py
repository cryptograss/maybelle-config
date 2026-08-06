#!/usr/bin/env python3
"""
Magenta Memory Uptime Monitor

Checks the memory stack that magent bootstraps from on every fresh session:
the MCP query server, Memory Lane's read API, and whether the watcher is
still ingesting.

This exists because the MCP server went down and stayed down through four
consecutive wakeups without anyone noticing. Nothing watched it, and its
failure is silent — magent just quietly bootstraps degraded.

Exit codes:
  0 - All checks passed
  1 - One or more checks failed

Usage:
  ./test-magenta-memory.py              # Run all tests
  ./test-magenta-memory.py --verbose    # Show detailed output
  ./test-magenta-memory.py --json       # Output JSON for Jenkins to parse
"""

import argparse
import json
import socket
import sys
import time
from dataclasses import dataclass, asdict
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


# maybelle over the Hetzner private network.
MEMORY_HOST = "10.0.0.2"
MCP_PORT = 8000
MEMORY_LANE_URL = f"http://{MEMORY_HOST}:3000"

# How stale ingest may get before we call it a problem.
#
# Deliberately generous. The watcher only has something to ingest when someone
# is actually talking to Claude, so a quiet long weekend is normal and a tight
# threshold would page us for silence rather than for breakage. Three days
# still catches a watcher that died and nobody noticed, which is the failure
# this is here to find.
DEFAULT_MAX_INGEST_AGE_HOURS = 72


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    response_time_ms: Optional[float] = None
    details: Optional[dict] = None


def check_tcp(name: str, host: str, port: int, timeout: float = 5.0) -> CheckResult:
    """Check that something is listening.

    The MCP server's observed failure is ConnectionRefused — the container is
    not running at all — so a plain connect is the signal that matters. It
    speaks MCP rather than plain HTTP, and asking it for a page would tell us
    less than asking whether it answers the door.
    """
    start = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed_ms = (time.time() - start) * 1000
            return CheckResult(
                name=name,
                passed=True,
                message=f"listening on {host}:{port}",
                response_time_ms=elapsed_ms,
            )
    except ConnectionRefusedError:
        return CheckResult(
            name=name,
            passed=False,
            message=(
                f"connection refused on {host}:{port} — the container is not "
                f"running. Check 'docker logs mcp-server'."
            ),
            response_time_ms=(time.time() - start) * 1000,
        )
    except (socket.timeout, OSError) as e:
        return CheckResult(
            name=name,
            passed=False,
            message=f"cannot reach {host}:{port}: {e}",
            response_time_ms=(time.time() - start) * 1000,
        )


def check_json_endpoint(name: str, url: str, expected_keys: list[str] = None,
                        timeout: float = 15.0) -> CheckResult:
    """Fetch a JSON endpoint and optionally assert some top-level keys."""
    start = time.time()
    try:
        req = Request(url, headers={"User-Agent": "magenta-memory-monitor/1.0"})
        with urlopen(req, timeout=timeout) as response:
            elapsed_ms = (time.time() - start) * 1000
            body = response.read().decode("utf-8")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return CheckResult(
                name=name,
                passed=False,
                message=f"HTTP {response.status} but body is not JSON",
                response_time_ms=elapsed_ms,
            )

        if expected_keys:
            if not isinstance(data, dict):
                return CheckResult(
                    name=name,
                    passed=False,
                    message=f"expected an object, got {type(data).__name__}",
                    response_time_ms=elapsed_ms,
                )
            missing = [k for k in expected_keys if k not in data]
            if missing:
                return CheckResult(
                    name=name,
                    passed=False,
                    message=f"missing keys: {', '.join(missing)}",
                    response_time_ms=elapsed_ms,
                )

        return CheckResult(
            name=name,
            passed=True,
            message="ok",
            response_time_ms=elapsed_ms,
            details={"payload": data} if not expected_keys else None,
        )

    except HTTPError as e:
        elapsed_ms = (time.time() - start) * 1000
        hint = ""
        if e.code == 500:
            hint = (
                " — a 500 here usually means the Era/ContextHeap traversal is "
                "failing, which also takes the MCP server down"
            )
        return CheckResult(
            name=name,
            passed=False,
            message=f"HTTP {e.code}{hint}",
            response_time_ms=elapsed_ms,
        )
    except URLError as e:
        return CheckResult(
            name=name,
            passed=False,
            message=f"unreachable: {e.reason}",
            response_time_ms=(time.time() - start) * 1000,
        )
    except Exception as e:
        return CheckResult(
            name=name,
            passed=False,
            message=f"error: {e}",
            response_time_ms=(time.time() - start) * 1000,
        )


def check_ingest_freshness(max_age_hours: int) -> CheckResult:
    """Check that the watcher is still writing messages into the database.

    The watcher failing is the quietest failure in the stack: queries keep
    working, nothing errors, and the record just stops growing.
    """
    url = f"{MEMORY_LANE_URL}/api/recent_messages/?limit=1"
    start = time.time()

    try:
        req = Request(url, headers={"User-Agent": "magenta-memory-monitor/1.0"})
        with urlopen(req, timeout=15.0) as response:
            elapsed_ms = (time.time() - start) * 1000
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return CheckResult(
            name="ingest_freshness",
            passed=False,
            message=f"could not read recent messages: {e}",
            response_time_ms=(time.time() - start) * 1000,
        )

    messages = data.get("messages") if isinstance(data, dict) else data
    if not messages:
        return CheckResult(
            name="ingest_freshness",
            passed=False,
            message="no messages returned at all",
            response_time_ms=elapsed_ms,
        )

    newest_ms = max(m.get("timestamp", 0) for m in messages)
    if not newest_ms:
        return CheckResult(
            name="ingest_freshness",
            passed=False,
            message="newest message carries no timestamp",
            response_time_ms=elapsed_ms,
        )

    age_hours = (time.time() * 1000 - newest_ms) / 1000 / 3600
    passed = age_hours <= max_age_hours

    return CheckResult(
        name="ingest_freshness",
        passed=passed,
        message=(
            f"newest message is {age_hours:.1f}h old"
            + ("" if passed else f" (limit {max_age_hours}h — is the watcher running?)")
        ),
        response_time_ms=elapsed_ms,
        details={"age_hours": round(age_hours, 2), "newest_timestamp_ms": newest_ms},
    )


def run_all_checks(max_ingest_age_hours: int) -> list[CheckResult]:
    """Run all health checks."""
    results = []

    # The MCP query server — what magent actually bootstraps through.
    results.append(check_tcp("mcp_server", MEMORY_HOST, MCP_PORT))

    # Memory Lane's read API. recent_messages is the fallback bootstrap path,
    # so it failing means magent has no way back to its own history.
    results.append(check_json_endpoint(
        "memory_lane_recent",
        f"{MEMORY_LANE_URL}/api/recent_messages/?limit=5",
        expected_keys=["messages"],
    ))

    # Era and context-heap metadata. This is 500ing as of 2026-08-03; the
    # check is here so the recovery is noticed as well as the breakage.
    results.append(check_json_endpoint(
        "memory_lane_heaps",
        f"{MEMORY_LANE_URL}/api/heap_metadata/",
    ))

    results.append(check_ingest_freshness(max_ingest_age_hours))

    return results


def print_results(results: list[CheckResult], verbose: bool = False):
    """Print results in human-readable format."""
    passed = sum(1 for r in results if r.passed)
    total = len(results)

    print(f"\n{'=' * 60}")
    print("MAGENTA MEMORY HEALTH CHECK")
    print(f"{'=' * 60}\n")

    for result in results:
        status = "✓" if result.passed else "✗"
        time_str = f" ({result.response_time_ms:.0f}ms)" if result.response_time_ms else ""
        print(f"  {status} {result.name}: {result.message}{time_str}")

        if verbose and result.details:
            for key, value in result.details.items():
                print(f"      {key}: {value}")

    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} checks passed")
    print("Status: ALL SYSTEMS OPERATIONAL" if passed == total else "Status: DEGRADED")
    print(f"{'=' * 60}\n")


def main():
    parser = argparse.ArgumentParser(description="Magenta memory uptime monitor")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument(
        "--max-ingest-age-hours",
        type=int,
        default=DEFAULT_MAX_INGEST_AGE_HOURS,
        help=f"Fail if nothing has been ingested for this long (default {DEFAULT_MAX_INGEST_AGE_HOURS})",
    )
    args = parser.parse_args()

    results = run_all_checks(args.max_ingest_age_hours)

    if args.json:
        output = {
            "timestamp": int(time.time() * 1000),
            "checks": [asdict(r) for r in results],
            "passed": sum(1 for r in results if r.passed),
            "total": len(results),
            "all_passed": all(r.passed for r in results),
        }
        print(json.dumps(output, indent=2))
    else:
        print_results(results, verbose=args.verbose)

    sys.exit(0 if all(r.passed for r in results) else 1)


if __name__ == "__main__":
    main()
