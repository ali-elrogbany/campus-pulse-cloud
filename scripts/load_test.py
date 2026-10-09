#!/usr/bin/env python3
"""Load test for POST /events: how does CampusPulse behave under pressure?

Two phases, because they answer two different questions:

  1. STEADY: traffic just under the API's rate limit (default 8 requests/s, limit is 10/s).
     Question: is the system fast and error-free when used as designed?
     Pass: no server errors (5xx) and at least 95% of requests stored (HTTP 201).

  2. BURST: many requests at once, far above the rate limit.
     Question: when overloaded, does the system protect itself instead of falling over?
     Pass: no server errors (5xx). Excess requests should be refused quickly with HTTP 429
     ("Too Many Requests"), which means API Gateway shed the load before it reached Lambda/DynamoDB.

Every request uses a fresh simulated event, so successful ones are really stored. The default
run sends 500 requests, a small share of the 10,000/day quota in the usage plan.

Usage:
    export CAMPUSPULSE_URL=...  CAMPUSPULSE_API_KEY=...
    python scripts/load_test.py
"""
import argparse
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "simulator"))
from simulator import generate_event  # noqa: E402  (the same event generator the simulator uses)

MAX_TOTAL_REQUESTS = 2000  # protects the 10,000/day quota and your Free Tier
STEADY_MIN_SUCCESS = 0.95


def percentile(sorted_values: list, p: float) -> float:
    """Nearest-rank percentile of an already sorted list (p between 0 and 100)."""
    if not sorted_values:
        return 0.0
    rank = max(1, int(-(-p * len(sorted_values) // 100)))  # ceiling without importing math
    return sorted_values[min(rank, len(sorted_values)) - 1]


def send_one(session: requests.Session, url: str):
    """Send one fresh event. Returns (status code, seconds). Status 0 means a network error."""
    started = time.perf_counter()
    try:
        status = session.post(url, json=generate_event(0.1), timeout=15).status_code
    except requests.RequestException:
        status = 0
    return status, time.perf_counter() - started


def run_phase(send, total: int, concurrency: int, rps: float = None) -> dict:
    """Send `total` requests with up to `concurrency` at once.

    With `rps` the requests are released evenly, one every 1/rps seconds. Without it they are
    all released immediately (a burst). `send` is a function returning (status, seconds).
    """
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = []
        for number in range(total):
            if rps:
                delay = started + number / rps - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
            futures.append(pool.submit(send))
        results = [future.result() for future in futures]
    return summarize(results, time.perf_counter() - started)


def summarize(results: list, elapsed: float) -> dict:
    statuses = Counter(status for status, _ in results)
    stored = sorted(seconds for status, seconds in results if status == 201)
    refused = sorted(seconds for status, seconds in results if status == 429)
    total = len(results)
    return {
        "total": total,
        "elapsed": elapsed,
        "throughput": total / elapsed if elapsed > 0 else 0.0,
        "statuses": dict(sorted(statuses.items())),
        "stored": statuses.get(201, 0),
        "throttled": statuses.get(429, 0),
        "server_errors": sum(count for status, count in statuses.items() if 500 <= status <= 599),
        "network_errors": statuses.get(0, 0),
        "stored_p50": percentile(stored, 50),
        "stored_p95": percentile(stored, 95),
        "stored_p99": percentile(stored, 99),
        "stored_max": stored[-1] if stored else 0.0,
        "throttled_p50": percentile(refused, 50),
    }


def steady_passed(summary: dict) -> bool:
    return (summary["total"] > 0 and summary["server_errors"] == 0 and summary["network_errors"] == 0
            and summary["stored"] / summary["total"] >= STEADY_MIN_SUCCESS)


def burst_passed(summary: dict) -> bool:
    return summary["total"] > 0 and summary["server_errors"] == 0 and summary["network_errors"] == 0


def ms(seconds: float) -> str:
    return f"{seconds * 1000:.0f} ms"


def print_summary(title: str, summary: dict, passed: bool) -> None:
    print(f"\n{title}: {'PASSED' if passed else 'FAILED'}")
    print(f"  requests sent:      {summary['total']} in {summary['elapsed']:.1f} s ({summary['throughput']:.1f} requests/s)")
    for status, count in summary["statuses"].items():
        label = {201: "stored", 429: "throttled (refused by API Gateway)", 0: "network error"}.get(status, "")
        print(f"  HTTP {status or '---'}:           {count:>4}  {label}")
    print(f"  server errors 5xx:  {summary['server_errors']}")
    if summary["stored"]:
        print(f"  latency (stored):   p50 {ms(summary['stored_p50'])}   p95 {ms(summary['stored_p95'])}"
              f"   p99 {ms(summary['stored_p99'])}   max {ms(summary['stored_max'])}")
    if summary["throttled"]:
        print(f"  latency (refused):  p50 {ms(summary['throttled_p50'])}  (rejected before reaching Lambda)")


def main() -> int:
    parser = argparse.ArgumentParser(description="CampusPulse load test")
    parser.add_argument("--url", default=os.environ.get("CAMPUSPULSE_URL"), help="API base URL")
    parser.add_argument("--api-key", default=os.environ.get("CAMPUSPULSE_API_KEY"), help="API key")
    parser.add_argument("--steady-count", type=int, default=300)
    parser.add_argument("--steady-rps", type=float, default=8.0, help="requests per second (limit is 10)")
    parser.add_argument("--burst-count", type=int, default=200)
    parser.add_argument("--burst-concurrency", type=int, default=25)
    parser.add_argument("--skip-burst", action="store_true")
    args = parser.parse_args()

    if not args.url or not args.api_key:
        parser.error("set --url and --api-key (or CAMPUSPULSE_URL and CAMPUSPULSE_API_KEY)")
    planned = args.steady_count + (0 if args.skip_burst else args.burst_count)
    if planned > MAX_TOTAL_REQUESTS:
        parser.error(f"{planned} requests is more than the safety limit of {MAX_TOTAL_REQUESTS}")

    url = args.url.rstrip("/")
    if not url.endswith("/events"):
        url += "/events"
    session = requests.Session()
    session.headers.update({"x-api-key": args.api_key})
    send = lambda: send_one(session, url)  # noqa: E731

    print(f"Phase 1/2 STEADY: {args.steady_count} requests at {args.steady_rps:g} per second ...", flush=True)
    steady = run_phase(send, args.steady_count, concurrency=20, rps=args.steady_rps)
    steady_ok = steady_passed(steady)
    print_summary("STEADY LOAD", steady, steady_ok)

    burst_ok = True
    if not args.skip_burst:
        print(f"\nPhase 2/2 BURST: {args.burst_count} requests, {args.burst_concurrency} at a time, no pacing ...", flush=True)
        burst = run_phase(send, args.burst_count, concurrency=args.burst_concurrency)
        burst_ok = burst_passed(burst)
        print_summary("BURST (OVERLOAD)", burst, burst_ok)

    print("\nNow look at the dashboard: API requests, 4XX errors (the 429s) and Lambda duration. "
          "5XX should be flat at zero.")
    return 0 if steady_ok and burst_ok else 1


if __name__ == "__main__":
    sys.exit(main())
