from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Вакансии и чаты"

    def ready(self) -> None:
        # Таблицы chat_* создаёт ChatStore (тот же Postgres, что vacancies).
        try:
            from chat.store import ChatStore
            from config import load_env, pg_conninfo

            load_env()
            with ChatStore(pg_conninfo()):
                pass
        except Exception:
            # Админка поднимется и без чатов, если Postgres ещё не доступен
            pass
