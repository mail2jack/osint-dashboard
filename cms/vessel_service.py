"""
Vessel / Ship Lookup Service
============================
Integrates with free maritime data sources:
- VesselFinder (public web, free MMSI/name lookup — no key needed)
- MarinePlan OpenShipData (free API key, search by name/MMSI)
- KVNR Schepenzoeker (public web, IMO/name lookup)
- Binnenvaart.eu (public web, ENI/name lookup)
- DeBinnenvaart.nl (public web, ENI/name lookup)
- Equasis (free registration, IMO lookup - optional via settings)
"""

import logging
import re
import threading
import time
from typing import Optional, Any
from urllib.parse import quote

import requests as http_requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LAST_MARINEPLAN_CALL = 0
_MARINEPLAN_MIN_INTERVAL = 2  # seconds between calls
_marineplan_lock = threading.Lock()


def _rate_limit(last: float, interval: float) -> float:
    now = time.time()
    elapsed = now - last
    if elapsed < interval:
        time.sleep(interval - elapsed)
    return time.time()


def _get_setting(key: str, default=None) -> Any:
    """Lazy-import Setting to avoid circular imports."""
    from .models import Setting
    return Setting.get(key, default)


# ---------------------------------------------------------------------------
# VesselFinder (public web, free, no key needed — MMSI/name)
# ---------------------------------------------------------------------------

VESSELFINDER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/124.0.0.0 Safari/537.36',
}


def lookup_vesselfinder(name: Optional[str] = None, mmsi: Optional[str] = None) -> Optional[dict]:
    """Look up vessel on VesselFinder by MMSI or name (free, no key).

    Returns dict with keys: name, mmsi, imo, ship_type, position, source, source_url
    or None if not found.
    """
    query = mmsi or name
    if not query:
        return None

    try:
        param = f'mmsi={query}' if mmsi else f'q={quote(query)}'
        url = f'https://www.vesselfinder.com/?{param}'
        resp = http_requests.get(url, headers=VESSELFINDER_HEADERS, timeout=15)

        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, 'lxml')
        title_tag = soup.find('title')
        if not title_tag:
            return None
        title = title_tag.text

        # Extract name from title: "NAME Current position (type, MMSI X)" or "NAME..."
        name_match = re.match(r'^(.+?)\s+Current position', title)
        if not name_match:
            return None
        vessel_name = name_match.group(1).strip()

        # Extract vessel type from title
        type_match = re.search(r'\(([^)]+)\)', title)
        ship_type = type_match.group(1).split(',')[0].strip() if type_match else None

        # Extract MMSI and IMO from embedded JS vars
        imo = None
        mmsi_val = None
        for script in soup.find_all('script'):
            if script.string and 'MMSI=' in script.string:
                mmsi_m = re.search(r'MMSI=(\d+)', script.string)
                if mmsi_m:
                    mmsi_val = mmsi_m.group(1)
                imo_m = re.search(r'IMO=(\d+)', script.string)
                if imo_m and imo_m.group(1) != '0':
                    imo = imo_m.group(1)
                break

        # Extract position from meta description
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        position = None
        if meta_desc:
            pos_match = re.search(r'position is ([\d.]+ [NS], [\d.]+ [EW])',
                                  meta_desc.get('content', ''))
            if pos_match:
                raw = pos_match.group(1)
                try:
                    lat_str, lon_str = raw.split(', ')
                    lat = _parse_dms(lat_str)
                    lon = _parse_dms(lon_str)
                    if lat is not None and lon is not None:
                        position = {'lat': lat, 'lon': lon}
                except (ValueError, IndexError):
                    logger.debug("Failed to parse VesselFinder position data")

        return {
            'name': vessel_name,
            'mmsi': mmsi_val or (query if mmsi else None),
            'imo': imo,
            'ship_type': ship_type,
            'position': position,
            'source': 'vesselfinder',
            'source_url': f'https://www.vesselfinder.com/?mmsi={mmsi_val}' if mmsi_val else url
        }

    except http_requests.RequestException as e:
        logger.warning(f"VesselFinder request failed: {e}")
        return None


def _parse_dms(s: str) -> Optional[float]:
    """Parse '46 N' or '-12.5' style coordinate strings."""
    s = s.strip()
    direction = 1
    if s.endswith('N') or s.endswith('E'):
        direction = 1
        s = s[:-1].strip()
    elif s.endswith('S') or s.endswith('W'):
        direction = -1
        s = s[:-1].strip()
    try:
        return float(s) * direction
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# MarinePlan OpenShipData (free, API key required)
# ---------------------------------------------------------------------------

MARINEPLAN_BASE = 'https://ais.marineplan.com/location/2'


def lookup_marineplan(name: Optional[str] = None, mmsi: Optional[str] = None) -> Optional[dict]:
    """Look up vessel on MarinePlan by name or MMSI.

    Returns dict with keys: name, mmsi, ship_type, length, beam,
    position {lat, lon}, speed, destination, callsign, flag, source_url
    or None if not found / no API key.
    """
    global _LAST_MARINEPLAN_CALL

    api_key = _get_setting('marineplan_api_key')
    if not api_key:
        logger.info("MarinePlan API key not configured")
        return None

    if not name and not mmsi:
        return None

    # Build query param — MarinePlan accepts name or MMSI
    query_param = mmsi or name
    if not query_param:
        return None

    with _marineplan_lock:
        _LAST_MARINEPLAN_CALL = _rate_limit(_LAST_MARINEPLAN_CALL, _MARINEPLAN_MIN_INTERVAL)

    try:
        url = f'{MARINEPLAN_BASE}/ship.json'
        params = {
            'ship': query_param.replace(' ', ''),
            'source': 'ANY',
            'key': api_key
        }
        resp = http_requests.get(url, params=params, timeout=15)

        if resp.status_code == 404:
            logger.info(f"MarinePlan: no ship found for '{query_param}'")
            return None
        if resp.status_code != 200:
            logger.warning(f"MarinePlan API returned {resp.status_code}")
            return None

        data = resp.json()
        if not data or not isinstance(data, dict):
            return None

        # Map MarinePlan fields to our standard format
        result = {
            'name': data.get('name'),
            'mmsi': str(data.get('mmsi', '')),
            'callsign': data.get('callsign'),
            'ship_type': _map_marineplan_type(data.get('type')),
            'length': data.get('length'),
            'beam': data.get('width'),
            'draught': data.get('draught'),
            'speed': data.get('speed'),
            'destination': data.get('destinationname'),
            'eta': data.get('eta'),
            'bearing': data.get('bearing'),
            'position': None,
            'source': 'marineplan',
            'source_url': f'https://marineplan.com/track?mmsi={data.get("mmsi", "")}'
        }

        if data.get('point'):
            parts = str(data.get('point', '')).split(',')
            if len(parts) >= 2:
                try:
                    result['position'] = {
                        'lat': float(parts[0]),
                        'lon': float(parts[1])
                    }
                except (ValueError, TypeError):
                    logger.debug("Failed to parse MarinePlan position data")

        return result

    except http_requests.RequestException as e:
        logger.warning(f"MarinePlan request failed: {e}")
        return None
    except (ValueError, TypeError) as e:
        logger.warning(f"MarinePlan parse error: {e}")
        return None


def _map_marineplan_type(t: Optional[int]) -> str:
    mapping = {
        6: 'Canoe',
        5: 'Open Console Boat',
        4: 'Recreation Cruiser',
        3: 'Recreation Yacht',
        2: 'Cargo Vessel',
    }
    return mapping.get(t, 'Unknown')


# ---------------------------------------------------------------------------
# KVNR Schepenzoeker (public, no key needed, IMO/name)
# ---------------------------------------------------------------------------

KVNR_SEARCH_URL = 'https://kvnr.nl/nl/schepenzoeker'
_KVNR_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'nl,en;q=0.9',
}


def lookup_kvnr(imo: Optional[str] = None, name: Optional[str] = None) -> Optional[dict]:
    """Look up vessel on KVNR Schepenzoeker by IMO or name (public).

    Returns dict with keys: name, imo, flag, source, source_url
    or None if not found.
    """
    query = imo or name
    if not query:
        return None

    try:
        params = {'q': query.strip()}
        resp = http_requests.get(KVNR_SEARCH_URL, params=params,
                                 headers=_KVNR_HEADERS, timeout=15)

        if resp.status_code != 200:
            logger.warning(f"KVNR returned {resp.status_code}")
            return None

        soup = BeautifulSoup(resp.text, 'lxml')
        results = soup.select('.search-result, .schepenzoeker-result, article')

        for result in results:
            text = result.get_text(' ', strip=True)
            if imo and imo in text:
                return _parse_kvnr_result(result, query)
            if name and name.lower() in text.lower():
                return _parse_kvnr_result(result, query)

        if imo:
            return None

        # With name only, try the page title or first result
        page_text = soup.get_text(' ', strip=True)
        if name and name.lower() in page_text.lower():
            return {
                'name': name,
                'imo': None,
                'flag': None,
                'source': 'kvnr',
                'source_url': f'{KVNR_SEARCH_URL}?q={quote(query)}'
            }

        return None

    except http_requests.RequestException as e:
        logger.warning(f"KVNR request failed: {e}")
        return None


def _parse_kvnr_result(element, query: str) -> dict:
    """Parse a KVNR search result element."""
    text = element.get_text(' ', strip=True)

    name = query
    imo = None
    flag = None

    imo_match = re.search(r'IMO[:\s]*(\d{7})', text, re.IGNORECASE)
    if imo_match:
        imo = imo_match.group(1)

    flag_match = re.search(r'(Flag|Vlag)[:\s]*(\w+)', text, re.IGNORECASE)
    if flag_match:
        flag = flag_match.group(2)

    # Try to extract name from result heading
    heading = element.select_one('h2, h3, h4, strong')
    if heading:
        name = heading.get_text(strip=True)

    return {
        'name': name,
        'imo': imo,
        'flag': flag,
        'source': 'kvnr',
        'source_url': f'{KVNR_SEARCH_URL}?q={quote(query)}'
    }


# ---------------------------------------------------------------------------
# Binnenvaart.eu (public, ENI lookup)
# ---------------------------------------------------------------------------

BINNENVAART_URL = 'https://www.binnenvaart.eu/'


def lookup_binnenvaart(eni: Optional[str] = None, name: Optional[str] = None) -> Optional[dict]:
    """Look up inland vessel on Binnenvaart.eu by ENI number or name (public).

    Returns dict with keys: name, eni, ship_type, year_built, builder, source, source_url
    or None if not found.
    """
    query = eni or name
    if not query:
        return None

    try:
        params = {'s': query.strip()}
        resp = http_requests.get(BINNENVAART_URL, params=params,
                                 headers=_KVNR_HEADERS, timeout=15)

        if resp.status_code != 200:
            logger.warning(f"Binnenvaart.eu returned {resp.status_code}")
            return None

        soup = BeautifulSoup(resp.text, 'lxml')
        rows = soup.select('table tr')

        for row in rows:
            text = row.get_text(' ', strip=True)
            if eni and eni in text:
                return _parse_binnenvaart_row(row, query)
            if name and name.lower() in text.lower():
                return _parse_binnenvaart_row(row, query)

        return None

    except http_requests.RequestException as e:
        logger.warning(f"Binnenvaart.eu request failed: {e}")
        return None


def _parse_binnenvaart_row(element, query: str) -> dict:
    """Parse a Binnenvaart.eu result row."""
    text = element.get_text(' | ', strip=True)
    cells = element.select('td, .schip-cell')

    name = query
    eni = None
    ship_type = None
    year_built = None
    builder = None

    eni_match = re.search(r'(\d{7,8})', text)
    if eni_match:
        eni = eni_match.group(1)

    type_match = re.search(r'(Motorvrachtschip|Motortankschip|Duwboot|Sleepvrachtschip|Passagierschip|Veerboot|Stoompassagiersschip|Kraanponton)', text)
    if type_match:
        ship_type = type_match.group(1)

    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    if year_match:
        year_built = year_match.group(1)

    # Cell indices: 0=image, 1=name, 2=ENI, 3=type, 4=location, 5=year, 6=builder, 7=date
    if len(cells) >= 2:
        name = cells[1].get_text(strip=True)
    if len(cells) >= 7:
        builder = cells[6].get_text(strip=True) or None

    return {
        'name': name,
        'eni': eni,
        'ship_type': ship_type,
        'year_built': year_built,
        'builder': builder,
        'source': 'binnenvaart',
        'source_url': f'{BINNENVAART_URL}?s={quote(query)}'
    }


# ---------------------------------------------------------------------------
# DeBinnenvaart.nl (public, ENI/name lookup)
# ---------------------------------------------------------------------------

DEBINNENVAART_URL = 'https://www.debinnenvaart.nl/schepen/zoek/'


def lookup_debinnenvaart(eni: Optional[str] = None, name: Optional[str] = None) -> Optional[dict]:
    """Look up inland vessel on DeBinnenvaart.nl by ENI or name (public).

    Returns dict with keys: name, eni, ship_type, year_built, builder, source, source_url
    or None if not found.
    """
    query = eni or name
    if not query:
        return None

    try:
        resp = http_requests.get(DEBINNENVAART_URL,
                                 params={'schip': query.strip()},
                                 headers=_KVNR_HEADERS, timeout=15)

        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, 'lxml')
        # Search results are in a table with class 'schepenlijst'
        rows = soup.select('table.schepenlijst tr')

        for row in rows:
            text = row.get_text(' | ', strip=True)
            if eni and eni in text:
                return _parse_debinnenvaart_row(row, query)
            if name and name.lower() in text.lower():
                return _parse_debinnenvaart_row(row, query)

        return None

    except http_requests.RequestException as e:
        logger.warning(f"DeBinnenvaart.nl request failed: {e}")
        return None


def _parse_debinnenvaart_row(element, query: str) -> dict:
    """Parse a DeBinnenvaart.nl search result row."""
    cells = element.select('td')
    text = element.get_text(' | ', strip=True)

    name = query
    eni_val = None
    ship_type = None
    year_built = None
    builder = None

    # Try to extract from cell positions
    if len(cells) >= 2:
        name = cells[1].get_text(strip=True) or name
    if len(cells) >= 3:
        eni_val = cells[2].get_text(strip=True) or None
    if len(cells) >= 4:
        ship_type = cells[3].get_text(strip=True) or None

    # Fallback to regex
    eni_match = re.search(r'(\d{7,8})', text)
    if eni_match and not eni_val:
        eni_val = eni_match.group(1)
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    if year_match:
        year_built = year_match.group(1)

    return {
        'name': name,
        'eni': eni_val,
        'ship_type': ship_type,
        'year_built': year_built,
        'builder': builder,
        'source': 'debinnenvaart',
        'source_url': f'{DEBINNENVAART_URL}?schip={quote(query)}'
    }


# ---------------------------------------------------------------------------
# Combined lookup
# ---------------------------------------------------------------------------

def lookup_vessel(imo: Optional[str] = None, mmsi: Optional[str] = None,
                  eni: Optional[str] = None, name: Optional[str] = None) -> dict:
    """Try all available sources and return merged result.

    Returns:
    {
        'found': bool,
        'name': str,
        'imo': str | None,
        'mmsi': str | None,
        'eni': str | None,
        'flag': str | None,
        'ship_type': str | None,
        'length': float | None,
        'beam': float | None,
        'year_built': str | None,
        'position': {lat, lon} | None,
        'speed': float | None,
        'destination': str | None,
        'callsign': str | None,
        'sources': [str],
        'source_data': {source_name: raw_dict}
    }
    """
    result = {
        'found': False,
        'name': name,
        'imo': imo,
        'mmsi': mmsi,
        'eni': eni,
        'flag': None,
        'ship_type': None,
        'length': None,
        'beam': None,
        'year_built': None,
        'builder': None,
        'position': None,
        'speed': None,
        'destination': None,
        'callsign': None,
        'sources': [],
        'source_data': {}
    }

    # 1. VesselFinder (free, no key — good for MMSI/name)
    vf = lookup_vesselfinder(name=name, mmsi=mmsi)
    if vf:
        result['sources'].append('vesselfinder')
        result['source_data']['vesselfinder'] = vf
        if not result['found']:
            result['found'] = True
        result['name'] = result['name'] or vf.get('name')
        result['mmsi'] = result['mmsi'] or vf.get('mmsi')
        result['imo'] = result['imo'] or vf.get('imo')
        result['ship_type'] = result['ship_type'] or vf.get('ship_type')
        result['position'] = result['position'] or vf.get('position')

    # 2. MarinePlan (needs name or MMSI + API key — richer data)
    lookup_name = result.get('name') or name
    if not lookup_name and mmsi:
        lookup_name = None  # use MMSI directly

    mp = lookup_marineplan(name=lookup_name, mmsi=mmsi)
    if mp:
        result['sources'].append('marineplan')
        result['source_data']['marineplan'] = mp
        result['found'] = True
        result['name'] = result['name'] or mp.get('name')
        result['mmsi'] = result['mmsi'] or mp.get('mmsi')
        result['ship_type'] = mp.get('ship_type')
        result['length'] = mp.get('length')
        result['beam'] = mp.get('beam')
        result['position'] = mp.get('position')
        result['speed'] = mp.get('speed')
        result['destination'] = mp.get('destination')
        result['callsign'] = mp.get('callsign')
        result['flag'] = result['flag'] or mp.get('flag')

    # 3. KVNR (IMO or name)
    kvnr = lookup_kvnr(imo=imo, name=result.get('name') or name)
    if kvnr:
        result['sources'].append('kvnr')
        result['source_data']['kvnr'] = kvnr
        if not result['found']:
            result['found'] = True
        result['name'] = result['name'] or kvnr.get('name')
        result['imo'] = result['imo'] or kvnr.get('imo')
        result['flag'] = result['flag'] or kvnr.get('flag')

    # 4. Binnenvaart.eu (ENI or name)
    bv = lookup_binnenvaart(eni=eni, name=result.get('name') or name)
    if bv:
        result['sources'].append('binnenvaart')
        result['source_data']['binnenvaart'] = bv
        if not result['found']:
            result['found'] = True
        result['name'] = result['name'] or bv.get('name')
        result['eni'] = result['eni'] or bv.get('eni')
        result['ship_type'] = result['ship_type'] or bv.get('ship_type')
        result['year_built'] = result['year_built'] or bv.get('year_built')
        result['builder'] = result['builder'] or bv.get('builder')

    # 5. DeBinnenvaart.nl (ENI or name, fallback if Binnenvaart.eu misses)
    if not bv:
        dbv = lookup_debinnenvaart(eni=eni, name=result.get('name') or name)
        if dbv:
            result['sources'].append('debinnenvaart')
            result['source_data']['debinnenvaart'] = dbv
            if not result['found']:
                result['found'] = True
            result['name'] = result['name'] or dbv.get('name')
            result['eni'] = result['eni'] or dbv.get('eni')
            result['ship_type'] = result['ship_type'] or dbv.get('ship_type')
            result['year_built'] = result['year_built'] or dbv.get('year_built')

    # 6. Equasis (IMO only, optional)
    eq = lookup_equasis(imo=result.get('imo') or imo)
    if eq:
        result['sources'].append('equasis')
        result['source_data']['equasis'] = eq
        if not result['found']:
            result['found'] = True
        result['name'] = result['name'] or eq.get('name')
        result['imo'] = result['imo'] or eq.get('imo')
        result['flag'] = result['flag'] or eq.get('flag')
        result['ship_type'] = result['ship_type'] or eq.get('ship_type')
        result['year_built'] = result['year_built'] or eq.get('year_built')

    return result


# ---------------------------------------------------------------------------
# Equasis (optional, requires free account credentials)
# ---------------------------------------------------------------------------

EQUASIS_LOGIN_URL = 'https://www.equasis.org/EquasisWeb/public/Login'
EQUASIS_SEARCH_URL = 'https://www.equasis.org/EquasisWeb/restricted/ShipResult'


def lookup_equasis(imo: str) -> Optional[dict]:
    """Look up vessel on Equasis by IMO (requires credentials in settings).

    Returns dict with keys: name, imo, flag, gt, dwt, year_built,
    ship_type, owner, manager, source, source_url
    or None if not found / no credentials.
    """
    email = _get_setting('equasis_email')
    password = _get_setting('equasis_password')
    if not email or not password:
        logger.info("Equasis credentials not configured")
        return None

    if not imo:
        return None

    session = http_requests.Session()
    session.headers.update({
        'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/124.0.0.0 Safari/537.36')
    })

    try:
        login_resp = session.post(EQUASIS_LOGIN_URL, data={
            'login': email,
            'password': password
        }, timeout=15)

        if login_resp.status_code != 200:
            logger.warning(f"Equasis login failed: {login_resp.status_code}")
            return None

        search_resp = session.get(EQUASIS_SEARCH_URL, params={
            'imo': imo,
            'searchType': 'ship'
        }, timeout=15)

        if search_resp.status_code != 200:
            logger.warning(f"Equasis search failed: {search_resp.status_code}")
            return None

        soup = BeautifulSoup(search_resp.text, 'lxml')

        result = {
            'imo': imo,
            'name': None,
            'flag': None,
            'gt': None,
            'dwt': None,
            'year_built': None,
            'ship_type': None,
            'owner': None,
            'manager': None,
            'source': 'equasis',
            'source_url': f'https://www.equasis.org/EquasisWeb/restricted/ShipResult?imo={imo}'
        }

        table = soup.select_one('table.result-table, .ship-details')
        if table:
            text = table.get_text(' | ', strip=True)
            result['name'] = _extract_after(text, 'Ship name', '|')
            result['flag'] = _extract_after(text, 'Flag', '|')
            result['gt'] = _extract_after(text, 'GT', '|')
            result['dwt'] = _extract_after(text, 'DWT', '|')
            result['year_built'] = _extract_after(text, 'Year of build', '|')
            result['ship_type'] = _extract_after(text, 'Type', '|')

        owner_table = soup.select_one('.owner-details, #owner-section')
        if owner_table:
            text = owner_table.get_text(' | ', strip=True)
            result['owner'] = _extract_after(text, 'Owner', '|')
            result['manager'] = _extract_after(text, 'Manager', '|')

        return result

    except http_requests.RequestException as e:
        logger.warning(f"Equasis request failed: {e}")
        return None


def _extract_after(text: str, label: str, delimiter: str = '|') -> Optional[str]:
    """Extract value after label in delimited text."""
    pattern = re.compile(re.escape(label) + r'\s*' + re.escape(delimiter) + r'\s*([^' + re.escape(delimiter) + r']+)', re.IGNORECASE)
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    return None
