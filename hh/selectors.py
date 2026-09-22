"""Селекторы UI hh.ru."""

from __future__ import annotations

# Список поиска
SEARCH_VACANCY_CARDS = "[data-qa='vacancy-serp__vacancy']"
SEARCH_VACANCY_TITLE = "[data-qa='serp-item__title']"
SEARCH_VACANCY_TITLE_TEXT = "[data-qa='serp-item__title-text']"
SEARCH_VACANCY_COMPANY = "[data-qa='vacancy-serp__vacancy-employer-text']"
SEARCH_VACANCY_COMPANY_FALLBACK = "[data-qa='vacancy-serp__vacancy-employer']"
SEARCH_VACANCY_SALARY = "[data-qa='vacancy-serp__compensation']"
SEARCH_VACANCY_ADDRESS = "[data-qa='vacancy-serp__vacancy-address']"
SEARCH_VACANCY_SNIPPET = "[data-qa='vacancy-serp__vacancy_snippet']"
SEARCH_HIDE_BUTTON = "[data-qa='vacancy__blacklist-show-add']"
# На выдаче «Откликнуться» — ссылка
SEARCH_RESPONSE_BUTTON = "[data-qa='vacancy-serp__vacancy_response']"
# Пагинация поиска
SEARCH_PAGER_NEXT = "a[data-qa='pager-next']"
SEARCH_PAGER_BLOCK = "[data-qa='pager-block']"

# Страница вакансии — карточка компании
VACANCY_COMPANY_NAME_LINK = "[data-qa='vacancy-company-name']"
VACANCY_COMPANY_BLOCK = "[data-qa='vacancy-company']"

# Страница работодателя /employer/{id}
COMPANY_NAME = (
    "[data-qa='company-header-title'], "
    "[data-qa='employer-page-title'], "
    "h1[data-qa='bloko-header-1'], "
    "h1.bloko-header-1, "
    "h1"
)
COMPANY_DESCRIPTION = (
    "[data-qa='company-description-text'], "
    "[data-qa='employer-description'], "
    "[data-qa='employer-description-wrapper'], "
    ".employer-description, "
    ".g-user-content, "
    "[data-qa='vacancy-company-description']"
)
COMPANY_INDUSTRIES = (
    "[data-qa='company-industries'], "
    "[data-qa='employer-industries'], "
    "[data-qa='company-sidebar-industries']"
)
COMPANY_SITE = (
    "a[data-qa='sidebar-company-site'], "
    "a[data-qa='employer-sidebar-site'], "
    "a[data-qa='company-url']"
)

# Страница вакансии
VACANCY_TITLE = "[data-qa='vacancy-title']"
VACANCY_DESCRIPTION = "[data-qa='vacancy-description']"
VACANCY_RESPONSE_BUTTON = "[data-qa='vacancy-response-link-top']"
# запасные кнопки отклика на странице вакансии
VACANCY_RESPONSE_BUTTON_ALT = "[data-qa='vacancy-response-link-bottom']"
# Уже откликались: «Чат» / «Отказ» / «Собеседование» и т.п.
VACANCY_RESPONSE_VIEW_TOPIC = "[data-qa='vacancy-response-link-view-topic']"
VACANCY_RESPONSE_BY_TEXT_XPATH = (
    "//a[.//span[contains(normalize-space(.),'Откликнуться')]]"
    " | //button[.//span[contains(normalize-space(.),'Откликнуться')]]"
    " | //a[contains(normalize-space(.),'Откликнуться')]"
    " | //button[contains(normalize-space(.),'Откликнуться')]"
)
# Тексты кнопки, если отклик уже был
VACANCY_ALREADY_RESPONDED_TEXTS = (
    "чат",
    "отказ",
    "собеседование",
    "приглашение",
    "переписка",
    "откликнут",
    "вы откликнулись",
)

# После отклика: блок «Резюме доставлено» на странице вакансии
RESPONSE_SUCCESS_ATTACH_LETTER = "[data-qa='responded-success-attach-cover-letter']"
RESPONSE_SUCCESS_ATTACH_LETTER_TEXT = "[data-qa='responded-success-attach-cover-letter-text']"
# Старый попап-информер (на всякий случай)
RESPONSE_LETTER_INFORMER = "[data-qa='vacancy-response-letter-informer']"
RESPONSE_LETTER_TOGGLE = "[data-qa='vacancy-response-letter-toggle']"
RESPONSE_LETTER_TOGGLE_TEXT = "[data-qa='vacancy-response-letter-toggle-text']"
RESPONSE_LETTER_TEXTAREA = "[data-qa='vacancy-response-popup-form-letter-input']"
# Два варианта UI submit
RESPONSE_LETTER_SUBMIT = "[data-qa='vacancy-response-letter-submit']"
RESPONSE_SUBMIT_POPUP = "[data-qa='vacancy-response-submit-popup']"
RESPONSE_POPUP_CLOSE = "[data-qa='response-popup-close']"
RESPONSE_PRIMARY_ACTIONS = "[data-qa='primary-actions'] button"
# Не трогаем вопросы про зарплату в модалке отклика
RESPONSE_SALARY_QUESTION = "[data-qa*='vacancy-response-question_salary']"

# Попап «вакансия в другой стране» — всегда подтверждаем
FOREIGN_COUNTRY_POPUP_TITLE = "Вы откликаетесь на вакансию в другой стране"
FOREIGN_COUNTRY_FORCE_TEXTS = (
    "Все равно откликнуться",
    "Всё равно откликнуться",
)
FOREIGN_COUNTRY_FORCE_XPATH = (
    "//button[.//span[contains(normalize-space(.),'Все равно откликнуться')]]"
    " | //button[contains(normalize-space(.),'Все равно откликнуться')]"
    " | //a[.//span[contains(normalize-space(.),'Все равно откликнуться')]]"
    " | //a[contains(normalize-space(.),'Все равно откликнуться')]"
    " | //button[.//span[contains(normalize-space(.),'Всё равно откликнуться')]]"
    " | //button[contains(normalize-space(.),'Всё равно откликнуться')]"
)

# Признаки сценария с тестом (пока только детект + stub)
RESPONSE_TEST_MARKERS = (
    "[data-qa='task-body']",
    "[data-qa='vacancy-response-test']",
    "[data-qa='applicant-questions']",
    "form[action*='vacancy_response'][action*='test']",
)

# Капча hh.ru (картинка + поле «Текст с картинки») — tests_example/capcha.html
CAPTCHA_PICTURE = "[data-qa='account-captcha-picture']"
CAPTCHA_INPUT = "[data-qa='account-captcha-input']"
CAPTCHA_INPUT_NAME = "input[name='captchaText']"
CAPTCHA_IMG_SRC = "img[src*='/captcha/picture']"
CAPTCHA_RENEW = "[data-qa='captcha-renew-text']"
CAPTCHA_ERROR = "[data-qa='account-captcha-error']"
CAPTCHA_MARKERS = (
    CAPTCHA_PICTURE,
    CAPTCHA_INPUT,
    CAPTCHA_INPUT_NAME,
    CAPTCHA_IMG_SRC,
)

# Опросник работодателя при отклике
TEST_ASKING = "[data-qa='employer-asking-for-test']"
TEST_DESCRIPTION = "[data-qa='test-description']"
TEST_TASK_BODY = "[data-qa='task-body']"
TEST_QUESTION = "[data-qa='task-question']"
TEST_OPTION_CELL = "label[data-qa='cell']"
TEST_OPTION_TEXT = "[data-qa='cell-text-content'], [data-qa='cell-text']"
TEST_RADIO = "[data-qa='radio']"
TEST_CHECKBOX = "[data-qa='checkbox']"
TEST_TEXTAREA = "textarea"
TEST_CUSTOM_OPTION_TEXTS = ("свой вариант", "свой ответ", "другое")

# Отклик уже просмотрен → письмо только через Chatik
LETTER_VIEWED_WARNING_TEXT = "Отклик уже просмотрен работодателем"
# Чат рядом с «Приложить сопроводительное» в блоке «Резюме доставлено»
RESPONSE_SUCCESS_OPEN_CHAT = "[data-qa='vacancy-response-link-view-topic']"
CHATIK_IFRAME = (
    "iframe.chatik-integration-iframe, "
    "iframe[src*='chatik.hh.ru']"
)
CHATIK_WIDGET = ".chatik-integration_visible, .chatik-integration-iframe-container"
CHATIK_CLOSE = "[data-qa='chatik-close-chatik']"
# Внутри iframe chatik.hh.ru
CHATIK_ADD_LETTER = "[data-qa='chatik-chat-message-applicant-action']"
CHATIK_ADD_LETTER_TEXT = "[data-qa='chatik-chat-message-applicant-action-text']"
CHATIK_MESSAGE_INPUT = "textarea[data-qa='text-input']"
CHATIK_SEND_BUTTONS = (
    "[data-qa='chat-input-send']",
    "[data-qa='chat-input-send-button']",
    "[data-qa='send-button']",
    "button[aria-label='Отправить']",
    "button[aria-label='Отправить сообщение']",
)
CHATIK_LETTER_PREVIEW = "[data-qa='chat-input-preview']"

# --- Полноэкранный чат https://hh.ru/chat (модуль chat/) ---
# Ориентир DOM: tests_example/chat.html (список), chat_thread.html (тред).
CHAT_PAGE_URL = "https://hh.ru/chat"
CHATIK_LAYOUT = "[data-qa='chatik-layout']"
CHATIK_ONLY_UNREAD = "[data-qa='chatik-checkbox-only-unread']"
CHATIK_NO_CHATS = "[data-qa='chatik-no-chats']"
CHATIK_OPEN_CHAT_PREFIX = "chatik-open-chat-"
CHATIK_OPEN_CHAT = "[data-qa^='chatik-open-chat-']"
CHATIK_SELECT_CHAT = "[data-qa^='chatik-select-chat-']"
CHAT_CELL_TITLE = "[data-qa='chat-cell-title']"
CHAT_CELL_SUBTITLE = "[data-qa='chat-cell-subtitle']"
CHAT_CELL_META = "[data-qa='chat-cell-meta']"
CHAT_CELL_TIME = "[data-qa='chat-cell-creation-time']"
# превью/статус под вакансией: «Отказ», текст сообщения, «Отклик на вакансию»
CHAT_CELL_LAST_MESSAGE = "[class*='last-message-color_']"

# Открытый тред
CHAT_MESSAGES_SCROLLER = "#chatik_messages_scroller"
# Корень сообщения: data-qa="chatik-chat-message-<id>" (не *-text)
CHAT_MESSAGE_ROOT_PREFIX = "chatik-chat-message-"
CHAT_MESSAGE_ROOT = "[data-qa^='chatik-chat-message-']"
CHAT_BUBBLE_TEXT = "[data-qa='chat-bubble-text']"
CHAT_BUBBLE_AUTHOR = "[data-qa='chat-bubble-author-name']"
CHAT_BUBBLE_TIME = "[data-qa='chat-buble-display-time']"  # опечатка hh в DOM
# Хеш в class меняется — матчим по стабильному куску имени
CHAT_BUBBLE_OUTGOING = "[class*='chat-bubble_outgoing'], [class*='message_my']"
CHAT_BUBBLE_INCOMING = "[class*='chat-bubble_incoming']"
CHAT_UNREAD_PLATE = "[data-qa='unread-messages-plate']"
CHAT_PARTICIPANT_TITLE = "[data-qa='participant-info-title']"
CHAT_PARTICIPANT_SUBTITLE = "[data-qa='participant-info-subtitle']"
CHAT_PARTICIPANT_DETAILS = "[data-qa='participant-info-details']"  # a → /employer/<id>
CHAT_COMPOSER = "[data-qa='chatik-message-input']"
CHAT_THREAD_TEXTAREA = (
    "[data-qa='chatik-message-input'] textarea[data-qa='text-input']"
)
CHAT_SCROLL_DOWN = "[data-qa='chatik-chat-scroll-down-button']"
# кнопка отправки в полноэкранном /chat (те же data-qa, что в iframe chatik)
CHAT_SEND_BUTTONS = CHATIK_SEND_BUTTONS
