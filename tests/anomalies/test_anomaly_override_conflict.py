"""
ANOMALY: OVERRIDE CONFLICT (Rev 3)

Triggered when override_date contradicts known history:

- override_date < pub_date
- OR override_date < latest date_tag
- OR override_date is chronologically older than any known official date

TODO CASES TO IMPLEMENT:

1. override_date < pub_date → anomaly_override_conflict
2. override_date < latest_tag → anomaly_override_conflict
3. override_date < today while pub_date > today → anomaly_override_conflict
4. Situations that should instead be pubdate_conflict (negative tests)
"""