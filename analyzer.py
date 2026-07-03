import anthropic
import base64
import os
import json
import logging
from formatter import format_receipt_data

logger = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

PROMPT = """Ты обрабатываешь фото корейского кассового чека. Верни ТОЛЬКО валидный JSON без пояснений и markdown.

ФОРМАТ ОТВЕТА:
{
  "store_name": "название магазина или null",
  "date": "дата как строка или null",
  "items": [
    {
      "barcode": "баркод",
      "quantity": "количество",
      "amount": 4500
    }
  ],
  "receipt_total": 456567
}

ПРАВИЛА:

1. ВЕСОВЫЕ БАРКОДЫ (начинаются на "20"):
   - В поле barcode писать только первые 6 цифр
   - Quantity (вес):
     * Если в чеке 3 цифры без точки (напр. 867) → писать "0.867"
     * Если в чеке цифры с запятой/точкой (напр. 1,44) → писать "1.44"

2. ОДИНАКОВЫЕ БАРКОДЫ — объединять в одну строку:
   - quantity суммировать
   - amount суммировать

3. НЕСКОЛЬКО ФОТО — это части одного длинного чека:
   - Объединить все позиции в один список
   - Исключить дублирующиеся строки на границах фото

4. ОСТАЛЬНЫЕ БАРКОДЫ:
   - Писать полностью как напечатано
   - Quantity — строго как в чеке: целое число без точки ("2"), десятичное с точкой ("1.44")
   - Amount — итоговая сумма за строку, число

5. Включить КАЖДУЮ позицию товара, не пропускать даже если баркод плохо виден
6. Если данные не читаются — для строк "НЕОПОЗНАНО", для чисел -1
7. receipt_total — итоговая сумма всего чека"""


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

            return data

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Попытка {try_num} не удалась: {e}")
            if try_num == max_attempts:
                raise ValueError("Не удалось распознать чек после нескольких попыток")
        except anthropic.APIError as e:
            logger.error(f"Ошибка Claude API: {e}")
            raise

    return {}


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
