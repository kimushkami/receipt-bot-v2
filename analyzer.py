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

Извлеки ВСЕ позиции товаров из чека и верни ТОЛЬКО валидный JSON объект строго в этом формате (без пояснений, без markdown, только JSON):

{
  "store_name": "название магазина или null",
  "date": "дата покупки как строка или null",
  "items": [
    {
      "barcode": "полный баркод как строка",
      "quantity": "количество точно как указано в чеке (напр. '867' или '1.44' или '2')",
      "amount": 4500
    }
  ],
  "receipt_total": 456567
}

Правила:
- Включи КАЖДУЮ позицию товара из чека
- Баркод — точно как напечатано, все цифры
- Quantity — строго как в чеке: если целое число — строка без точки ("867"), если десятичное — с точкой ("1.44")
- Amount — итоговая сумма за строку (количество × цена), число
- receipt_total — итоговая сумма чека, число
- Если данные не читаются — для строк пиши "НЕОПОЗНАНО", для чисел -1
- Не пропускай позиции даже если баркод плохо виден"""


async def analyze_receipt(images: list[bytes]) -> str:
    max_attempts = 3

    for try_num in range(1, max_attempts + 1):
        try:
            logger.info(f"Попытка анализа {try_num}/{max_attempts}, фото: {len(images)}")

            content = []
            for image_bytes in images:
                image_b64 = base64.standard_b64encode(image_bytes).decode('utf-8')
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_b64,
                    },
                })
            content.append({"type": "text", "text": PROMPT})

            message = await client.messages.create(
                model="claude-sonnet-4-6",
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

            return format_receipt_data(data)

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Попытка {try_num} не удалась: {e}")
            if try_num == max_attempts:
                return (
                    "❌ Не удалось распознать чек после нескольких попыток.\n"
                    "Попробуйте сфотографировать чек ещё раз: лучше освещение и без размытия."
                )
        except anthropic.APIError as e:
            logger.error(f"Ошибка Claude API: {e}")
            raise

    return "❌ Неопознанные данные. Пожалуйста, попробуйте ещё раз."
