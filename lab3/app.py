"""Точка входа приложения ЛР3 «Аренда электросамоката».

Запуск под gunicorn: gunicorn -b 0.0.0.0:8000 -w 1 app:app
Запуск для отладки:  python3 app.py
"""

import os

from web.wsgi import create_app

app = create_app()

if __name__ == "__main__":  # pragma: no cover - ручной запуск
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
