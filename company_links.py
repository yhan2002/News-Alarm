"""Company relevance in the existing analysis call; no network operations."""
import json
import re

# These aliases also denote ordinary words, places or unrelated businesses.
AMBIGUOUS = {'apple', 'amazon', 'alphabet', 'popular', 'caterpillar', 'grail',
             'progressive', 'tapestry', 'oracle', 'vertex', 'adobe', 'micron', 'lilly',
             'dash', 'mat', 'mar', 'hood', 'halo', 'arm', 'meta', 'app', 'now', 'cat', 'has', 'bro'}


def conservative(text, candidates):
    from news import UNIVERSE, contains
    links = []
    for row in candidates:
        symbol = row['symbol']
        name = UNIVERSE[symbol][0]
        explicit = bool(re.search(r'\$'+re.escape(symbol)+r'\b|\('+re.escape(symbol)+r'\)|\b(?:NASDAQ|NYSE)\s*:\s*'+re.escape(symbol)+r'\b', text))
        legal_name = bool(re.search(r'\b(?:Inc|Corp|Corporation|Ltd|plc|LLC)\b', name, re.I) and contains(text, name))
        evidence = [v for v in row['evidence'] if v.lower() not in AMBIGUOUS]
        if explicit or legal_name or evidence:
            links.append(dict(row, evidence=row['evidence'] if explicit or legal_name else evidence))
    return links


def candidates(text):
    from news import candidate_entities
    # ponytail: at most 20 candidates per call; excess candidates are withheld, raise with an output-budget review.
    return candidate_entities({'title': text, 'summary': ''})[0][:20]


def add_request(schema, prompt, text, spans):
    rows = candidates(text)
    schema['required'].append('companies')
    schema['properties']['companies'] = {'type':'array', 'minItems':len(rows), 'maxItems':len(rows),
        'items':{'type':'object', 'additionalProperties':False, 'required':['symbol','decision','quote'],
                 'properties':{'symbol':{'type':'string'},
                               'decision':{'type':'string','enum':['relevant','unrelated','uncertain']},
                               'quote':{'type':'integer','minimum':0,'maximum':len(spans)-1}}}}
    prompt += '\ncompanies: return exactly one {"symbol":"...","decision":"relevant|unrelated|uncertain","quote":0} for EVERY candidate below; [] if none. A candidate is NOT a confirmed company mention. Read the source context: relevant means this exact listed company is substantively discussed, not an idiom, person, place, common noun, other company with the same name, navigation, ticker sidebar or incidental name-drop. Reject holy grail->GRAIL, rainforest->Amazon, popular->Popular bank, progressive policy->Progressive insurance. Use uncertain when the identity/relevance is unsupported. quote is the source span containing the actual mention, for every decision. Never infer relevance from a feed ticker or sector.\nCandidates: '
    return prompt + json.dumps([{'symbol':r['symbol'],'name':r['name']} for r in rows], ensure_ascii=False) + '\nThis candidate list is ONLY an identity-disambiguation checklist, NOT a restriction on event/impact targets. Assess impact on the actual subject of the article, including companies outside this list (SpaceX can be the target even if GRAIL is the sole candidate). Never use an unrelated candidate as impact.target. Even for score 0, impact.evidence MUST contain 1..3 existing source span IDs; never return an empty list.'


def validate(value, text, spans):
    from news import candidate_entities
    expected = {r['symbol'] for r in candidates(text)}
    if not isinstance(value, list) or len(value) != len(expected):
        raise ValueError('Incomplete company relevance assessment')
    seen = set()
    rows = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {'symbol','decision','quote'}:
            raise ValueError('Invalid company relevance row')
        symbol, decision, index = row['symbol'], row['decision'], row['quote']
        if not isinstance(symbol,str) or symbol not in expected or symbol in seen or decision not in ('relevant','unrelated','uncertain'):
            raise ValueError('Invalid company decision')
        if type(index) is not int or not 0 <= index < len(spans):
            raise ValueError('Invalid company evidence span')
        quote = spans[index]
        if symbol not in {r['symbol'] for r in candidate_entities({'title':quote,'summary':''})[0]}:
            raise ValueError('Company absent from cited evidence')
        rows.append({'symbol':symbol,'decision':decision,'quote':quote})
        seen.add(symbol)
    return {'version':1,'rows':rows}


def resolve(article):
    from news import entities, UNIVERSE
    record = article.get('qwen', {})
    review = record.get('facts', {}).get('companies', {})
    if record.get('facts', {}).get('verification'):
        from relation_verifier import apply
        review = apply(record['facts']).get('companies', {})
    if record.get('status') == 'done' and review.get('version') == 1:
        return [{'symbol':r['symbol'],'name':UNIVERSE[r['symbol']][0],
                 'evidence':[r['quote']], 'basis':'model_company_relevance'}
                for r in review['rows'] if r['decision']=='relevant' and r['symbol'] in UNIVERSE]
    return entities(article)[0]
