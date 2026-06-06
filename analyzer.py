import anthropic
import base64
import os
import json
import logging
from formatter import format_receipt_data

logger = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

PROMPT = """Ты анализируешь корейский чек из магазина. На чеке данные о сумме, цене, количестве написаны на корейском, товары преимущественно на русском или английском языке.
Если отправлено несколько фото — это части одного длинного чека, объедини все позиции в один список.

Структура чека: каждый товар занимает 2 строки.
1-я строка: баркод, затем по горизонтали три числа — 단가 (цена за 1 ед.), 수량 (количество), 금액 (итоговая сумма)
2-я строка: название товара

Тебе нужно:
- quantity = значение из столбца 수량 (количество штук или граммов)
- amount = значение из столбца 금액 (итоговая сумма за строку)
- НЕ используй 단가 (цена за единицу) — это не количество и не сумма

ВАЖНО — на строке всегда ровно 3 отдельных числа: 단가, 수량, 금액
Между ними есть визуальный пробел, даже если он небольшой.
Никогда не объединяй два соседних числа в одно — если видишь пробел между цифрами, это два разных значения.
Например: "36   268" — это два числа: 36 и 268, а не 36268.

Извлеки ВСЕ позиции и верни ТОЛЬКО валидный JSON без пояснений и markdown:

{
  "store_name": "название магазина или null",
  "date": "дата покупки как строка или null",
  "items": [
    {
      "barcode": "полный баркод как строка",
      "quantity": "значение из столбца 수량 точно как в чеке ('867' или '1.44' или '2')",
      "amount": 4500
    }
  ],
  "receipt_total": 456567
}

Правила:
- Включи КАЖДУЮ позицию товара из чека
- Баркод — считай каждую цифру по одной. Стандартный баркод EAN-13 содержит ровно 13 цифр. Если получилось 14 — пересчитай.
- quantity — строго из столбца 수량: если целое — строка без точки ("2"), если десятичное — с точкой ("1.44")
- amount — строго из столбца 금액, число
- receipt_total — итоговая сумма чека, число
- Если данные не читаются — для строк "НЕОПОЗНАНО", для чисел -1
- Не пропускай позиции даже если баркод плохо виден"""


async def reanalyze_barcode(images: list[bytes], prev_barcode: str, product_name: str = '') -> str:
    """Re-read a specific barcode from the receipt. Returns new barcode or prev_barcode if unchanged."""
    hint = f" Под баркодом написано название товара: '{product_name}'." if product_name else ""
    prompt = (
        f"Посмотри на чек внимательно.{hint}\n"
        f"Я прочитал баркод как: {prev_barcode}\n"
        f"Этот товар не найден в базе — возможно я ошибся в цифрах.\n\n"
        f"Очень внимательно перечитай этот баркод цифра за цифрой.\n"
        f"Стандартный баркод EAN-13 содержит ровно 13 цифр.\n"
        f"Верни ТОЛЬКО цифры баркода, больше ничего."
    )
    content = []
    for image_bytes in images:
        image_b64 = base64.standard_b64encode(image_bytes).decode('utf-8')
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}})
    content.append({"type": "text", "text": prompt})
    try:
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=64,
            messages=[{"role": "user", "content": content}],
        )
        result = ''.join(filter(str.isdigit, message.content[0].text.strip()))
        return result if result else prev_barcode
    except Exception:
        return prev_barcode


async def analyze_receipt_raw(images: list[bytes]) -> dict:
    max_attempts = 3
    for try_num in range(1, max_attempts + 1):
        try:
            logger.info(f"Попытка анализа {try_num}/{max_attempts}, фото: {len(images)}")
            content = []
            for image_bytes in images:
                image_b64 = base64.standard_b64encode(image_bytes).decode('utf-8')
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
                })
            content.append({"type": "text", "text": PROMPT})

            message = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                messages=[{"role": "user", "content": content}],
            )

            response_text = message.content[0].text.strip()
            logger.info(f"Ответ от Claude: {response_text[:200]}...")

            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            if json_start == -1 or json_end == 0:
                raise ValueError("JSON не найден в ответе")

            data = json.loads(response_text[json_start:json_end])
            if not data.get('items'):
                raise ValueError("Список товаров пуст")
            return data

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Попытка {try_num} не удалась: {e}")
            if try_num == max_attempts:
                raise ValueError("Не удалось распознать чек после нескольких попыток") from e
        except anthropic.APIError as e:
            logger.error(f"Ошибка Claude API: {e}")
            raise

    raise ValueError("Неопознанные данные")


async def analyze_receipt(images: list[bytes]) -> list[str]:
    data = await analyze_receipt_raw(images)
    return format_receipt_data(data)
