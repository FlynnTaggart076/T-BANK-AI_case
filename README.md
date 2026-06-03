# Vacancy Matching Agent

MVP агентного pipeline для поиска стажировок и junior-вакансий. Пользователь пишет запрос в свободной форме, агент извлекает критерии, ищет вакансии через открытые данные Работа России / Trudvsem, валидирует и ранжирует результаты, а затем делает Markdown-отчет с объяснением топ-5.

## Stack

- Python 3.11+
- Trudvsem open data API как основной источник
- Локальный `data/sample_vacancies.json` как fallback для демо
- OpenAI LLM через HTTP без обязательных Python-зависимостей
- CLI, Web UI и Telegram-бот используют один агентный pipeline

## Run CLI

```bash
cp .env.example .env
python run.py "Ищу стажировку или junior позицию Python backend, можно удаленно или Москва, FastAPI/PostgreSQL, без опыта или до года."
```

Если в `.env` или переменных окружения задан `OPENAI_API_KEY`, агент использует LLM для извлечения критериев и объяснений. Если ключа нет или модель недоступна, включается эвристический fallback.

Пример `.env`:

```dotenv
OPENAI_API_KEY=<your_openai_key>
OPENAI_MODEL=gpt-4o-mini
SUPERJOB_API_KEY=<your_superjob_key>
TELEGRAM_BOT_TOKEN=<your_telegram_token>
```

Результаты:

- `output/report.md` - итоговый отчет;
- `output/run.log` - trace шагов pipeline.

## Run Web UI

```bash
python3 server.py
```

Открой:

```text
http://127.0.0.1:8000
```

Сервер одновременно раздает статический frontend из `frontend/` и принимает API-запрос:

```http
POST /api/search
Content-Type: application/json

{
  "query": "Ищу junior позицию на продуктового аналитика, работа в Москве, без опыта работы",
  "top_n": 5,
  "source": "trudvsem"
}
```

Поле `source` выбирает инструментарий поиска:

- `trudvsem` - Работа России / Trudvsem;
- `superjob` - SuperJob API, секрет читается из `SUPERJOB_API_KEY`, `SUPERJOB_SECRET`
  или `SUPERJOB_TOKEN` из окружения или `.env`;
- `all` - объединить Работа России и SuperJob перед дедупликацией и scoring.

Ответ совместим с `frontend/app.js`:

```json
{
  "criteria": {
    "role": "продуктовый аналитик",
    "level": "junior",
    "skills": ["sql", "python"],
    "city": "Москва",
    "remote": "нет, нужен город",
    "salary": "не указана"
  },
  "vacancies": [],
  "trace": [],
  "top_n": 5,
  "source": "trudvsem",
  "source_label": "Работа России"
}
```

В frontend `top_n` задается слайдером в правой карточке, а `source` выбирается в форме поиска.
Допустимый диапазон `top_n` на backend: от `1` до `25`.

## Run Telegram Bot

Токен читается из переменной окружения `TELEGRAM_BOT_TOKEN` или из `.env`.

```bash
python3 telegram_bot/bot.py
```

При необходимости можно указать альтернативный env-файл:

```bash
python3 telegram_bot/bot.py --env-file /path/to/.env
```

Проверить формат ответа без подключения к Telegram:

```bash
python3 telegram_bot/bot.py --dry-run "Ищу junior позицию продуктового аналитика в Москве без опыта" --top 5
```

Команды бота:

- `/start` - краткая инструкция;
- `/help` - подсказка по формату запроса;
- `/top 10 <запрос>` - вернуть от 1 до 25 вакансий;
- обычный текст - поиск топ-5 вакансий.

## Pipeline

1. `extract_criteria` - LLM или эвристика превращает свободный запрос в структуру критериев.
2. `TrudvsemSource` - ходит в `https://opendata.trudvsem.ru/api/v1/vacancies`.
3. `LocalFileSource` - добавляет локальные вакансии, если API вернул мало результатов.
4. `validate_and_dedupe` - удаляет битые строки и дубли.
5. `score_vacancies` - обычная логика считает score по роли, стеку, формату, уровню, свежести и стоп-словам.
6. `explain_top` - LLM или fallback объясняет топ-5: совпадения, риски, следующий шаг.
7. `render_report` - создает Markdown-отчет.

Отдельный слой `experience_reality_check` проверяет фактический текст карточки и структурные поля Trudvsem:
`qualification`, `requirement.experience`, `requirements`, `duty`. Если пользователь ищет junior/стажировку/без опыта, вакансии с маркерами `Сеньор`, `Middle`, `Lead`, `Старший`, `от 3 лет`, `2+ лет`, `Опыт работы: 3 лет` жестко уходят в `filtered`, даже если источник вернул их по широкому запросу.

## Integration Points

Основной вход отделен от CLI:

```python
from job_agent import run_agent

result = run_agent(user_query)
```

Для web и Telegram общий JSON-формат собирается в `job_agent.payload`.

## Limitations

- Trudvsem дает меньше серверных фильтров, чем HH/SuperJob, поэтому часть фильтрации делается локально.
- У вакансий часто не хватает структурированного опыта или навыков.
- LLM не является источником истины: финальный score считается обычным кодом, а LLM только извлекает намерение и объясняет результат.
- Для стабильной защиты есть локальный fallback-датасет.
