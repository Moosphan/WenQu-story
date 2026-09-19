"""Aggregate observations; missing labels/costs remain unknown, never zero failures."""
import json
from collections import Counter


def context_usage(conn, book_id):
    # Each service compilation attempt is one observation, including refusals
    # before a lease exists. Repeated blocked attempts remain separate attempts.
    rows = list(conn.execute("SELECT kind,payload FROM events WHERE book_id=? AND kind IN ('context_compiled','context_blocked') ORDER BY seq", (book_id,)))
    observations = [json.loads(row[1])['context'] for row in rows]
    gates = Counter(json.loads(row[1]).get('gate', 'context_capacity') for row in rows if row[0] == 'context_blocked')
    provider = [json.loads(row[0]) for row in conn.execute(
        "SELECT payload FROM events WHERE book_id=? AND kind='provider_usage' ORDER BY seq", (book_id,))]
    measured = len(observations)
    triggered = sum(bool(item.get('organization_triggered')) for item in observations)
    known = [item for item in observations if item.get('context_window') is not None]
    overflow = sum(bool(item.get('would_exceed_capacity')) for item in known)
    missing_dependencies = sum(bool(item.get('missing_hard_ids')) or item.get('blocked_reason') == 'missing_hard_dependencies' for item in observations)
    # Missing hard dependencies are a definite failure even without a capacity
    # profile. Otherwise an unknown capacity has no assessable success/failure.
    assessed = [item for item in observations if item.get('context_window') is not None or
                item.get('missing_hard_ids') or item.get('blocked_reason') == 'missing_hard_dependencies']
    unexecutable = sum(bool(item.get('would_be_unexecutable', item.get('would_exceed_capacity'))) or
                      bool(item.get('missing_hard_ids')) for item in assessed)
    transport = [item for item in provider if isinstance(item.get('context'), dict)]
    transport_blocked = [item for item in provider if item.get('error_code') == 'CONTEXT_CAPACITY']
    transport_reasons = Counter((item.get('context') or {}).get('blocked_reason') or 'unspecified' for item in transport_blocked)
    labels = [json.loads(row[0]) for row in conn.execute(
        "SELECT payload FROM events WHERE book_id=? AND kind='context_recall_label'", (book_id,))]
    expected = sum(item.get('expected_count', 0) for item in labels)
    missing = sum(item.get('missing_count', 0) for item in labels)
    return {'measured_requests': measured, 'organization_triggers': triggered,
            'organization_trigger_rate': triggered / measured if measured else None,
            'measurement_unit': 'service_compilation_attempt',
            'known_capacity_requests': len(known), 'unknown_capacity_requests': measured - len(known),
            'assessed_executability_requests': len(assessed),
            'organized_unexecutable': unexecutable,
            'organized_unexecutable_rate': unexecutable / len(assessed) if assessed else None,
            'capacity_overflow_requests': overflow, 'missing_hard_dependency_requests': missing_dependencies,
            'operational_blocked_requests': sum(row[0] == 'context_blocked' for row in rows),
            'operational_blocked_gates': dict(gates),
            # Final transport counts are separate; never double-count a leased
            # request in the service numerator or classify a network error as
            # capacity failure. Call attempts, including retries, are the unit.
            'transport_calls': len(provider), 'transport_measured_calls': len(transport),
            'transport_context_blocked_calls': len(transport_blocked),
            'transport_context_blocked_rate': len(transport_blocked) / len(provider) if provider else None,
            'transport_blocked_reasons': dict(transport_reasons),
            'transport_unknown_capacity_calls': sum(item['context'].get('context_window') is None for item in transport),
            'truncated_outputs': sum(item.get('error_code') == 'TRUNCATED_OUTPUT' for item in provider),
            'key_fact_omission_rate': missing / expected if expected else None,
            'recall_labels': len(labels), 'latest': observations[-1] if observations else None}
