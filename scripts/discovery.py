"""Bounded Saved-card screening before details; full listing remains mandatory."""
import batches
import eligibility
import spans


@spans.measured('card_screening')
def screen_cards(records, cards, policies, *, limit=20):
    if type(limit) is not int or not 1 <= limit <= 100 or not isinstance(cards, list):
        raise ValueError('invalid_card_bound')
    records, policies = list(records), list(policies)
    if not policies or any(not isinstance(card, dict) for card in cards[:limit]):
        raise ValueError('invalid_card_screen_context')
    # Saved cards win stable ordering, including when a caller supplies search.
    ordered = sorted(enumerate(cards[:limit]), key=lambda pair: (pair[1].get('source') != 'saved', pair[0]))
    candidates = []
    for index, card in ordered:
        if not isinstance(card, dict):
            raise ValueError('invalid_card')
        candidate = {k: v for k,v in card.items() if k not in {'application_id', 'submissions', 'status', 'submission_intent'}}
        candidate['application_id'] = f'card-candidate-{index}'
        if any(r['application_id'] == candidate['application_id'] for r in records):
            raise ValueError('card_identity_collision')
        candidates.append((index, candidate))
    groups = batches._groups(records + [c for _,c in candidates])
    known = {r['application_id'] for r in records}
    result, accepted = [], set()
    for index, candidate in candidates:
        group = next(g for g in groups if any(r['application_id']==candidate['application_id'] for r in g))
        local = [r for r in group if r['application_id'] in known]
        if local:
            unsafe = any(r.get('submissions') or r.get('status') in {'submitted', 'submission_uncertain'} or 'submission_intent' in r for r in local)
            decision = 'excluded_identity' if unsafe else 'existing_queue'
        elif any(r['application_id'] in accepted for r in group):
            decision = 'duplicate_card'
        else:
            # Header exclusions only. No missing/ambiguous header approves a job.
            header = {**candidate, 'job_description': ''}
            rejected = any(eligibility.evaluate(header, p)['decision'] == 'excluded' for p in policies)
            decision = 'header_excluded' if rejected else 'inspect_listing'
            accepted.add(candidate['application_id'])
        result.append({'index': index, 'decision': decision})
    return {'cards': result, 'bounded': len(cards) > limit, 'full_listing_required': True}


def discover_saved(adapter, *, read_records, policies, guard, limit=20):
    """Integration helper for a live runner's discover adapter; no bundled driver.

    Every tool boundary gets a fresh guard and fresh identities before details.
    The adapter must cap Saved-card observation itself and capture only observed
    listings via the existing guarded workspace.capture(deferred=True) path.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('invalid_card_bound')
    def allowed():
        if spans.authorize(guard) is not True:
            raise ValueError('discovery_guard_refused')
    with spans.span('saved_discovery', 'browser'):
        allowed()
        cards = adapter.saved_cards(limit)
    if not isinstance(cards, list) or len(cards) > limit or any(c.get('source') != 'saved' for c in cards):
        raise ValueError('saved_card_contract')
    initial = screen_cards(read_records(), cards, policies(), limit=limit)
    captured = []
    for row in initial['cards']:
        if row['decision'] != 'inspect_listing':
            continue
        with spans.span('navigation', 'browser'):
            allowed()
            fresh = screen_cards(read_records(), cards, policies(), limit=limit)
            if next(r['decision'] for r in fresh['cards'] if r['index']==row['index']) != 'inspect_listing':
                continue
            allowed()  # screening can persist nested spans too
            listing = adapter.open_listing(cards[row['index']])
        with spans.span('listing_capture', 'tool'):
            allowed()
            captured.append(adapter.capture(listing))
    return captured
