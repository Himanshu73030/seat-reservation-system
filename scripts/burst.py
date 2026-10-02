import argparse
import asyncio
import os
import re
import sys
from collections import Counter
from typing import Any
from uuid import uuid4

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Exercise reservation contention against a running service.")
    parser.add_argument("base_url", nargs="?", default=os.getenv("BASE_URL", "http://localhost:8000"))
    parser.add_argument("--requests", type=int, default=500, help="Measured reservation requests (minimum 100).")
    parser.add_argument("--concurrency", type=int, default=200, help="Maximum simultaneous HTTP requests.")
    parser.add_argument("--admin-token", default=os.getenv("ADMIN_TOKEN", "local-admin-change-me"))
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if args.requests < 100 or args.concurrency < 1:
        parser.error("--requests must be >= 100 and --concurrency must be >= 1")
    return args


def reason_for(response: httpx.Response) -> str:
    if response.status_code == 201:
        return "confirmed"
    try:
        return response.json().get("reason", response.json().get("detail", "unknown_decline"))
    except ValueError:
        return "non_json_response"


def metric_value(metrics: str, name: str, label: str | None = None) -> float | None:
    label_part = rf'\{{reason="{re.escape(label)}"\}}' if label is not None else ""
    match = re.search(rf"^{re.escape(name)}{label_part}\s+([0-9]+(?:\.[0-9]+)?)$", metrics, re.MULTILINE)
    return float(match.group(1)) if match else None


async def run() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    run_id = uuid4().hex
    timeout = httpx.Timeout(args.timeout, connect=min(args.timeout, 10))
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=min(args.concurrency, 500))
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        ready = await client.get(f"{base_url}/readyz")
        ready.raise_for_status()
        baseline_response = await client.get(f"{base_url}/metrics")
        baseline_response.raise_for_status()
        baseline_metrics = baseline_response.text
        hot_count = args.requests * 8 // 10
        retry_count = args.requests // 10
        limit_count = args.requests - hot_count - retry_count
        retry_seed_count = 10
        limit_seat_count = max(10, limit_count)
        seats = ["HOT-A", "HOT-B"]
        seats.extend(f"RETRY-{index}" for index in range(retry_seed_count))
        seats.extend(f"LIMIT-{index}" for index in range(limit_seat_count))
        seats.append("MISMATCH")
        created = await client.post(
            f"{base_url}/shows",
            headers={"Authorization": f"Bearer {args.admin_token}"},
            json={"name": "automated-burst", "seats": seats, "price_paise": 25000},
        )
        created.raise_for_status()
        show_id = created.json()["id"]

        async def reserve_once(user: str, seat: str, key: str) -> httpx.Response:
            return await client.post(
                f"{base_url}/shows/{show_id}/reserve",
                headers={"Authorization": f"Bearer {user}", "Idempotency-Key": key},
                json={"seats": [seat]},
            )

        retry_keys = [f"{run_id}-retry-{index}" for index in range(retry_seed_count)]
        for index, key in enumerate(retry_keys):
            seeded = await reserve_once(f"{run_id}-retry-user-{index}", f"RETRY-{index}", key)
            if seeded.status_code != 201:
                raise RuntimeError(f"could not seed retry scenario: {seeded.status_code} {seeded.text}")

        mismatch_user = f"{run_id}-mismatch-user"
        mismatch_key = f"{run_id}-mismatch-key"
        mismatch_seed = await reserve_once(mismatch_user, "MISMATCH", mismatch_key)
        if mismatch_seed.status_code != 201:
            raise RuntimeError(f"could not seed payload-mismatch scenario: {mismatch_seed.status_code} {mismatch_seed.text}")
        mismatch = await client.post(
            f"{base_url}/shows/{show_id}/reserve",
            headers={"Authorization": f"Bearer {mismatch_user}", "Idempotency-Key": mismatch_key},
            json={"seats": ["HOT-A"]},
        )
        if mismatch.status_code != 409 or reason_for(mismatch) != "idempotency_key_reused":
            raise AssertionError(f"same-key/different-body check failed: {mismatch.status_code} {mismatch.text}")

        semaphore = asyncio.Semaphore(args.concurrency)

        async def bounded_reserve(user: str, seat: str, key: str) -> tuple[int, str]:
            async with semaphore:
                response = await reserve_once(user, seat, key)
                return response.status_code, reason_for(response)

        jobs: list[tuple[str, Any]] = []
        for index in range(hot_count):
            jobs.append(("hot", bounded_reserve(f"{run_id}-hot-user-{index}", "HOT-A" if index % 2 == 0 else "HOT-B", f"{run_id}-hot-{index}")))
        for index in range(retry_count):
            retry_index = index % retry_seed_count
            jobs.append(("retry", bounded_reserve(f"{run_id}-retry-user-{retry_index}", f"RETRY-{retry_index}", retry_keys[retry_index])))
        for index in range(limit_count):
            jobs.append(("limit", bounded_reserve(f"{run_id}-limit-user", f"LIMIT-{index}", f"{run_id}-limit-{index}")))

        results = await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)
        failures = [result for result in results if isinstance(result, BaseException)]
        status_counts: Counter[str] = Counter()
        reason_counts: Counter[str] = Counter()
        for result in results:
            if isinstance(result, BaseException):
                status_counts["transport_error"] += 1
                reason_counts[type(result).__name__] += 1
            else:
                response_status, reason = result
                status_counts[str(response_status)] += 1
                reason_counts[reason] += 1

        state_response = await client.get(f"{base_url}/shows/{show_id}")
        state_response.raise_for_status()
        state: dict[str, Any] = state_response.json()
        counts = state["counts"]
        invariant_ok = counts["available"] + counts["held"] + counts["confirmed"] == counts["total"]
        confirmed_hot = [seat for seat in state["seats"] if seat["seat_id"].startswith("HOT-") and seat["status"] == "confirmed"]
        limit_successes = sum(
            category == "limit" and result == (201, "confirmed")
            for (category, _), result in zip(jobs, results)
            if not isinstance(result, BaseException)
        )
        if limit_successes > 4:
            failures.append(AssertionError(f"per-user limit exceeded: {limit_successes}"))
        if len(confirmed_hot) != 2:
            failures.append(AssertionError(f"expected one winner per hot seat, got {len(confirmed_hot)}"))
        if not invariant_ok:
            failures.append(AssertionError(f"seat counts do not reconcile: {counts}"))
        if any(status.startswith("5") for status in status_counts):
            failures.append(AssertionError(f"server errors observed: {status_counts}"))

        metrics_response = await client.get(f"{base_url}/metrics")
        metrics_response.raise_for_status()
        confirmed_metric = metric_value(metrics_response.text, "reservations_confirmed_total")
        confirmed_baseline = metric_value(baseline_metrics, "reservations_confirmed_total") or 0
        expected_confirmed = (
            retry_seed_count
            + 1
            + sum(category == "hot" and result == (201, "confirmed") for (category, _), result in zip(jobs, results) if not isinstance(result, BaseException))
            + limit_successes
        )
        if confirmed_metric is None or confirmed_metric - confirmed_baseline != expected_confirmed:
            failures.append(
                AssertionError(
                    f"confirmed metric delta mismatch: expected {expected_confirmed}, "
                    f"got {None if confirmed_metric is None else confirmed_metric - confirmed_baseline}"
                )
            )
        seat_taken_expected = sum(
            category == "hot" and result == (409, "seat_taken")
            for (category, _), result in zip(jobs, results)
            if not isinstance(result, BaseException)
        )
        per_user_limit_expected = sum(
            category == "limit" and result == (409, "per_user_limit")
            for (category, _), result in zip(jobs, results)
            if not isinstance(result, BaseException)
        )
        seat_taken_metric = metric_value(metrics_response.text, "reservations_declined_total", "seat_taken") or 0
        seat_taken_baseline = metric_value(baseline_metrics, "reservations_declined_total", "seat_taken") or 0
        if seat_taken_metric - seat_taken_baseline != seat_taken_expected:
            failures.append(AssertionError("seat_taken metric does not match unique declined requests"))
        limit_metric = metric_value(metrics_response.text, "reservations_declined_total", "per_user_limit") or 0
        limit_baseline = metric_value(baseline_metrics, "reservations_declined_total", "per_user_limit") or 0
        if limit_metric - limit_baseline != per_user_limit_expected:
            failures.append(AssertionError("per_user_limit metric does not match unique declined requests"))
        replay_metric = metric_value(metrics_response.text, "idempotent_replays_total") or 0
        replay_baseline = metric_value(baseline_metrics, "idempotent_replays_total") or 0
        if replay_metric - replay_baseline != retry_count:
            failures.append(AssertionError("idempotent replay metric does not match replay requests"))
        mismatch_metric = metric_value(metrics_response.text, "idempotency_key_mismatches_total") or 0
        mismatch_baseline = metric_value(baseline_metrics, "idempotency_key_mismatches_total") or 0
        if mismatch_metric - mismatch_baseline != 1:
            failures.append(AssertionError("idempotency mismatch metric did not increment exactly once"))
        available_metric = re.search(
            rf'^seats_available\{{show_id="{re.escape(show_id)}"\}}\s+([0-9]+(?:\.[0-9]+)?)',
            metrics_response.text,
            re.MULTILINE,
        )
        if available_metric is None or float(available_metric.group(1)) != counts["available"]:
            failures.append(AssertionError("seats_available metric does not match the show API"))

        print(f"show_id={show_id}")
        print(f"requests={len(jobs)} concurrency_limit={args.concurrency}")
        print(f"status_counts={dict(status_counts)}")
        print(f"outcome_counts={dict(reason_counts)}")
        print(f"seat_counts={counts}")
        print(f"hot_seat_winners={len(confirmed_hot)} limit_user_confirmed={limit_successes}")
        print(f"api_metric_reconciliation={'PASS' if not failures else 'FAIL'}")
        if failures:
            for failure in failures[:10]:
                print(f"failure={type(failure).__name__}: {failure}", file=sys.stderr)
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))