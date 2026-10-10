# Онлайн-деканат

Локальный помощник для проверки студенческих документов. Приложение принимает файл и вопрос на русском языке, распознаёт документ, извлекает известные поля и сверяет их с локальным Положением. Результат включает ответ, найденные поля и источники проверки.

OCR и логика проверки выполняются на компьютере пользователя: приложение не отправляет документы во внешние API. Для распознавания доступны PaddleOCR, EasyOCR и Tesseract. Режим `auto` последовательно пробует эти движки; для демонстрации и тестов доступен режим чтения текста из `.txt`.

> Установщик загружает библиотеки Python и языковые данные Tesseract. OCR-движки могут отдельно загрузить свои модели при первом распознавании; для PaddleOCR и EasyOCR это могут быть крупные файлы.

## Возможности

- Приём сканов и изображений `JPG`, `PNG`, а также PDF.
- Определение типа документа по тексту либо явный выбор типа в CLI/API:
  - `study_certificate` — справка об обучении;
  - `academic_leave_application` — заявление на академический отпуск;
  - `practice_certificate` — справка о практике.
- Извлечение полей, перечисленных в `config/document_types.json`.
- Сопоставление вопроса с правилами из `config/regulations.json`.
- Веб-интерфейс, HTTP API и командная строка.
- Шифрование получаемых через API файлов при хранении; расшифрованный временный файл удаляется после обработки.
- Локальная обработка без внешних API.

## Требования

- Windows 10/11 для `setup.bat`; для ручной установки нужен Python 3.10 или новее.
- Интернет для первоначальной установки библиотек, Tesseract и языковых данных. Интернет также может понадобиться при первом запуске OCR-движка для загрузки его модели.
- Свободное место на диске: зависимости OCR, особенно PaddleOCR и EasyOCR, могут занимать несколько гигабайт.

## Установка и запуск в Windows

### 1. Получите проект

Если проект уже открыт в VS Code, откройте терминал в его корневой папке — там, где находятся `pyproject.toml` и `setup.bat`.

При необходимости клонируйте репозиторий:

```powershell
git clone https://github.com/intropolice/online_dekanat.git
cd online_dekanat
```

### 2. Установите зависимости

Дважды щёлкните `setup.bat` или запустите его из PowerShell:

```powershell
.\setup.bat
```

Скрипт создаёт локальное окружение `.venv`, устанавливает проект с наборами `dev`, `dataset` и `ocr`, а также настраивает Tesseract с английскими и русскими языковыми данными. Если Tesseract отсутствует, установщик пытается поставить его через `winget`.

После завершения закройте и снова откройте терминал (или VS Code), чтобы применились переменные среды Tesseract.

### 3. Запустите веб-приложение

В новом терминале из корневой папки проекта выполните:

```powershell
.\.venv\Scripts\online-dekanat.exe serve --host 127.0.0.1 --port 8000
```

Откройте в браузере:

- Интерфейс: <http://127.0.0.1:8000/ui>
- Проверка сервера: <http://127.0.0.1:8000/health>
- Интерактивная документация FastAPI: <http://127.0.0.1:8000/docs>, если запущен backend `fastapi`.

Чтобы остановить сервер, нажмите `Ctrl+C` в терминале, где он запущен.

Если порт `8000` занят, укажите другой порт, например:

```powershell
.\.venv\Scripts\online-dekanat.exe serve --host 127.0.0.1 --port 8001
```

### Установка вручную в Windows

Если автоматический установщик использовать нельзя, из корневой папки выполните:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,dataset,ocr]"
```

Tesseract — отдельная системная программа, её нельзя установить только пакетом `pytesseract`. Например, в Windows её можно установить через `winget`:

```powershell
winget install --id UB-Mannheim.TesseractOCR -e
```

Убедитесь, что Tesseract и языковые данные `rus` и `eng` установлены. При установке языковых файлов вручную задайте `TESSDATA_PREFIX` на каталог `tessdata` и добавьте каталог с `tesseract.exe` в `PATH`.

## Другие способы запуска

### Командная строка (CLI)

Тестовый пример читает текст непосредственно из `samples/study_certificate.txt`; внешняя OCR-модель для этого не требуется:

```powershell
.\.venv\Scripts\online-dekanat.exe process `
  --file samples/study_certificate.txt `
  --question "Хватит ли справки для перевода на 2 курс?" `
  --ocr text
```

Проверка изображения или PDF с автоматическим выбором OCR:

```powershell
.\.venv\Scripts\online-dekanat.exe process `
  --file "C:\path\to\document.jpg" `
  --question "Подходит ли документ?" `
  --ocr auto
```

Принудительный выбор типа документа и OCR:

```powershell
.\.venv\Scripts\online-dekanat.exe process `
  --file "C:\path\to\application.pdf" `
  --question "Можно оформить академический отпуск?" `
  --doc-type academic_leave_application `
  --ocr tesseract
```

Доступные значения `--ocr`: `auto`, `paddleocr`, `easyocr`, `tesseract`, `text`. Параметр `--doc-type` необязателен. Для `--ocr text` передайте `.txt`-файл с распознанным текстом либо файл, рядом с которым есть файл с таким же именем и расширением `.txt`.

Результат выводится в JSON. Его можно одновременно сохранить в файл:

```powershell
.\.venv\Scripts\online-dekanat.exe process `
  --file samples/study_certificate.txt `
  --question "Хватит ли справки для перевода на 2 курс?" `
  --ocr text `
  --out output/result.json
```

### HTTP API

`POST /process` принимает `multipart/form-data`:

| Поле | Обязательное | Описание |
| --- | --- | --- |
| `file` | Да | Файл документа |
| `question` | Да | Вопрос на русском языке |
| `doc_type` | Нет | Идентификатор типа документа; по умолчанию определяется автоматически |
| `ocr` | Нет | `auto`, `paddleocr`, `easyocr`, `tesseract` или `text`; по умолчанию `auto` |

Пример запроса из PowerShell с установленным `curl.exe`:

```powershell
curl.exe -X POST http://127.0.0.1:8000/process `
  -F "file=@samples/study_certificate.txt" `
  -F "question=Хватит ли справки для перевода на 2 курс?" `
  -F "ocr=text"
```

При успешной обработке ответ содержит объект `card` с типом документа, извлечёнными полями и распознанным текстом, а также объект `answer` с текстом ответа, источниками, пропущенными полями и оценкой уверенности. При ошибке API возвращает JSON с полем `error` и HTTP-статус `400`.

Проверка состояния:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Ожидаемый ответ:

```json
{"status":"ok"}
```

### Запуск через Python-модуль

Вместо установленной CLI-команды можно использовать:

```powershell
.\.venv\Scripts\python.exe -m online_dekanat.cli serve --host 127.0.0.1 --port 8000
```

## OCR и зависимости

Базовые зависимости, необходимые для приложения, устанавливаются вместе с проектом. Дополнительные группы заданы в `pyproject.toml`:

| Набор | Устанавливается как | Для чего нужен |
| --- | --- | --- |
| Основные библиотеки | `pip install -e .` | Веб-приложение, API, PDF, изображения и шифрование |
| Все OCR-движки | `pip install -e ".[ocr]"` | PaddleOCR, EasyOCR и Python-адаптер Tesseract |
| Средства разработки | `pip install -e ".[dev]"` | Тесты проекта |
| Работа с датасетом | `pip install -e ".[dataset]"` | Загрузчик MWS-Vision-Bench |

Установить полный набор, используемый `setup.bat`, можно командой:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,dataset,ocr]"
```

Режим `auto` пробует движки в следующем порядке: PaddleOCR → EasyOCR → Tesseract. Если все движки завершились ошибкой, API/CLI возвращают сообщение об ошибке; текстовый режим автоматически не подставляется.

Для Tesseract нужны и Python-пакет `pytesseract`, и системный исполняемый файл `tesseract`. Для распознавания русского текста также нужны языковые данные `rus`; скрипт установки Windows размещает `eng` и `rus` в пользовательском каталоге и задаёт `TESSDATA_PREFIX`.

Модели OCR скачиваются самими движками при первом распознавании. Поэтому первый запуск для изображения может занять заметное время. Режим `text` можно использовать для быстрой локальной проверки конвейера без загрузки моделей.

## Данные и настройки

- Типы документов и шаблоны извлечения полей: `config/document_types.json`.
- Правила проверки и формулировки ответов: `config/regulations.json`.
- Демонстрационный текстовый документ: `samples/study_certificate.txt`.
- API сохраняет исходный загруженный документ в зашифрованном виде в `data/secure/`; расшифрованная копия используется только временно при обработке.
- Ключ Fernet создаётся в `data/secure/.storage.key` при первом запуске и не должен публиковаться или передаваться вместе с зашифрованными файлами. Для переноса зашифрованных данных сохраните исходный ключ либо задайте его через `ONLINE_DEKANAT_STORAGE_KEY`.

Каталог `data/` и виртуальное окружение `.venv/` исключены из Git. Не добавляйте ключ хранилища, загруженные документы, токены или другие секреты в репозиторий.

## MWS-Vision-Bench

Опциональная команда `mws-sample` загружает примеры из `MTSAIR/MWS-Vision-Bench` через Hugging Face Datasets и экспортирует выбранные записи в JSONL. Это внешний набор данных для экспериментов, а не источник правил деканата.

Например, экспортировать до 20 примеров задачи OCR:

```powershell
.\.venv\Scripts\online-dekanat.exe mws-sample `
  --limit 20 `
  --task-type "full-page OCR ru" `
  --out data/mws_sample.jsonl
```

Для этой команды нужна группа зависимостей `dataset`. При первом запуске потребуется доступ к Hugging Face и загрузка выбранной части набора.

## Разработка и тесты

Активируйте окружение:

```powershell
.\.venv\Scripts\Activate.ps1
```

Если PowerShell блокирует запуск `Activate.ps1`, используйте Python и CLI напрямую через `.venv\Scripts\python.exe` и `.venv\Scripts\online-dekanat.exe`, как в примерах выше.

Запуск тестов:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Для запуска FastAPI напрямую через Uvicorn:

```powershell
.\.venv\Scripts\python.exe -m uvicorn online_dekanat.api:app --host 127.0.0.1 --port 8000
```

Если на Windows возникают проблемы с backend Uvicorn, запустите сервер через CLI с `--backend simple`:

```powershell
.\.venv\Scripts\online-dekanat.exe serve --host 127.0.0.1 --port 8000 --backend simple
```

Упрощённый сервер поддерживает веб-форму, `/health` и `/process`; документация OpenAPI `/docs` для него не предоставляется.
