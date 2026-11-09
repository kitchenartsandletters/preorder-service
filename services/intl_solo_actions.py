"""
services/intl_solo_actions.py

International cart isolation ("intl-solo"), PR 2: the write side.

Every action here changes two things: the decision of record
(preorder.intl_solo_state) and the Shopify tag `intl-solo` that the Cart Lock
checkout rule enforces. Each action is logged to preorder.intl_solo_log.

WRITE ORDER: fail toward restricting
The two writes cannot be atomic, so each action orders them so that a failure
part-way leaves the title RESTRICTED (the safe side), never unrestricted:

  restrict (restore, force_on, reconcile restrict):
      1. add the Shopify tag     2. record the decision
      Tag ok + DB fails  -> tagged, no decision: enforced, shows as drift.
  clear:
      1. record the decision     2. remove the Shopify tag
      DB ok + tag fails  -> decision says cleared, tag still on: enforced,
                            shows as drift; reconcile removes the tag.

A restriction that silently fails would let a mixed international order
through, which means a cancelled order. A clearance that silently fails only
costs a sale until someone looks.

TAG WRITES use tagsAdd / tagsRemove, which touch only the named tag. Never
productUpdate, which replaces the whole tag list and would wipe `preorder`
and every other tag. Both mutations are idempotent.

Validation is driven by intl_solo_service.ALLOWED_ACTIONS, the same table the
dashboard renders from, so the UI and the API cannot disagree about what is
allowed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from services import intl_solo_service as svc
from services.hold_service import today_et

logger = logging.getLogger(__name__)

STATE_TABLE = "intl_solo_state"
PRODUCT_STATUS_TABLE = "product_status"

CLEAR_BASES = ("received", "guaranteed")

# Reconcile plan actions.
PLAN_RESTRICT = "restrict"      # pending_restriction: record auto_preorder + add tag (the backfill)
PLAN_TAG_ADD = "tag_add"        # restricted but untagged
PLAN_TAG_REMOVE = "tag_remove"  # cleared but still tagged
PLAN_REPORT = "report_only"     # stray tag with no decision (e.g. test books): never auto-removed

# A typed confirmation is required to apply a reconcile, because applying it
# turns on live international enforcement for every restricted title.
APPLY_CONFIRMATION = "apply"


class IntlSoloActionError(Exception):
    """An action that is not allowed in the product's current state (HTTP 409)."""

    def __init__(self, message: str, display_state: Optional[str] = None):
        super().__init__(message)
        self.display_state = display_state


class IntlSoloShopifyError(Exception):
    """Shopify refused or failed a tag write (HTTP 502)."""

    def __init__(self, message: str, errors: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.errors = errors or []


# ──────────────────────────────────────────────
# Shopify tag writes
# ──────────────────────────────────────────────

TAGS_ADD_MUTATION = """
mutation IntlSoloTagsAdd($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}
"""

TAGS_REMOVE_MUTATION = """
mutation IntlSoloTagsRemove($id: ID!, $tags: [String!]!) {
  tagsRemove(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}
"""


def _gid(product_id: int) -> str:
    return f"gid://shopify/Product/{product_id}"


async def add_tag(client: Any, product_id: int) -> List[Dict[str, Any]]:
    """Add `intl-solo` to a product. Returns userErrors (empty on success)."""
    result = await client.graphql(
        query=TAGS_ADD_MUTATION,
        variables={"id": _gid(product_id), "tags": [svc.TAG]},
    )
    return (result.get("tagsAdd") or {}).get("userErrors") or []


async def remove_tag(client: Any, product_id: int) -> List[Dict[str, Any]]:
    """Remove `intl-solo` from a product. Returns userErrors (empty on success)."""
    result = await client.graphql(
        query=TAGS_REMOVE_MUTATION,
        variables={"id": _gid(product_id), "tags": [svc.TAG]},
    )
    return (result.get("tagsRemove") or {}).get("userErrors") or []


# ──────────────────────────────────────────────
# Supabase helpers
# ──────────────────────────────────────────────

def _now() -> str:
    return datetime.now(svc.ET).isoformat()


def _clean_reason(reason: Optional[str]) -> str:
    cleaned = (reason or "").strip()
    if not cleaned:
        raise ValueError("A reason is required.")
    return cleaned


def load_row(supabase, product_id: int) -> Optional[Dict[str, Any]]:
    """
    The product's vw_intl_solo_inputs row. A product the view leaves out (not a
    preorder, no decision, no tag) is rebuilt from product_status so force_on
    can still act on it. None when the product is unknown to the service.
    """
    resp = (
        supabase.schema(svc.SCHEMA)
        .table(svc.INPUTS_VIEW)
        .select("*")
        .eq("product_id", product_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if rows:
        return rows[0]

    resp = (
        supabase.schema(svc.SCHEMA)
        .table(PRODUCT_STATUS_TABLE)
        .select("product_id, status, effective_pub_date, metadata_snapshot, last_classified_at")
        .eq("product_id", product_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if not rows:
        return None
    ps = rows[0]
    snap = ps.get("metadata_snapshot") or {}
    tags = snap.get("tags") if isinstance(snap.get("tags"), list) else []
    return {
        "product_id": ps["product_id"],
        "title": snap.get("title"),
        "isbn": snap.get("isbn"),
        "classification": ps.get("status"),
        "pub_date": ps.get("effective_pub_date"),
        "in_preorder_collection": bool(snap.get("in_preorder_collection")),
        "intl_solo_tag_present": any(str(t).strip().lower() == svc.TAG for t in tags),
        "snapshot_at": ps.get("last_classified_at"),
        "first_positive_inventory_at": None,
        "state": None,
        "basis": None,
        "reason": None,
        "changed_by": None,
        "state_updated_at": None,
    }


def _write_state(supabase, product_id: int, state: str, basis: str,
                 reason: Optional[str], changed_by: Optional[str]) -> None:
    supabase.schema(svc.SCHEMA).table(STATE_TABLE).upsert(
        {
            "product_id": product_id,
            "state": state,
            "basis": basis,
            "reason": reason,
            "changed_by": changed_by,
            "updated_at": _now(),
        },
        on_conflict="product_id",
    ).execute()


def _log(supabase, product_id: int, action: str, *, from_state: Optional[str] = None,
         to_state: Optional[str] = None, basis: Optional[str] = None,
         reason: Optional[str] = None, changed_by: Optional[str] = None,
         success: bool = True, details: Optional[Dict[str, Any]] = None,
         errors: Optional[List[Dict[str, Any]]] = None) -> None:
    """Append to intl_solo_log. A logging failure never undoes the action."""
    try:
        supabase.schema(svc.SCHEMA).table(svc.LOG_TABLE).insert({
            "product_id": product_id,
            "action": action,
            "from_state": from_state,
            "to_state": to_state,
            "basis": basis,
            "reason": reason,
            "changed_by": changed_by,
            "success": success,
            "details": details or {},
            "errors": errors or [],
            "performed_at": _now(),
        }).execute()
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"[intl-solo] log insert failed for {product_id}: {exc}")


def _require(supabase, product_id: int, action: str) -> Dict[str, Any]:
    """Load and derive the product, and check `action` is allowed right now."""
    row = load_row(supabase, product_id)
    if row is None:
        raise LookupError(f"Product {product_id} is not known to preorder-service.")
    derived = svc.derive_state(row, as_of=today_et())
    if action not in derived["allowed_actions"]:
        raise IntlSoloActionError(
            f"'{action}' is not allowed while the title is {derived['display_state']}. "
            f"Allowed: {derived['allowed_actions'] or 'none'}.",
            display_state=derived["display_state"],
        )
    return {"row": row, "derived": derived}


# ──────────────────────────────────────────────
# Actions
# ──────────────────────────────────────────────

async def clear(supabase, client, product_id: int, basis: str,
                reason: Optional[str], changed_by: Optional[str] = None) -> Dict[str, Any]:
    """
    Clear the restriction on the basis of `received` stock or `guaranteed`
    stock. Records the decision first, then removes the tag (see WRITE ORDER).
    """
    if basis not in CLEAR_BASES:
        raise ValueError(f"basis must be one of {CLEAR_BASES}, got {basis!r}")
    reason = _clean_reason(reason)
    ctx = _require(supabase, product_id, f"clear_{basis}")
    from_state = ctx["derived"]["display_state"]

    _write_state(supabase, product_id, "cleared", basis, reason, changed_by)
    errors = await remove_tag(client, product_id)
    _log(supabase, product_id, "clear", from_state=from_state, to_state="cleared",
         basis=basis, reason=reason, changed_by=changed_by, success=not errors,
         details={"title": ctx["row"].get("title")}, errors=errors)
    if errors:
        raise IntlSoloShopifyError(
            "Decision recorded as cleared, but Shopify did not remove the tag. "
            "The title is still restricted at checkout; run reconcile to retry.",
            errors,
        )
    return {"product_id": product_id, "action": "clear", "basis": basis,
            "from_state": from_state, "result": "cleared"}


async def _restrict(supabase, client, product_id: int, action: str, basis: str,
                    reason: Optional[str], changed_by: Optional[str],
                    from_state: str, title: Optional[str]) -> Dict[str, Any]:
    """Add the tag first, then record the decision (see WRITE ORDER)."""
    errors = await add_tag(client, product_id)
    if errors:
        _log(supabase, product_id, action, from_state=from_state, to_state="restricted",
             basis=basis, reason=reason, changed_by=changed_by, success=False,
             details={"title": title}, errors=errors)
        raise IntlSoloShopifyError(
            "Shopify did not add the tag; nothing was recorded. The title is NOT "
            "restricted at checkout.",
            errors,
        )
    _write_state(supabase, product_id, "restricted", basis, reason, changed_by)
    _log(supabase, product_id, action, from_state=from_state, to_state="restricted",
         basis=basis, reason=reason, changed_by=changed_by, details={"title": title})
    return {"product_id": product_id, "action": action, "basis": basis,
            "from_state": from_state, "result": "restricted"}


async def restore(supabase, client, product_id: int, reason: Optional[str],
                  changed_by: Optional[str] = None) -> Dict[str, Any]:
    """
    Undo a clearance. The basis returns to auto_preorder when the title is
    still a preorder, otherwise force_on.
    """
    reason = _clean_reason(reason)
    ctx = _require(supabase, product_id, "restore")
    basis = "auto_preorder" if svc.is_candidate(ctx["row"]) else "force_on"
    return await _restrict(supabase, client, product_id, "restore", basis, reason,
                           changed_by, ctx["derived"]["display_state"], ctx["row"].get("title"))


async def force_on(supabase, client, product_id: int, reason: Optional[str],
                   changed_by: Optional[str] = None) -> Dict[str, Any]:
    """Restrict a title that is not a preorder."""
    reason = _clean_reason(reason)
    ctx = _require(supabase, product_id, "force_on")
    return await _restrict(supabase, client, product_id, "force_on", "force_on", reason,
                           changed_by, ctx["derived"]["display_state"], ctx["row"].get("title"))


# ──────────────────────────────────────────────
# Reconcile
# ──────────────────────────────────────────────

def plan_for(derived: Dict[str, Any]) -> Optional[str]:
    """The reconcile action for one derived row, or None when nothing is needed."""
    if derived["display_state"] == svc.PENDING_RESTRICTION:
        return PLAN_RESTRICT
    if derived["drift"] == svc.DRIFT_TAG_MISSING:
        return PLAN_TAG_ADD
    if derived["drift"] == svc.DRIFT_TAG_UNEXPECTED:
        return PLAN_TAG_REMOVE if derived["state"] == "cleared" else PLAN_REPORT
    return None


def build_plan(supabase, product_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """
    Every product that needs a reconcile action. Based on the tags in the last
    classification snapshot, so a just-made Shopify edit may show until the
    product is reclassified. Applying is safe regardless: both tag mutations
    are idempotent.
    """
    wanted = set(product_ids) if product_ids else None
    plan = []
    for derived in svc.list_states(supabase, include_not_applicable=True):
        if wanted is not None and derived["product_id"] not in wanted:
            continue
        action = plan_for(derived)
        if action:
            plan.append({
                "product_id": derived["product_id"],
                "title": derived["title"],
                "pub_date": derived["pub_date"],
                "classification": derived["classification"],
                "display_state": derived["display_state"],
                "drift": derived["drift"],
                "plan": action,
            })
    return plan


async def reconcile(supabase, client, apply: bool = False, confirm: Optional[str] = None,
                    product_ids: Optional[List[int]] = None,
                    changed_by: Optional[str] = None) -> Dict[str, Any]:
    """
    Dry-run by default: returns the plan and changes nothing.

    With apply=True and confirm == "apply", executes it. The first applied run
    is the backfill, and it turns on international enforcement for every
    restricted title, because the Cart Lock rule is already live on the tag.
    report_only items are never acted on.
    """
    plan = build_plan(supabase, product_ids)
    counts: Dict[str, int] = {}
    for item in plan:
        counts[item["plan"]] = counts.get(item["plan"], 0) + 1

    if not apply:
        return {"applied": False, "count": len(plan), "counts": counts, "plan": plan}

    if confirm != APPLY_CONFIRMATION:
        raise ValueError(
            f"Applying turns on live enforcement. Pass confirm={APPLY_CONFIRMATION!r} to proceed."
        )

    results = []
    for item in plan:
        pid, action = item["product_id"], item["plan"]
        try:
            if action == PLAN_RESTRICT:
                errors = await add_tag(client, pid)
                if not errors:
                    _write_state(supabase, pid, "restricted", "auto_preorder", None, changed_by)
                _log(supabase, pid, "restrict", from_state=item["display_state"],
                     to_state="restricted", basis="auto_preorder", changed_by=changed_by,
                     success=not errors, details={"title": item["title"], "source": "reconcile"},
                     errors=errors)
            elif action == PLAN_TAG_ADD:
                errors = await add_tag(client, pid)
                _log(supabase, pid, "tag_add", changed_by=changed_by, success=not errors,
                     details={"title": item["title"], "source": "reconcile"}, errors=errors)
            elif action == PLAN_TAG_REMOVE:
                errors = await remove_tag(client, pid)
                _log(supabase, pid, "tag_remove", changed_by=changed_by, success=not errors,
                     details={"title": item["title"], "source": "reconcile"}, errors=errors)
            else:
                errors = []
            results.append({**item, "result": "skipped" if action == PLAN_REPORT
                            else ("failed" if errors else "done"), "errors": errors or None})
        except Exception as exc:
            logger.error(f"[intl-solo] reconcile {action} failed for {pid}: {exc}")
            _log(supabase, pid, action, changed_by=changed_by, success=False,
                 details={"title": item["title"], "source": "reconcile"},
                 errors=[{"message": str(exc)}])
            results.append({**item, "result": "error", "errors": [{"message": str(exc)}]})

    failed = sum(1 for r in results if r["result"] in ("failed", "error"))
    return {"applied": True, "count": len(results), "counts": counts,
            "failed": failed, "results": results}
