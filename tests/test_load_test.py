"""Tests for scripts/load_test.py, including a run against a throwaway local web server."""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

import load_test
from load_test import burst_passed, percentile, run_phase, send_one, steady_passed, summarize


def test_percentile_uses_nearest_rank():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert percentile(values, 50) == 5
    assert percentile(values, 95) == 10
    assert percentile(values, 100) == 10
    assert percentile([7], 99) == 7
    assert percentile([], 50) == 0.0


def test_summary_counts_each_kind_of_outcome_and_only_times_the_stored_requests():
    results = [(201, 0.1), (201, 0.3), (429, 0.01), (500, 0.5), (0, 1.0), (201, 0.2)]
    summary = summarize(results, elapsed=2.0)

    assert summary["total"] == 6
    assert summary["stored"] == 3
    assert summary["throttled"] == 1
    assert summary["server_errors"] == 1
    assert summary["network_errors"] == 1
    assert summary["throughput"] == 3.0
    assert summary["stored_max"] == 0.3          # the 0.5 s 500 and 1.0 s error are not "stored" latency
    assert summary["stored_p50"] == 0.2


def test_steady_passes_only_with_no_server_errors_and_at_least_95_percent_stored():
    good = summarize([(201, 0.1)] * 96 + [(429, 0.01)] * 4, 1)
    too_many_refused = summarize([(201, 0.1)] * 90 + [(429, 0.01)] * 10, 1)
    one_crash = summarize([(201, 0.1)] * 99 + [(500, 0.1)], 1)
    assert steady_passed(good)
    assert not steady_passed(too_many_refused)
    assert not steady_passed(one_crash)


def test_burst_passes_when_the_overload_is_shed_without_server_errors():
    shed = summarize([(201, 0.1)] * 30 + [(429, 0.01)] * 170, 1)
    broke = summarize([(201, 0.1)] * 30 + [(429, 0.01)] * 160 + [(502, 0.1)] * 10, 1)
    assert burst_passed(shed)
    assert not burst_passed(broke)


class FakeApi(BaseHTTPRequestHandler):
    """Accepts the first `allowed` requests (201), then answers 429, like a throttled API."""
    allowed = 5
    seen = 0
    lock = threading.Lock()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        with FakeApi.lock:
            FakeApi.seen += 1
            ok = FakeApi.seen <= FakeApi.allowed
        self.send_response(201 if ok else 429)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_url():
    FakeApi.seen = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/events"
    server.shutdown()


def test_a_burst_against_a_throttling_server_is_counted_correctly(fake_url):
    FakeApi.allowed = 5
    session = requests.Session()
    summary = run_phase(lambda: send_one(session, fake_url), total=40, concurrency=10)

    assert summary["total"] == 40
    assert summary["stored"] == 5
    assert summary["throttled"] == 35
    assert summary["server_errors"] == 0


def test_paced_requests_are_spread_out_in_time(fake_url):
    FakeApi.allowed = 10_000
    session = requests.Session()
    started = time.perf_counter()
    summary = run_phase(lambda: send_one(session, fake_url), total=10, concurrency=5, rps=20)
    elapsed = time.perf_counter() - started

    assert summary["stored"] == 10
    assert elapsed >= 0.4   # 10 requests at 20/s cannot finish in under (10 - 1) / 20 = 0.45 s


def test_an_unreachable_server_counts_as_network_errors():
    session = requests.Session()
    summary = run_phase(lambda: send_one(session, "http://127.0.0.1:1/events"), total=3, concurrency=3)
    assert summary["network_errors"] == 3
    assert not burst_passed(summary)


def test_the_safety_cap_protects_the_daily_quota():
    assert load_test.MAX_TOTAL_REQUESTS < 10_000
