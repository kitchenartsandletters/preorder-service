"""
ANOMALY: MULTI-DATE CONFLICT (Rev 3)

Triggered ONLY when ALL are true:

- len(date_tags) >= 2
- pub_date is None
- override_date is None

I.e., multiple historical pub dates exist but there is no canonical value.

TODO CASES TO IMPLEMENT:

1. Multiple date_tags, no pub_date, no override_date → anomaly_multi_date_conflict
2. Ensure NOT triggered when pub_date matches latest_tag
3. Ensure NOT triggered when override_date exists
4. Ensure date_tag order in the tag array does not matter (sorting works)
"""