import os
import logging
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)
from dotenv import load_dotenv
import storage
import moysklad
from analyzer import analyze_receipt_raw, analyze_receipt
from formatter import is_weight_barcode

load_dotenv()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
_media_groups: dict = {}


# ── HELPERS ───────────────────────────────────────────────────────────────────

def st(ud: dict) -> str:
    return ud.get('state', 'idle')

def set_st(ud: dict, s: str):
    ud['state'] = s

def _qty_float(qty_str: str, is_weight: bool) -> float:
    try:
        val = float(str(qty_str).replace(',', '.'))
    except ValueError:
        return 1.0
    if is_weight and '.' not in str(qty_str):
        if int(val) >= 10:
            return int(val) / 1000
    return val

def _fmt(n: float) -> str:
    return f"{round(n):,}".replace(',', ' ')


# ── KEYBOARDS ─────────────────────────────────────────────────────────────────

def _kb(items: list[dict], prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(i['name'], callback_data=f"{prefix}:{i['id']}")]
        for i in items
    ])

def _kb_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Создать документ", callback_data="confirm")],
        [InlineKeyboardButton("✏️ Изменить", callback_data="edit"),
         InlineKeyboardButton("📅 Изменить дату", callback_data="change_date")],
    ])

def _kb_edit() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏬 Магазин_тип списания", callback_data="edit:shoptype")],
        [InlineKeyboardButton("📅 Дата", callback_data="edit:date")],
        [InlineKeyboardButton("◀️ Назад к сводке", callback_data="edit:back")],
    ])

def _kb_settings() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏢 Изменить организацию", callback_data="settings:org")],
        [InlineKeyboardButton("🏪 Изменить склад", callback_data="settings:store")],
        [InlineKeyboardButton("📂 Изменить статью расходов", callback_data="settings:expense")],
    ])


# ── SUMMARY ───────────────────────────────────────────────────────────────────

def _summary_text(ud: dict) -> str:
    profile = ud.get('profile', {})
    shop = ud.get('shop_type', {})
    moment: datetime = ud.get('moment', datetime.now().replace(hour=23, minute=59, second=0))
    items = ud.get('resolved_items', [])

    lines = [
        "📋 <b>Сводка — Списание</b>", "",
        f"🏢 {profile.get('org_name', '—')}",
        f"🏪 {profile.get('store_name', '—')}",
        f"📂 {profile.get('expense_name', 'Списания')}",
        f"🏬 {shop.get('name', '—')}",
        f"📅 {moment.strftime('%d.%m.%Y')} 23:59",
        "", f"<b>Позиции ({len(items)}):</b>",
    ]

    total = 0.0
    for item in items:
        if item['found']:
            cost = item['amount'] / 1.3
            total += cost
            uom = f" {item['uom_name']}" if item.get('uom_name') else ""
            qty = item['qty']
            qty_str = str(int(qty)) if qty == int(qty) else str(qty)
            lines.append(f"✅ {item['product_name']} — {qty_str}{uom} — {_fmt(cost)} ₩")
        else:
            lines.append(f"❌ Баркод <code>{item['barcode']}</code> не найден")

    lines += ["", f"<b>Итого (себест.): {_fmt(total)} ₩</b>"]
    return "\n".join(lines)


async def _send_summary(msg, ud: dict, edit: bool = False):
    text = _summary_text(ud)
    if edit:
        await msg.edit_text(text, parse_mode='HTML', reply_markup=_kb_confirm())
    else:
        if len(text) > 3900:
            await msg.reply_text(text[:3900] + "\n...", parse_mode='HTML')
            await msg.reply_text(text[3900:], parse_mode='HTML', reply_markup=_kb_confirm())
        else:
            await msg.reply_text(text, parse_mode='HTML', reply_markup=_kb_confirm())


# ── SETUP ─────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    profile = storage.get_user(uid)
    ud = ctx.user_data
    if profile and profile.get('org_href') and profile.get('store_href'):
        ud['profile'] = profile
        set_st(ud, 'idle')
        await update.message.reply_text(
            f"С возвращением!\n\n"
            f"🏢 {profile['org_name']}\n"
            f"🏪 {profile['store_name']}\n\n"
            f"Отправьте фото чека."
        )
    else:
        await _setup_start_org(update.message, ctx.user_data)


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    profile = storage.get_user(uid) or {}
    await update.message.reply_text(
        f"⚙️ Текущие настройки:\n\n"
        f"🏢 Организация: {profile.get('org_name', 'не задана')}\n"
        f"🏪 Склад: {profile.get('store_name', 'не задан')}\n"
        f"📂 Статья расходов: {profile.get('expense_name', 'Списания')}\n\n"
        f"Что изменить?",
        reply_markup=_kb_settings()
    )


async def _setup_start_org(msg, ud: dict):
    try:
        orgs = await moysklad.get_organizations()
    except Exception as e:
        await msg.reply_text(f"❌ Ошибка подключения к МоёмуСкладу:\n<code>{e}</code>", parse_mode='HTML')
        return
    if not orgs:
        await msg.reply_text("❌ Организации не найдены. Проверьте MOYSKLAD_TOKEN.")
        return
    ud['_orgs'] = {o['id']: o for o in orgs}
    await msg.reply_text("⚙️ Настройка профиля\n\nВыберите организацию:", reply_markup=_kb(orgs, 'org'))
    set_st(ud, 'setup_org')


# ── PHOTO ─────────────────────────────────────────────────────────────────────

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ud = ctx.user_data
    if st(ud) not in ('idle',):
        await update.message.reply_text("⏳ Подождите, идёт обработка.")
        return

    msg = update.message
    photo = msg.photo[-1]
    file = await ctx.bot.get_file(photo.file_id)
    image_bytes = bytes(await file.download_as_bytearray())
    mgid = msg.media_group_id

    if mgid:
        if mgid not in _media_groups:
            _media_groups[mgid] = {'images': [], 'message': msg}
        _media_groups[mgid]['images'].append(image_bytes)
        if ud.get('_mg_task') and not ud['_mg_task'].done():
            ud['_mg_task'].cancel()
        ud['_mg_task'] = asyncio.create_task(_process_group(mgid, ctx.bot, ud))
        set_st(ud, 'collecting')
    else:
        set_st(ud, 'analyzing')
        await _do_analyze(msg, ctx.bot, ud, [image_bytes])


async def _process_group(mgid: str, bot, ud: dict):
    await asyncio.sleep(2)
    group = _media_groups.pop(mgid, None)
    if group:
        await _do_analyze(group['message'], bot, ud, group['images'])


async def _do_analyze(msg, bot, ud: dict, images: list[bytes]):
    set_st(ud, 'analyzing')
    n = len(images)
    status = await msg.reply_text(
        f"⏳ Анализирую чек ({n} фото)..." if n > 1 else "⏳ Анализирую чек..."
    )
    try:
        data = await analyze_receipt_raw(images)
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        await status.edit_text(f"❌ Ошибка анализа: {e}")
        set_st(ud, 'idle')
        return

    await status.delete()
    ud['receipt_data'] = data

    try:
        attr = await moysklad.get_loss_shop_type_attr()
        if not attr:
            raise ValueError("Атрибут 'Магазин_тип списания' не найден")
        ud['_shop_attr'] = attr
        entity_href = attr.get('customEntityMeta', {}).get('href', '')
        values = await moysklad.get_custom_entity_values(entity_href)
        ud['_shop_values'] = {v['id']: v for v in values}
    except Exception as e:
        logger.error(f"Shop type load error: {e}")
        await msg.reply_text(f"❌ Ошибка загрузки Магазин_тип: {e}")
        set_st(ud, 'idle')
        return

    await msg.reply_text(
        "Выберите Магазин_тип списания:",
        reply_markup=_kb(list(ud['_shop_values'].values()), 'shoptype')
    )
    set_st(ud, 'selecting_shoptype')


# ── CALLBACKS ─────────────────────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    ud = ctx.user_data
    uid = update.effective_user.id
    state = st(ud)

    # Settings menu
    if d == 'settings:org':
        orgs = await moysklad.get_organizations()
        ud['_orgs'] = {o['id']: o for o in orgs}
        await q.edit_message_text("Выберите организацию:", reply_markup=_kb(orgs, 'org'))
        set_st(ud, 'setup_org')
        return

    if d == 'settings:store':
        stores = await moysklad.get_stores()
        ud['_stores'] = {s['id']: s for s in stores}
        await q.edit_message_text("Выберите склад:", reply_markup=_kb(stores, 'store'))
        set_st(ud, 'setup_store')
        return

    if d == 'settings:expense':
        articles = await moysklad.get_expense_articles()
        ud['_expenses'] = {a['id']: a for a in articles}
        await q.edit_message_text("Выберите статью расходов:", reply_markup=_kb(articles, 'expense'))
        set_st(ud, 'setup_expense')
        return

    # Setup: org chosen
    if d.startswith('org:') and state == 'setup_org':
        oid = d.split(':', 1)[1]
        org = ud['_orgs'][oid]
        storage.upsert_user(uid, org_name=org['name'], org_href=org['meta']['href'])
        stores = await moysklad.get_stores()
        ud['_stores'] = {s['id']: s for s in stores}
        await q.edit_message_text("Выберите склад:", reply_markup=_kb(stores, 'store'))
        set_st(ud, 'setup_store')
        return

    # Setup: store chosen
    if d.startswith('store:') and state == 'setup_store':
        sid = d.split(':', 1)[1]
        store = ud['_stores'][sid]
        storage.upsert_user(uid, store_name=store['name'], store_href=store['meta']['href'])
        profile = storage.get_user(uid)
        if not profile.get('expense_href'):
            articles = await moysklad.get_expense_articles()
            exp = next((a for a in articles if 'списани' in a['name'].lower()), articles[0] if articles else None)
            if exp:
                storage.upsert_user(uid, expense_name=exp['name'], expense_href=exp['meta']['href'])
        profile = storage.get_user(uid)
        ud['profile'] = profile
        await q.edit_message_text(
            f"✅ Настройка сохранена!\n\n"
            f"🏢 {profile['org_name']}\n"
            f"🏪 {profile['store_name']}\n"
            f"📂 {profile.get('expense_name', 'Списания')}\n\n"
            f"Отправьте фото чека."
        )
        set_st(ud, 'idle')
        return

    # Setup: expense chosen
    if d.startswith('expense:') and state == 'setup_expense':
        eid = d.split(':', 1)[1]
        exp = ud['_expenses'][eid]
        storage.upsert_user(uid, expense_name=exp['name'], expense_href=exp['meta']['href'])
        profile = storage.get_user(uid)
        ud['profile'] = profile
        await q.edit_message_text(
            f"✅ Статья расходов: <b>{exp['name']}</b>\n\nОтправьте фото чека.",
            parse_mode='HTML'
        )
        set_st(ud, 'idle')
        return

    # Shop type chosen
    if d.startswith('shoptype:') and state == 'selecting_shoptype':
        vid = d.split(':', 1)[1]
        val = ud['_shop_values'][vid]
        attr = ud['_shop_attr']
        ud['shop_type'] = {
            'name': val['name'],
            'attr_href': attr['meta']['href'],
            'val_href': val['meta']['href'],
        }
        ud['moment'] = datetime.now().replace(hour=23, minute=59, second=0, microsecond=0)
        await q.edit_message_text("⏳ Ищу товары в МоёмСкладе...")
        await _resolve_items(ud)
        if not ud.get('profile'):
            ud['profile'] = storage.get_user(uid) or {}
        await _send_summary(q.message, ud)
        set_st(ud, 'confirming')
        return

    # Confirm → create
    if d == 'confirm' and state == 'confirming':
        await q.edit_message_text("⏳ Создаю документ в МоёмСкладе...")
        await _create_document(q.message, ud, uid)
        return

    # Edit menu
    if d == 'edit' and state == 'confirming':
        await q.edit_message_text("Что изменить?", reply_markup=_kb_edit())
        set_st(ud, 'editing')
        return

    if d == 'change_date' and state == 'confirming':
        moment = ud.get('moment', datetime.now())
        await q.edit_message_text(
            f"Текущая дата: <b>{moment.strftime('%d.%m.%Y')}</b>\n\n"
            f"Введите новую дату в формате дд.мм.гггг:",
            parse_mode='HTML'
        )
        set_st(ud, 'editing_date')
        return

    if d == 'edit:shoptype' and state == 'editing':
        values = list(ud.get('_shop_values', {}).values())
        await q.edit_message_text("Выберите Магазин_тип списания:", reply_markup=_kb(values, 'shoptype'))
        set_st(ud, 'selecting_shoptype')
        return

    if d == 'edit:date' and state == 'editing':
        moment = ud.get('moment', datetime.now())
        await q.edit_message_text(
            f"Текущая дата: <b>{moment.strftime('%d.%m.%Y')}</b>\n\n"
            f"Введите новую дату в формате дд.мм.гггг:",
            parse_mode='HTML'
        )
        set_st(ud, 'editing_date')
        return

    if d == 'edit:back' and state == 'editing':
        if not ud.get('profile'):
            ud['profile'] = storage.get_user(uid) or {}
        await _send_summary(q.message, ud, edit=True)
        set_st(ud, 'confirming')
        return


# ── TEXT ──────────────────────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ud = ctx.user_data
    if st(ud) != 'editing_date':
        return
    uid = update.effective_user.id
    try:
        d = datetime.strptime(update.message.text.strip(), '%d.%m.%Y')
        ud['moment'] = d.replace(hour=23, minute=59, second=0, microsecond=0)
        if not ud.get('profile'):
            ud['profile'] = storage.get_user(uid) or {}
        await _send_summary(update.message, ud)
        set_st(ud, 'confirming')
    except ValueError:
        await update.message.reply_text("Неверный формат. Введите дату как дд.мм.гггг (например, 02.06.2026):")


# ── RESOLVE + CREATE ──────────────────────────────────────────────────────────

async def _resolve_items(ud: dict):
    items = ud.get('receipt_data', {}).get('items', [])
    resolved = []
    for item in items:
        barcode = str(item.get('barcode', '')).strip()
        qty_str = str(item.get('quantity', '1'))
        try:
            amount = float(item.get('amount', 0))
        except (ValueError, TypeError):
            amount = 0.0

        is_weight = is_weight_barcode(barcode)
        display_barcode = barcode[:6] if is_weight else barcode
        qty = _qty_float(qty_str, is_weight)

        product = await moysklad.find_by_barcode(display_barcode)
        resolved.append({
            'barcode': display_barcode,
            'qty': qty,
            'amount': amount,
            'found': product is not None,
            'product_name': product.get('name') if product else None,
            'product_href': product['meta']['href'] if product else None,
            'uom_href': (product.get('uom') or {}).get('meta', {}).get('href') if product else None,
            'uom_name': (product.get('uom') or {}).get('name', '') if product else '',
        })
    ud['resolved_items'] = resolved


async def _create_document(msg, ud: dict, uid: int):
    profile = ud.get('profile') or storage.get_user(uid) or {}
    shop = ud.get('shop_type', {})
    moment: datetime = ud.get('moment', datetime.now().replace(hour=23, minute=59, second=0))
    items = ud.get('resolved_items', [])

    positions = []
    for item in items:
        if not item['found']:
            continue
        qty = item['qty']
        unit_cost = (item['amount'] / qty / 1.3) if qty else 0
        positions.append({
            'product_href': item['product_href'],
            'qty': qty,
            'unit_cost': unit_cost,
            'uom_href': item.get('uom_href'),
        })

    try:
        result = await moysklad.create_loss(
            org_href=profile['org_href'],
            store_href=profile['store_href'],
            expense_href=profile.get('expense_href', ''),
            moment=moment.strftime('%Y-%m-%d %H:%M:%S'),
            shop_attr_href=shop.get('attr_href', ''),
            shop_val_href=shop.get('val_href', ''),
            positions=positions,
        )
        name = result.get('name', '—')
        doc_moment = result.get('moment', '')
        try:
            dt = datetime.fromisoformat(doc_moment[:19])
            doc_moment = dt.strftime('%d.%m.%Y %H:%M')
        except Exception:
            pass

        not_found = [i for i in items if not i['found']]
        text = f"✅ <b>Документ создан!</b>\n\nНомер: <b>{name}</b>\nДата: <b>{doc_moment}</b>"
        if not_found:
            text += f"\n\n⚠️ Не найдено в МоёмСкладе ({len(not_found)} поз.):\n"
            text += "\n".join(f"— <code>{i['barcode']}</code>" for i in not_found)
        await msg.reply_text(text, parse_mode='HTML')

    except Exception as e:
        logger.error(f"Create loss error: {e}")
        await msg.reply_text(f"❌ Ошибка создания документа:\n<code>{e}</code>", parse_mode='HTML')
    finally:
        set_st(ud, 'idle')


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN не задан")
    storage.init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CommandHandler('settings', cmd_settings))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))
    logger.info("Бот запущен")
    app.run_polling()


if __name__ == '__main__':
    main()
