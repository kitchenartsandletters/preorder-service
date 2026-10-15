"""
Tests for jobs/pub_date_transition.py — the date-driven reclassification sweep.

Classification depends on date.today() at the moment it runs, and nothing
re-evaluates a product because the calendar moved. This sweep is what closes
that gap, so its SELECT is the thing worth pinning.

Before this change it selected only active_preorder and early_stock_arrival.
Any product that entered an anomaly_* status before its pub date passed was
never swept again — which is how "Pasta Posters" and "Memories of a Good Meal"
sat in anomaly_missing_collection for nine days after their pub date, when a
fresh classification returns historical_preorder.
"""

from datetime import date, timedelta

import pytest

from jobs.pub_date_transition import SETTLED_STATUSES, fetch_stale_preorders, run

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
NEXT_MONTH = (date.today() + timedelta(days=30)).isoformat()


def seed(sb, rows):
    sb.seed("preorder", "product_status", rows)


@pytest.mark.asyncio
async def test_sweeps_anomalies_as_well_as_active(fake_supabase):
    seed(fake_supabase, [
        {"product_id": 1, "status": "active_preorder", "effective_pub_date": YESTERDAY},
        {"product_id": 2, "status": "early_stock_arrival", "effective_pub_date": YESTERDAY},
        {"product_id": 3, "status": "anomaly_missing_collection", "effective_pub_date": YESTERDAY},
        {"product_id": 4, "status": "anomaly_override_conflict", "effective_pub_date": YESTERDAY},
        {"product_id": 5, "status": "anomaly_stale_collection", "effective_pub_date": YESTERDAY},
        {"product_id": 6, "status": "anomaly_missing_tag", "effective_pub_date": YESTERDAY},
    ])

    assert sorted(await fetch_stale_preorders(fake_supabase, limit=200)) == [1, 2, 3, 4, 5, 6]


@pytest.mark.asyncio
async def test_settled_statuses_are_never_swept(fake_supabase):
    seed(fake_supabase, [
        {"product_id": 10, "status": "historical_preorder", "effective_pub_date": YESTERDAY},
        {"product_id": 11, "status": "not_a_preorder_product", "effective_pub_date": YESTERDAY},
        {"product_id": 12, "status": "active_preorder", "effective_pub_date": YESTERDAY},
    ])

    assert await fetch_stale_preorders(fake_supabase, limit=200) == [12]
    assert SETTLED_STATUSES == ["historical_preorder", "not_a_preorder_product"]


@pytest.mark.asyncio
async def test_pub_day_is_included_and_future_dates_are_not(fake_supabase):
    seed(fake_supabase, [
        {"product_id": 20, "status": "active_preorder", "effective_pub_date": TODAY},
        {"product_id": 21, "status": "anomaly_missing_collection", "effective_pub_date": TODAY},
        {"product_id": 22, "status": "active_preorder", "effective_pub_date": NEXT_MONTH},
        {"product_id": 23, "status": "active_preorder", "effective_pub_date": None},
    ])

    assert sorted(await fetch_stale_preorders(fake_supabase, limit=200)) == [20, 21]


@pytest.mark.asyncio
async def test_limit_is_respected(fake_supabase):
    seed(fake_supabase, [
        {"product_id": i, "status": "anomaly_override_conflict", "effective_pub_date": YESTERDAY}
        for i in range(30, 40)
    ])

    assert len(await fetch_stale_preorders(fake_supabase, limit=4)) == 4


@pytest.mark.asyncio
async def test_dry_run_reports_candidates_without_reclassifying(fake_supabase, monkeypatch):
    import jobs.pub_date_transition as job

    seed(fake_supabase, [
        {"product_id": 50, "status": "anomaly_missing_collection", "effective_pub_date": YESTERDAY},
    ])
    monkeypatch.setattr(job, "get_supabase_client", lambda: fake_supabase)

    closed = {"n": 0}

    class FakeShopify:
        async def close(self):
            closed["n"] += 1

    monkeypatch.setattr(job, "ShopifyClient", FakeShopify)

    summary = await run(limit=200, dry_run=True)

    assert summary["stale_found"] == 1
    assert summary["reclassified"] == 0
    assert summary["errors"] == []
    assert closed["n"] == 1  # the Shopify client is always closed


@pytest.mark.asyncio
async def test_one_failure_does_not_stop_the_sweep(fake_supabase, monkeypatch):
    import jobs.pub_date_transition as job
    import services.reclassification_service as svc

    seed(fake_supabase, [
        {"product_id": 60, "status": "anomaly_override_conflict", "effective_pub_date": YESTERDAY},
        {"product_id": 61, "status": "anomaly_missing_collection", "effective_pub_date": YESTERDAY},
        {"product_id": 62, "status": "active_preorder", "effective_pub_date": YESTERDAY},
    ])
    monkeypatch.setattr(job, "get_supabase_client", lambda: fake_supabase)

    class FakeShopify:
        async def close(self):
            pass

    monkeypatch.setattr(job, "ShopifyClient", FakeShopify)

    seen = []

    async def fake_reclassify(*, supabase, shopify_client, product_id):
        seen.append(product_id)
        if product_id == 61:
            raise RuntimeError("Shopify timeout")
        return {"product_id": product_id, "status": "historical_preorder"}

    monkeypatch.setattr(svc, "reclassify_single_product", fake_reclassify)

    summary = await run(limit=200, dry_run=False)

    assert sorted(seen) == [60, 61, 62]
    assert summary["stale_found"] == 3
    assert summary["reclassified"] == 2
    assert [e["product_id"] for e in summary["errors"]] == [61]
    assert "Shopify timeout" in summary["errors"][0]["error"]
