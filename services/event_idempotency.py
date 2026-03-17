def has_processed_event(supabase, event_id: str) -> bool:
    response = (
        supabase
        .schema("preorder")
        .table("processed_events")
        .select("event_id")
        .eq("event_id", event_id)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def mark_event_processed(
    supabase,
    *,
    event_id: str,
    event_type: str,
    entity_id: int | None,
) -> None:
    (
        supabase
        .schema("preorder")
        .table("processed_events")
        .insert({
            "event_id": event_id,
            "event_type": event_type,
            "entity_id": entity_id,
        })
        .execute()
    )