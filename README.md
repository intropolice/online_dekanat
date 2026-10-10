# Онлайн-деканат

Сервис по ТЗ из кейса: принимает фото, скан или PDF студенческого документа и вопрос на русском, распознает текст, заполняет карточку полей и отвечает по локальному Положению со ссылкой на пункт или поле документа.

Проект не использует внешние API. Основной OCR работает локально на `Qwen/Qwen2.5-VL-3B-Instruct`, при недоступности Qwen автоматически выбирается Tesseract, тестовый режим - готовый `.txt` рядом с документом. У Qwen2.5-VL нет модели ровно на 2.5B параметров, поэтому используется компактная vision-модель на 3B.

## Что реализовано

- OCR-пайплайн для `JPG`, `PNG`, `PDF`.
- Три типа документов из ТЗ:
  - справка об обучении;
  - заявление на академический отпуск;
  - справка о практике.
- Ручной выбор типа документа или автоопределение по ключевым словам.
- Извлечение полей по конфигу без правки кода.
- Ответы трех ожидаемых типов: документ в порядке, чего-то не хватает, ответа нет.
- Тип документа и OCR-бэкенд выбираются автоматически.
- Загруженные документы сохраняются в зашифрованном хранилище Fernet; приложение само открывает их только на время OCR и удаляет временную расшифрованную копию.
- Источник ответа: пункт Положения и/или поля документа.
- CLI, HTTP API и простая веб-форма.
- Загрузчик `MTSAIR/MWS-Vision-Bench` для smoke-прогонов и подготовки локальных выборок.

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .[dev,dataset,tesseract]
```

Для локального OCR на Qwen:

```powershell
python -m pip install -e .[qwen]
```

При первом распознавании Transformers скачает модель с Hugging Face (примерно несколько гигабайт); следующие запуски используют локальный кэш. Для приемлемой скорости рекомендуется GPU с 8 ГБ памяти. На CPU распознавание тоже доступно, но может занять несколько минут и требует достаточно оперативной памяти. Внешние API не вызываются. Используется `Qwen2.5-VL-3B-Instruct` с `transformers` и `qwen-vl-utils`.

Для Tesseract установите бинарник отдельно и добавьте русский язык `rus`.

Ключ хранилища создаётся в `data/secure/.storage.key` при первом запуске. Для переноса приложения задайте тот же ключ через переменную окружения `ONLINE_DEKANAT_STORAGE_KEY` (URL-safe base64 ключ Fernet). Без ключа зашифрованные документы не расшифровываются.

## Запуск CLI

```powershell
online-dekanat process --file samples/study_certificate.txt --question "Хватит ли справки для перевода на 2 курс?" --ocr text
```

С реальным изображением и Qwen:

```powershell
online-dekanat process --file path\to\scan.jpg --question "Подходит ли документ?" --ocr qwen
```

С явным типом документа:

```powershell
online-dekanat process --file path\to\scan.pdf --question "Можно оформить академический отпуск?" --doc-type academic_leave_application
```

## Веб-интерфейс

```powershell
online-dekanat serve --host 127.0.0.1 --port 8000
```

Откройте `http://127.0.0.1:8000/ui`.

## API

```http
POST /process
Content-Type: multipart/form-data

file=<jpg|png|pdf|txt>
question=...
doc_type=study_certificate
ocr=auto|text|tesseract|qwen
```

Ответ содержит `card`, `answer`, `sources`, `missing_fields` и `raw_text`.

## MWS-Vision-Bench

Датасет `MTSAIR/MWS-Vision-Bench` содержит 1302 русскоязычных примера, 400 уникальных изображений и 5 типов задач: OCR, document parsing, KIE, VQA и grounding. Он используется здесь как открытый benchmark для проверки OCR/парсинга, а не как источник правил деканата.

Скачать небольшую локальную выборку:

```powershell
online-dekanat mws-sample --limit 20 --task-type "full-page OCR ru" --out data/mws_sample.jsonl
```

## Формат results.json

Для закрытой проверки можно писать массив объектов:

```json
[
  {
    "id": "test-001",
    "doc_type": "study_certificate",
    "fields": {
      "student_name": "Иванов Иван Иванович"
    },
    "answer": "Справка содержит все данные, которые требует п. 3.2 Положения...",
    "sources": ["п. 3.2 Положения", "поле: Подпись и печать"]
  }
]
```

## Стек

- Python 3.10+
- FastAPI + Uvicorn
- PyMuPDF + Pillow для PDF/изображений
- Qwen2.5-VL-3B-Instruct через `transformers` как основной OCR
- Tesseract как локальный CPU-фолбэк
- Hugging Face `datasets` для MWS-Vision-Bench

## Тесты

```powershell
python -m pytest
```
