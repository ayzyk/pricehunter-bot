"""
Разовая диагностика: проверяет, какие сайты вообще доступны с сервера GitHub Actions,
прежде чем строить сметный бот вокруг конкретного источника цен.

Идея: мы уже дважды напоролись на то, что российские сайты (Wildberries, zakupki.gov.ru)
блокируют/не пускают трафик с серверов GitHub Actions (не-российские дата-центры).
Этот скрипт быстро проверяет сразу несколько кандидатов — включая контрольные точки
(работает ли вообще исходящий интернет, работают ли обычные не-гос российские сайты) —
чтобы не тратить время на постройку бота вокруг источника, который потом окажется
недоступен.

Ничего не парсит и никуда не отправляет результат, кроме лога GitHub Actions —
запускается вручную через Actions -> Run workflow.
"""

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

# (название, url, зачем проверяем)
TARGETS = [
    ("example.com", "https://example.com", "контроль: работает ли вообще исходящий HTTPS"),
    ("google.com", "https://www.google.com", "контроль: работает ли обычный интернет"),
    ("yandex.ru", "https://yandex.ru", "контроль: пускают ли российские сайты в принципе (не гос)"),
    ("zakupki.gov.ru", "https://zakupki.gov.ru/epz/main/public/home.html", "уже знаем что блокирует — проверка согласованности"),
    ("wildberries.ru", "https://www.wildberries.ru", "уже знаем что блокирует — проверка согласованности"),
    ("fedstat.ru (Росстат/ЕМИСС)", "https://www.fedstat.ru/opendata", "кандидат: официальные цены на любые товары/услуги"),
    ("rosstat.gov.ru", "https://rosstat.gov.ru/statistics/price", "кандидат: официальная статистика цен"),
    ("fgiscs.minstroyrf.ru (ФГИС ЦС)", "https://fgiscs.minstroyrf.ru/", "кандидат: официальные сметные нормативы (стройка)"),
    ("gge.ru (Главгосэкспертиза)", "https://www.gge.ru/", "кандидат: официальные сборники цен ФЕР/ГЭСН"),
]


def check(name, url, purpose):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        print(f"[OK?] {name}: status={resp.status_code} len={len(resp.content)}  ({purpose})")
    except requests.exceptions.RequestException as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {e}  ({purpose})")


def main():
    print("=== Проверка доступности источников с сервера GitHub Actions ===\n")
    for name, url, purpose in TARGETS:
        check(name, url, purpose)
    print("\n=== Готово. Смотри выше, что дало status=200, а что FAIL/таймаут ===")


if __name__ == "__main__":
    main()
