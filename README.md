# ⭐️ Stars & Gifts Commerce Bot

Production-oriented Telegram commerce bot foundation for manual admin fulfillment.

## Core flow

User subscribes to mandatory channels → chooses Stars/Gift → creates order → balance is charged → admin receives order → admin manually fulfills → admin marks completed → user receives HTML notification + PNG electronic receipt → public feed/audit log are updated.

## Stack

- Python 3.11+
- aiogram 3.22
- PostgreSQL + SQLAlchemy async
- Redis
- FastAPI health service
- Alembic
- Pillow receipt generator
- Optional pytesseract OCR
- Render web + background worker

## Render

Build Command:

```bash
pip install --upgrade pip && pip install -r requirements.txt && alembic upgrade head
```

Web Start Command:

```bash
python -m app.web
```

Worker Start Command:

```bash
python -m app.worker
```

## Important

Telegram inline keyboard buttons do not expose arbitrary CSS background colors. The UI therefore uses HTML-formatted messages, semantic emojis, media/animation hooks and clear action labels. Optional animation file IDs can be supplied via the `ANIMATION_*_FILE_ID` environment variables.

Admin direct messaging is available via `📨 Userga ID orqali habar` or from an order/payment.
