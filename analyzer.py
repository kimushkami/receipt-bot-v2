import anthropic
import base64
import os
import json
import logging
from formatter import format_receipt_data

logger = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

PROMPT = """Ты анализируешь корейский кассовый чек. Если фото несколько — это части одного чека, объедини все позиции.

Структура каждого товара в чеке — 2 строки:
1-я строка: баркод, затем через пробелы три числа: 단가 (цена за 1 ед.) | 수량 (количество) | 금액 (итог за строку)
2-я строка: название товара

Тебе нужно извлечь: quantity = из столбца 수량, amount = из столбца 금액.
НЕ используй 단가 — это цена за единицу, не количество.

КРИТИЧЕСКИ ВАЖНО про числа на строке:
Между тремя числами есть видимые пробелы в напечатанном тексте. Пробел = граграница между числами.
НИКОГДА не объединяй два числа разделённых пробелом в одно.
Каждый пробел между цифрами означает что это два отдельных числа.
Примеры правильного разделения:
  "7,000  2  14,000" → 단가=7000, 수량=2, 금액=14000
  "36  268  9,648" → 단가=36, 수량=268, 금액=9648
  "1,200  12  14,400" → 단가=1200, 수량=12, 금액=14400

Верни ТОЛЬКО валидный JSON без пояснений и markdown:

{
  "store_name": "название магазина или null",
  "date": "дата покупки как строка или null",
  "items": [
    {
      "barcode": "баркод строкой",
      "quantity": "значение из 수량 точно как в чеке ('2' или '268' или '1.44')",
      "amount": 14000
    }
  ],
  "receipt_total": 456567
}

Правила:
- Включи КАЖДУЮ позицию
- Баркод: считай цифры по одной. EAN-13 = ровно 13 цифр. Если 14 — пересчитай.
- quantity: строго из столбца 수량, строка без лишних символов
- amount: строго из столбца 금액, число без запятых
- receipt_total: итоговая сумма чека
- Нечитаемое: строки → "НЕОПОЗНАНО", числа → -1"""


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
