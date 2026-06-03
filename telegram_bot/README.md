# Junior Scout AI Telegram Bot

Отдельный runnable-бот для текущего агентного pipeline. Он не требует web-сервер:
сообщение из Telegram сразу передается в `VacancyAgentPipeline`.

## Запуск

Токен читается из переменной окружения `TELEGRAM_BOT_TOKEN` или из `.env`.

```bash
python3 telegram_bot/bot.py
```

Альтернативный env-файл можно передать так:

```bash
python3 telegram_bot/bot.py --env-file /path/to/.env
```

## Проверка без Telegram

```bash
python3 telegram_bot/bot.py --dry-run "Ищу junior позицию продуктового аналитика в Москве без опыта" --top 5
```

## Команды

- `/start` - краткая инструкция.
- `/help` - подсказка по формату запроса.
- `/top 10 <запрос>` - вернуть от 1 до 25 вакансий.
- Любой обычный текст - поиск топ-5 вакансий.

Бот использует тот же payload-адаптер, что и frontend API, поэтому формат ответа
остается единым для сайта и Telegram.
