#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def parse_header(value: str) -> tuple[str, str]:
    name, separator, header_value = value.partition(":")
    if not separator or not name.strip():
        raise argparse.ArgumentTypeError("headers must use NAME: VALUE")
    return name.strip(), header_value.strip()


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]


def request_once(
    url: str,
    method: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, float, str | None]:
    started = time.perf_counter()
    request = urllib.request.Request(url=url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1024)
            status_code = response.status
        return status_code, (time.perf_counter() - started) * 1000, None
    except urllib.error.HTTPError as exc:
        exc.read(1024)
        return exc.code, (time.perf_counter() - started) * 1000, None
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return 0, (time.perf_counter() - started) * 1000, type(exc).__name__


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bounded HTTP load probe for local/staging production validation."
    )
    parser.add_argument("url")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--method", default="GET")
    parser.add_argument("--header", action="append", default=[], type=parse_header)
    parser.add_argument("--json-file", type=Path)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    if args.requests < 1 or args.requests > 100_000:
        parser.error("--requests must be between 1 and 100000")
    if args.concurrency < 1 or args.concurrency > 1000:
        parser.error("--concurrency must be between 1 and 1000")
    if args.timeout <= 0 or args.timeout > 300:
        parser.error("--timeout must be greater than zero and at most 300 seconds")

    method = args.method.strip().upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        parser.error("unsupported HTTP method")

    headers = dict(args.header)
    body: bytes | None = None
    if args.json_file is not None:
        raw = args.json_file.read_bytes()
        if len(raw) > 1_000_000:
            parser.error("--json-file must not exceed 1 MiB")
        try:
            json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            parser.error(f"--json-file is not valid JSON: {exc}")
        body = raw
        headers.setdefault("Content-Type", "application/json")

    started = time.perf_counter()
    statuses: Counter[int] = Counter()
    errors: Counter[str] = Counter()
    latencies: list[float] = []

    with ThreadPoolExecutor(max_workers=min(args.concurrency, args.requests)) as executor:
        futures = [
            executor.submit(request_once, args.url, method, headers, body, args.timeout)
            for _ in range(args.requests)
        ]
        for future in as_completed(futures):
            status_code, latency_ms, error = future.result()
            latencies.append(latency_ms)
            if error is None:
                statuses[status_code] += 1
            else:
                errors[error] += 1

    elapsed = max(time.perf_counter() - started, 0.000001)
    output = {
        "requests": args.requests,
        "concurrency": args.concurrency,
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(args.requests / elapsed, 2),
        "status_counts": {str(key): value for key, value in sorted(statuses.items())},
        "network_errors": dict(sorted(errors.items())),
        "latency_ms": {
            "p50": round(percentile(latencies, 0.50), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "p99": round(percentile(latencies, 0.99), 2),
            "max": round(max(latencies, default=0.0), 2),
        },
    }
    print(json.dumps(output, sort_keys=True))

    if errors or any(status_code >= 500 for status_code in statuses):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
