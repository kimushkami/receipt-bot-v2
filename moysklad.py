import os
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

BASE = 'https://api.moysklad.ru/api/remap/1.2'
TIMEOUT = 30


def _headers() -> dict:
    return {
        'Authorization': f'Bearer {os.environ["MOYSKLAD_TOKEN"]}',
        'Content-Type': 'application/json',
    }


async def _get(url: str, **params) -> dict:
    full_url = url if url.startswith('http') else f'{BASE}{url}'
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(full_url, headers=_headers(), params=params or None)
        r.raise_for_status()
        return r.json()


async def _post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f'{BASE}{path}', headers=_headers(), json=body)
        r.raise_for_status()
        return r.json()


async def get_organizations() -> list[dict]:
    data = await _get('/entity/organization', limit=100)
    logger.info(f"Organizations response keys: {list(data.keys())}, rows count: {len(data.get('rows', []))}")
    return data.get('rows', [])


async def get_stores() -> list[dict]:
    return (await _get('/entity/store', limit=100))['rows']


async def get_expense_articles() -> list[dict]:
    return (await _get('/entity/expensearticle', limit=100))['rows']


async def get_loss_shop_type_attr() -> Optional[dict]:
    data = await _get('/entity/loss/metadata')
    attrs = data.get('attributes', [])
    if isinstance(attrs, dict):
        attrs = attrs.get('rows', [])
    for attr in attrs:
        if 'магазин' in attr.get('name', '').lower():
            return attr
    return None


async def get_custom_entity_values(meta_href: str) -> list[dict]:
    # meta_href: .../context/companysettings/metadata/customEntities/{id}
    entity_id = meta_href.rstrip('/').split('/')[-1]
    data = await _get(f'/entity/customentity/{entity_id}', limit=100)
    return data.get('rows', [])


async def find_by_barcode(barcode: str) -> Optional[dict]:
    try:
        data = await _get('/entity/assortment', filter=f'barcode={barcode}', limit=5)
        rows = data.get('rows', [])
        return rows[0] if rows else None
    except Exception:
        return None


async def create_loss(
    org_href: str,
    store_href: str,
    expense_href: str,
    moment: str,
    shop_attr_href: str,
    shop_val_href: str,
    positions: list[dict],
) -> dict:
    body = {
        'organization': {'meta': {'href': org_href, 'type': 'organization', 'mediaType': 'application/json'}},
        'store':        {'meta': {'href': store_href, 'type': 'store', 'mediaType': 'application/json'}},
        'moment': moment,
        'attributes': [{
            'meta':  {'href': shop_attr_href, 'type': 'attributemetadata', 'mediaType': 'application/json'},
            'value': {'meta': {'href': shop_val_href, 'mediaType': 'application/json'}},
        }],
        'positions': [],
    }
    if expense_href:
        body['expenseItem'] = {'meta': {'href': expense_href, 'type': 'expensearticle', 'mediaType': 'application/json'}}
    for p in positions:
        if not p.get('product_href'):
            continue
        pos = {
            'assortment': {'meta': {'href': p['product_href'], 'mediaType': 'application/json'}},
            'quantity': p['qty'],
            'price': round(p['unit_cost'] * 100),  # МойСклад хранит цены в копейках
        }
        if p.get('uom_href'):
            pos['uom'] = {'meta': {'href': p['uom_href'], 'mediaType': 'application/json'}}
        body['positions'].append(pos)
    return await _post('/entity/loss', body)
