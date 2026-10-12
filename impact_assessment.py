"""Evidence-grounded potential price impact, independent of sentiment and reach."""
VERSION = 1
SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'score': {'type': 'integer', 'minimum': 0, 'maximum': 100},
    'scope': {'type': 'string', 'enum': ['company', 'sector', 'market']},
    'target': {'type': 'string', 'minLength': 1, 'maxLength': 120},
    'confidence': {'type': 'string', 'enum': ['low', 'medium', 'high']},
    'expectations': {'type': 'string', 'enum': ['documented', 'unknown']},
    'change': {'type': 'string', 'minLength': 1, 'maxLength': 350},
    'magnitude': {'type': 'string', 'minLength': 1, 'maxLength': 350},
    'realization': {'type': 'string', 'minLength': 1, 'maxLength': 350},
    'duration': {'type': 'string', 'minLength': 1, 'maxLength': 350},
    'reason': {'type': 'string', 'minLength': 1, 'maxLength': 500},
    'evidence': {'type': 'array', 'minItems': 1, 'maxItems': 3, 'items': {'type': 'integer'}},
    'expectation_evidence': {'type': 'array', 'maxItems': 2, 'items': {'type': 'integer'}},
}}
SCHEMA['required'] = list(SCHEMA['properties'])
PROMPT = """
Also return impact with these fields:
score (0..100, increments of 5), scope (company/sector/market), target (affected entity),
confidence (low/medium/high), expectations (documented/unknown), change, magnitude,
realization, duration, reason, evidence (1..3 source span IDs), expectation_evidence (0..2 span IDs).
Evaluate POTENTIAL price impact on the directly affected target, not predicted return or observed price reaction.
0..30: repetition/commentary or little incremental valuation information; 35..65: limited or uncertain
economic effect; 70..85: material change to earnings/cash flows/growth or discount rates/risk premiums;
90..100: changes survival or core valuation assumptions. No fixed score for an event category.
Small-company existential news may be 95 with company scope; market reach is NOT a score bonus.
Explain four questions briefly: what changed versus known facts, economic magnitude relative to target,
how far the development has actually progressed, and persistence of the effect.
Unknown denominators, consensus, timing and prior knowledge must stay unknown; never invent them.
Distinguish article-stated previous guidance from market consensus. Only expectations=documented when
the source explicitly supplies a comparable market expectation; cite it in expectation_evidence.
Never infer surprise or priced-in status from a fresh timestamp or a price move. Repetition alone is not news.
Rumors can have high potential impact but uncertainty must be explicit. Confidence is evidence sufficiency,
not event probability. Separate facts from your inferred economic transmission in reason.
Keep all explanations concise; English is fine. Evidence IDs support facts, not proof of your forecast.
"""


def validate(value, spans, coverage):
    if not isinstance(value, dict) or set(value) != set(SCHEMA['required']):
        raise ValueError('Invalid impact fields')
    for name, rule in SCHEMA['properties'].items():
        item = value[name]
        if rule['type'] == 'string':
            if not isinstance(item, str) or not item.strip() or len(item) > rule.get('maxLength', 30):
                raise ValueError('Invalid impact text: '+name)
            if 'enum' in rule and item not in rule['enum']:
                raise ValueError('Invalid impact category: '+name)
    if type(value['score']) is not int or not 0 <= value['score'] <= 100 or value['score'] % 5:
        raise ValueError('Invalid impact score')
    out = dict(value, version=VERSION, coverage=coverage)
    for name, minimum, maximum in [('evidence', 1, 3), ('expectation_evidence', 0, 2)]:
        ids = value[name]
        if not isinstance(ids, list) or not minimum <= len(ids) <= maximum or any(
                type(i) is not int or not 0 <= i < len(spans) for i in ids):
            raise ValueError('Invalid impact evidence')
        out[name] = [spans[i] for i in ids]
    if (value['expectations'] == 'documented') != bool(value['expectation_evidence']):
        raise ValueError('Expectations require separate evidence')
    # Source coverage limits confidence, never silently changes the model's impact score.
    if coverage != '본문 추출' and out['confidence'] == 'high':
        out['confidence'] = 'medium'
    return out
