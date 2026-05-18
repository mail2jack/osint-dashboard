import httpx
import re
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_URL = 'https://www.politie.nl'
GEZOCHT_URL = f'{BASE_URL}/gezocht/opsporingsbericht'
TIMEOUT = 15
MAX_PAGES = 5
RESULTS_PER_PAGE = 9


class NuxtResolver:
    def __init__(self, data: list):
        self.data = data

    def resolve(self, idx):
        if isinstance(idx, (int, float)):
            idx = int(idx)
            val = self.data[idx]
            if isinstance(val, list) and len(val) >= 2 and val[0] in ('ShallowReactive', 'Reactive', 'Ref'):
                if val[0] in ('ShallowReactive', 'Reactive'):
                    return self.data[val[1]]
                elif val[0] == 'Ref':
                    return self.resolve(val[1])
            if isinstance(val, list) and len(val) >= 2 and val[0] == 'EmptyRef':
                return None
            if isinstance(val, list) and len(val) >= 2 and val[0] == 'skipHydrate':
                return self.resolve(val[1])
            return val
        if isinstance(idx, list) and len(idx) >= 2:
            if idx[0] == 'Ref':
                return self.resolve(idx[1])
            if idx[0] in ('ShallowReactive', 'Reactive'):
                return self.data[idx[1]]
        if isinstance(idx, dict) and '$ref' in idx:
            return self.resolve(self.data[idx['$ref']])
        return idx

    def fully_resolve(self, obj):
        if isinstance(obj, bool):
            return obj
        if isinstance(obj, (int, float)):
            resolved = self.resolve(int(obj))
            if isinstance(resolved, (dict, list)):
                return self.fully_resolve(resolved)
            return resolved
        if isinstance(obj, list):
            if len(obj) >= 2 and obj[0] in ('Ref', 'EmptyRef', 'skipHydrate'):
                return self.fully_resolve(self.resolve(obj))
            return [self.fully_resolve(item) for item in obj]
        if isinstance(obj, dict):
            if '$ref' in obj:
                return self.fully_resolve(self.resolve(obj))
            return {k: self.fully_resolve(v) for k, v in obj.items()}
        return obj


def extract_nuxt_payload(html: str) -> list | None:
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    for s in scripts:
        s = s.strip()
        if s.startswith('['):
            try:
                data = json.loads(s)
                if isinstance(data, list) and len(data) > 10:
                    return data
            except json.JSONDecodeError:
                continue
    return None


def extract_opsporingsberichten(html: str) -> tuple[int, list[dict]]:
    data = extract_nuxt_payload(html)
    if not data:
        logger.warning('No Nuxt payload found in page')
        return 0, []

    resolver = NuxtResolver(data)

    try:
        pinia = resolver.resolve(data[1]['pinia'])
        bloomreach = resolver.resolve(pinia['bloomreach'])
        content = resolver.resolve(bloomreach['content'])
        pages = resolver.resolve(content['page'])
    except (KeyError, IndexError, TypeError) as e:
        logger.warning(f'Failed to resolve Nuxt structure: {e}')
        return 0, []

    for _page_key, page_val in pages.items():
        pv = resolver.resolve(page_val)
        if not isinstance(pv, dict) or 'models' not in pv:
            continue
        models = resolver.resolve(pv['models'])
        if not isinstance(models, dict) or 'totalResults' not in models:
            continue

        total = resolver.resolve(models['totalResults'])
        doc_list = resolver.resolve(models['overzichtDocuments'])
        if not isinstance(doc_list, list):
            continue

        docs = []
        for idx in doc_list:
            doc = resolver.fully_resolve(resolver.resolve(idx))
            if isinstance(doc, dict) and 'title' in doc and 'url' in doc:
                docs.append(_normalize_opsporingsbericht(doc))

        return total or 0, docs

    return 0, []


def _normalize_opsporingsbericht(doc: dict) -> dict:
    image = doc.get('image', {}) or {}
    image_src = image.get('imageSrc', '')
    if image_src and not image_src.startswith('http'):
        image_src = f'{BASE_URL}{image_src}' if image_src.startswith('/') else image_src

    url = doc.get('url', '')
    if url and url.startswith('/'):
        url = f'{BASE_URL}{url}'

    title = doc.get('title', '') or ''
    location = doc.get('location', '') or ''
    date_ms = doc.get('date')
    date_str = ''
    if date_ms:
        try:
            date_str = datetime.fromtimestamp(int(date_ms) / 1000).strftime('%Y-%m-%d')
        except (ValueError, OSError):
            pass

    return {
        'title': title,
        'location': location,
        'date': date_str,
        'date_raw': date_ms,
        'url': url,
        'image_url': image_src,
        'image_alt': image.get('altText', ''),
        'type': 'opsporingsbericht',
        'source': 'politie.nl/gezocht',
    }


def fetch_page(page: int = 1) -> tuple[int, list[dict]]:
    url = GEZOCHT_URL if page <= 1 else f'{GEZOCHT_URL}?page={page}'
    try:
        r = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        logger.error(f'Failed to fetch page {page}: {e}')
        return 0, []

    return extract_opsporingsberichten(r.text)


def search_name_in_docs(forename: str, surname: str, docs: list[dict]) -> list[dict]:
    if not forename and not surname:
        return []

    parts = [p.lower() for p in (forename + ' ' + surname).split() if p]
    matched = []
    for doc in docs:
        title_lower = (doc.get('title') or '').lower()
        location_lower = (doc.get('location') or '').lower()
        text = f'{title_lower} {location_lower}'
        if any(part in text for part in parts):
            matched.append(doc)
    return matched


def search_opsporingsberichten(
    forename: str = '',
    surname: str = '',
    max_pages: int = MAX_PAGES,
) -> dict:
    total_all = 0
    all_matched = []
    total_results_global = 0
    pages_scanned = 0

    for page in range(1, max_pages + 1):
        total_results, docs = fetch_page(page)
        if not docs and page == 1:
            break
        if not docs:
            break

        pages_scanned += 1
        total_results_global = total_results or len(docs) * 10
        total_all += len(docs)

        matched = search_name_in_docs(forename, surname, docs)
        all_matched.extend(matched)

        if total_results and total_all >= total_results:
            break

    return {
        'total_results': total_results_global,
        'pages_scanned': pages_scanned,
        'total_docs_fetched': total_all,
        'matches': all_matched,
        'match_count': len(all_matched),
    }
