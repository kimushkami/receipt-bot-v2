def escape_html(text: str) -> str:
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def is_weight_barcode(barcode: str) -> bool:
    digits = ''.join(filter(str.isdigit, barcode))
    return len(digits) >= 12 and digits.startswith('20')


def process_quantity(qty_str: str, is_weight: bool) -> str:
    if not is_weight:
        return qty_str.strip()

    normalized = qty_str.strip().replace(',', '.')

    if '.' in normalized:
        try:
            val = float(normalized)
            return _fmt_kg(val)
        except ValueError:
            return qty_str

    try:
        qty_int = int(normalized)
    except ValueError:
        return qty_str

    if qty_int >= 10:
        return _fmt_kg(qty_int / 1000)
    return str(qty_int)


def _fmt_kg(val: float) -> str:
    s = f"{val:.3f}"
    s = s.rstrip('0').rstrip('.')
    return s.replace('.', ',')


def _add_quantities(q1: str, q2: str, is_weight: bool) -> str:
    try:
        v1 = float(q1.replace(',', '.'))
        v2 = float(q2.replace(',', '.'))
        total = v1 + v2
        if is_weight:
            return _fmt_kg(total)
        if total == int(total):
            return str(int(total))
        return str(total).replace('.', ',')
    except ValueError:
        return q1


def _fmt_amount(amount: float) -> str:
    if amount == int(amount):
        return str(int(amount))
    return f"{amount:.2f}".replace('.', ',')


def _div13(amount: float) -> float:
    return round(amount / 1.3, 2)


def format_receipt_data(data: dict) -> str:
    items_raw = data.get('items', [])
    receipt_total_raw = data.get('receipt_total', -1)
    store_name = (data.get('store_name') or '').strip()
    date_str = (data.get('date') or '').strip()

    try:
        receipt_total = float(receipt_total_raw) if receipt_total_raw not in (-1, None, '') else -1
    except (ValueError, TypeError):
        receipt_total = -1

    processed = []
    for item in items_raw:
        barcode = str(item.get('barcode', 'НЕОПОЗНАНО')).strip()
        qty_str = str(item.get('quantity', '0')).strip()
        try:
            amount = float(item.get('amount', 0))
        except (ValueError, TypeError):
            amount = 0.0

        weight = is_weight_barcode(barcode)
        display_barcode = barcode[:6] if weight else barcode
        display_qty = process_quantity(qty_str, weight)

        processed.append({
            'barcode': display_barcode,
            'qty': display_qty,
            'amount': amount,
            'is_weight': weight,
        })

    merged: dict = {}
    order: list = []
    for item in processed:
        key = item['barcode']
        if key in merged:
            merged[key]['qty'] = _add_quantities(
                merged[key]['qty'], item['qty'], item['is_weight']
            )
            merged[key]['amount'] += item['amount']
        else:
            merged[key] = item.copy()
            order.append(key)

    header = "<b>№ - Баркод - Кол-во - Сумма - Сумма/1.3</b>"

    calculated_total = 0.0
    item_lines = []
    for i, key in enumerate(order, 1):
        item = merged[key]
        d13 = _div13(item['amount'])
        calculated_total += item['amount']

        line = (
            f"{i}) "
            f"<code>{escape_html(item['barcode'])}</code> - "
            f"<code>{escape_html(item['qty'])}</code> - "
            f"<code>{_fmt_amount(item['amount'])}</code> - "
            f"<code>{_fmt_amount(d13)}</code>"
        )
        item_lines.append(line)

    total_d13 = _div13(calculated_total)
    footer_lines = []
    footer_lines.append(f"<b>Итого:</b> <code>{_fmt_amount(calculated_total)}</code>")
    footer_lines.append(f"<b>Итого / 1.3:</b> <code>{_fmt_amount(total_d13)}</code>")

    if receipt_total > 0:
        diff = abs(calculated_total - receipt_total)
        if diff < 1.0:
            footer_lines.append("\n✅ Сверка пройдена")
        else:
            footer_lines.append(
                f"\n⚠️ Расхождение с чеком: "
                f"в чеке <code>{_fmt_amount(receipt_total)}</code>, "
                f"подсчитано <code>{_fmt_amount(calculated_total)}</code>"
            )

    # Разбиваем на чанки по 20 строк (лимит Telegram: 100 entities, 4 на строку)
    CHUNK = 20
    messages = []
    for start in range(0, len(item_lines), CHUNK):
        chunk = item_lines[start:start + CHUNK]
        if start == 0:
            messages.append('\n'.join([header, ''] + chunk))
        else:
            messages.append('\n'.join(chunk))

    if not messages:
        messages.append(header)

    messages.append('\n'.join(footer_lines))
    return messages
