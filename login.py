"""Шаг 1: открыть страницу авторизации hh.ru и дождаться ручного входа.

Запуск из каталога hh_autoapply/:

    python login.py
"""

from __future__ import annotations

import sys

from browser import create_driver
from config import HH_LOGIN_URL, load_env


def main() -> int:
    load_env()
    driver = create_driver()
    try:
        print(f"Открываю: {HH_LOGIN_URL}")
        driver.get(HH_LOGIN_URL)
        print(
            "Войди в аккаунт в открывшемся окне Chrome.\n"
            "Когда закончишь — вернись сюда и нажми Enter "
            "(браузер закроется, профиль сохранится)."
        )
        try:
            input()
        except EOFError:
            # нет TTY — просто ждём, пока пользователь закроет процесс
            print("Нет интерактивного ввода — закрой процесс вручную (Ctrl+C).")
            import time

            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        driver.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
