#!/usr/bin/env python3
"""
Delivery Kid Uptime Monitor

Tests all public endpoints on delivery-kid.cryptograss.live.
Designed to run on Jenkins as a periodic health check.

Exit codes:
  0 - All checks passed
  1 - One or more checks failed

Usage:
  ./test-delivery-kid.py              # Run all tests
  ./test-delivery-kid.py --verbose    # Show detailed output
  ./test-delivery-kid.py --json       # Output JSON for parsing
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


BASE_URL = "https://delivery-kid.cryptograss.live"
IPFS_GATEWAY = "https://ipfs.delivery-kid.cryptograss.live"

# Known good CID for testing IPFS gateway (hello world)
TEST_CID = "QmT78zSuBmuS4z925WZfrqQ1qHaJ56DQaTfyMUF7F8ff5o"
TEST_CID_CONTENT = "hello world\n"


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    response_time_ms: Optional[float] = None
    details: Optional[dict] = None


def check_endpoint(name: str, url: str, expected_keys: list[str] = None,
                   expected_content: str = None, timeout: float = 10.0) -> CheckResult:
    """Check an HTTP endpoint."""
    start = time.time()

    try:
        req = Request(url, headers={"User-Agent": "delivery-kid-monitor/1.0"})
        with urlopen(req, timeout=timeout) as response:
            elapsed_ms = (time.time() - start) * 1000
            body = response.read().decode("utf-8")

            # Check for expected content (exact match)
            if expected_content is not None:
                if body == expected_content:
                    return CheckResult(
                        name=name,
                        passed=True,
                        message="Content matches",
                        response_time_ms=elapsed_ms
                    )
                else:
                    return CheckResult(
                        name=name,
                        passed=False,
                        message=f"Content mismatch: got {repr(body[:100])}",
                        response_time_ms=elapsed_ms
                    )

            # Check for expected JSON keys
            if expected_keys:
                try:
                    data = json.loads(body)
                    missing = [k for k in expected_keys if k not in data]
                    if missing:
                        return CheckResult(
                            name=name,
                            passed=False,
                            message=f"Missing keys: {missing}",
                            response_time_ms=elapsed_ms,
                            details=data
                        )
                    return CheckResult(
                        name=name,
                        passed=True,
                        message="OK",
                        response_time_ms=elapsed_ms,
                        details=data
                    )
                except json.JSONDecodeError as e:
                    return CheckResult(
                        name=name,
                        passed=False,
                        message=f"Invalid JSON: {e}",
                        response_time_ms=elapsed_ms
                    )

            # No specific checks, just verify 200 response
            return CheckResult(
                name=name,
                passed=True,
                message="OK",
                response_time_ms=elapsed_ms
            )

    except HTTPError as e:
        elapsed_ms = (time.time() - start) * 1000
        return CheckResult(
            name=name,
            passed=False,
            message=f"HTTP {e.code}: {e.reason}",
            response_time_ms=elapsed_ms
        )
    except URLError as e:
        elapsed_ms = (time.time() - start) * 1000
        return CheckResult(
            name=name,
            passed=False,
            message=f"Connection failed: {e.reason}",
            response_time_ms=elapsed_ms
        )
    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        return CheckResult(
            name=name,
            passed=False,
            message=f"Error: {e}",
            response_time_ms=elapsed_ms
        )


def check_time_drift(time_response: dict, max_drift_ms: int = 30000) -> CheckResult:
    """Check that server time is within acceptable drift of local time."""
    server_time = time_response.get("timestamp") or time_response.get("time")
    if not server_time:
        return CheckResult(
            name="time_drift",
            passed=False,
            message="No timestamp in response"
        )

    local_time = int(time.time() * 1000)
    drift_ms = abs(local_time - server_time)

    if drift_ms > max_drift_ms:
        return CheckResult(
            name="time_drift",
            passed=False,
            message=f"Server time drift too high: {drift_ms}ms (max: {max_drift_ms}ms)",
            details={"server_time": server_time, "local_time": local_time, "drift_ms": drift_ms}
        )

    return CheckResult(
        name="time_drift",
        passed=True,
        message=f"Time drift OK: {drift_ms}ms",
        details={"drift_ms": drift_ms}
    )


def run_all_checks() -> list[CheckResult]:
    """Run all health checks."""
    results = []

    # Pinning service health
    results.append(check_endpoint(
        "pinning_health",
        f"{BASE_URL}/health",
        expected_keys=["status"]
    ))

    # Pinning service version
    version_result = check_endpoint(
        "pinning_version",
        f"{BASE_URL}/version",
        expected_keys=["commit"]
    )
    results.append(version_result)

    # Pinning service time (check for either 'time' or 'timestamp' key)
    time_result = check_endpoint(
        "pinning_time",
        f"{BASE_URL}/time",
        expected_keys=["timestamp"]  # Node.js uses 'timestamp', FastAPI uses 'time'
    )
    # Retry with 'time' key if 'timestamp' not found
    if not time_result.passed and "Missing keys" in time_result.message:
        time_result = check_endpoint(
            "pinning_time",
            f"{BASE_URL}/time",
            expected_keys=["time"]
        )
    results.append(time_result)

    # Check time drift if we got a response
    if time_result.passed and time_result.details:
        results.append(check_time_drift(time_result.details))

    # IPFS gateway - fetch known content
    results.append(check_endpoint(
        "ipfs_gateway",
        f"{IPFS_GATEWAY}/ipfs/{TEST_CID}",
        expected_content=TEST_CID_CONTENT
    ))

    return results


def print_results(results: list[CheckResult], verbose: bool = False):
    """Print results in human-readable format."""
    passed = sum(1 for r in results if r.passed)
    total = len(results)

    print(f"\n{'=' * 60}")
    print(f"DELIVERY-KID HEALTH CHECK")
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

    if passed == total:
        print("Status: ALL SYSTEMS OPERATIONAL")
    else:
        print("Status: DEGRADED")
    print(f"{'=' * 60}\n")


def main():
    parser = argparse.ArgumentParser(description="Delivery Kid uptime monitor")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    results = run_all_checks()

    if args.json:
        output = {
            "timestamp": int(time.time() * 1000),
            "checks": [asdict(r) for r in results],
            "passed": sum(1 for r in results if r.passed),
            "total": len(results),
            "all_passed": all(r.passed for r in results)
        }
        print(json.dumps(output, indent=2))
    else:
        print_results(results, verbose=args.verbose)

    # Exit with failure if any check failed
    if not all(r.passed for r in results):
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
