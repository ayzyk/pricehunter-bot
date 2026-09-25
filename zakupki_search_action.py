import os
import re
import time
import random
import json
import requests
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup

BASE_URL = "https://zakupki.gov.ru"
SEARCH_PATH = "/epz/order/extendedsearch/results.html"


PLATFORM_NAME = "ЕИС (zakupki.gov.ru)"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

MAX_PAGES = 3          # по 50 записей на страницу -> до 150 тендеров за проверку
RECORDS_PER_PAGE = 50
REQUEST_DELAY_RANGE = (0.6, 1.1)   # троттлинг: не больше ~1-1.5 запроса/сек
MAX_RETRIES = 3


def _zakupki_headers():
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Referer": "https://zakupki.gov.ru/epz/main/public/home.html",
    }


def _parse_price(text):
    """'125 000,00 руб.' -> 125000.0 ; None если не смогли распарсить."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,\.]", "", text).replace(",", ".")
    cleaned = cleaned.rstrip(".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(text):
    """'23.09.2026' или '23.09.2026 18:00' -> datetime ; None если не смогли."""
    if not text:
        return None
    text = text.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def search_zakupki(query, page, low_price, top_price, retry_count=0):
    """
    Запрашивает одну страницу результатов поиска на zakupki.gov.ru.
    Возвращает список словарей-тендеров или None при неустранимой ошибке
    (сеть/блокировка) — в этом случае main() честно сообщит об этом в Telegram,
    а не промолчит.
    """
    params = {
        "searchString": query,
        "morphology": "on",           # поиск по словоформам делает сам портал
        "pageNumber": page,
        "sortDirection": "false",
        "sortBy": "UPDATE_DATE",
        "recordsPerPage": f"_{RECORDS_PER_PAGE}",
        "showLotsInfoHidden": "false",
        "fz44": "on",
        "fz223": "on",
        "af": "on",
        "ca": "on",
        "pc": "on",
        "pa": "on",
        "currencyIdGeneral": "-1",
    }
    if low_price:
        params["priceFromGeneral"] = low_price
    if top_price:
        params["priceToGeneral"] = top_price

    url = f"{BASE_URL}{SEARCH_PATH}"

    try:
        resp = requests.get(
            url, params=params, headers=_zakupki_headers(), timeout=20
        )
    except requests.exceptions.RequestException as e:
        print(f"[DEBUG zakupki] network error page={page}: {e}")
        if retry_count < MAX_RETRIES:
            time.sleep(2 * (retry_count + 1))
            return search_zakupki(query, page, low_price, top_price, retry_count + 1)
        return None

    print(f"[DEBUG zakupki] status={resp.status_code} page={page} len={len(resp.content)}")

    if resp.status_code == 429 or resp.status_code >= 500:
        if retry_count < MAX_RETRIES:
            time.sleep(3 * (retry_count + 1))
            return search_zakupki(query, page, low_price, top_price, retry_count + 1)
        return None

    if resp.status_code != 200:
        # честно возвращаем None, а не тихо пустой список — main() должен об этом сказать
        return None

    html = resp.text

    # пустой ответ кэширующего прокси (Varnish) — иногда бывает, по опыту тех, кто уже
    # парсит этот портал; повторяем запрос
    if len(html.strip()) < 500:
        if retry_count < MAX_RETRIES:
            time.sleep(1.5)
            return search_zakupki(query, page, low_price, top_price, retry_count + 1)
        return None

    if "captcha" in html.lower() or "g-recaptcha" in html.lower():
        print("[DEBUG zakupki] похоже на капчу/антибот-защиту")
        return None

    soup = BeautifulSoup(html, "lxml")
    blocks = soup.select("div.search-registry-entry-block")

    tenders = []
    for block in blocks:
        try:
            number_link = block.select_one("div.registry-entry__header-mid a")
            number = number_link.get_text(strip=True) if number_link else None
            href = number_link["href"] if number_link and number_link.has_attr("href") else None
            link = href if (href and href.startswith("http")) else (BASE_URL + href if href else None)

            title_el = block.select_one("div.registry-entry__body-value")
            title = title_el.get_text(strip=True) if title_el else "Без названия"

            customer_el = block.select_one("div.registry-entry__body-href")
            customer = customer_el.get_text(strip=True) if customer_el else None

            price_el = block.select_one("div.price-block__value")
            price = _parse_price(price_el.get_text(strip=True)) if price_el else None

            deadline = None
            for data_block in block.select("div.data-block"):
                title_span = data_block.select_one("span.data-block__title")
                value_span = data_block.select_one("div.data-block__value")
                if title_span and value_span and "Окончание подачи заявок" in title_span.get_text():
                    deadline = _parse_date(value_span.get_text(strip=True))
                    break

            if not number and not title:
                continue

            tenders.append(
                {
                    "number": number,
                    "title": title,
                    "platform": PLATFORM_NAME,
                    "customer": customer,
                    "price": price,
                    "deadline": deadline,
                    "link": link,
                }
            )
        except Exception as e:
            print(f"[DEBUG zakupki] ошибка разбора одной карточки: {e}")
            continue

    return tenders


def format_tender_message(t, idx):
    price_str = f"{t['price']:,.0f} ₽".replace(",", " ") if t.get("price") else "цена не указана"
    deadline_str = t["deadline"].strftime("%d.%m.%Y") if t.get("deadline") else "не указан"
    lines = [
        f"{idx}. {t['title']}",
        f"   № {t.get('number') or '—'}",
        f"   Платформа: {t.get('platform') or '—'}",
        f"   Заказчик: {t.get('customer') or '—'}",
        f"   Цена: {price_str}",
        f"   Окончание подачи заявок: {deadline_str}",
    ]
    if t.get("link"):
        lines.append(f"   {t['link']}")
    return "\n".join(lines)


def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[DEBUG] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID не заданы — сообщение не отправлено")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Telegram режет сообщение по 4096 символов — бьём на части, если нужно
    for i in range(0, len(text), 4000):
        chunk = text[i:i + 4000]
        try:
            requests.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "disable_web_page_preview": True},
                timeout=15,
            )
        except requests.exceptions.RequestException as e:
            print(f"[DEBUG] ошибка отправки в Telegram: {e}")


def send_telegram_document(file_path, caption=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        with open(file_path, "rb") as f:
            requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files={"document": f},
                timeout=60,
            )
    except requests.exceptions.RequestException as e:
        print(f"[DEBUG] ошибка отправки файла в Telegram: {e}")


def main():
    query = os.environ.get("QUERY", "").strip()
    low_price = os.environ.get("LOW_PRICE", "").strip()
    top_price = os.environ.get("TOP_PRICE", "").strip()
    customer_filter = os.environ.get("CUSTOMER", "").strip()

    if not query:
        send_telegram_message("Не указан поисковый запрос (QUERY).")
        return

    print(f"[DEBUG] запрос='{query}' цена=[{low_price};{top_price}] заказчик='{customer_filter}'")

    all_tenders = []
    blocked_or_failed = False

    for page in range(1, MAX_PAGES + 1):
        page_tenders = search_zakupki(query, page, low_price, top_price)
        if page_tenders is None:
            blocked_or_failed = True
            break
        if not page_tenders:
            break
        all_tenders.extend(page_tenders)
        if page < MAX_PAGES:
            time.sleep(random.uniform(*REQUEST_DELAY_RANGE))

    if blocked_or_failed and not all_tenders:
        send_telegram_message(
            "Не удалось получить данные с портала госзакупок (zakupki.gov.ru): "
            "сайт ответил ошибкой, недоступен или похоже показал защиту от ботов. "
            "Это не значит, что тендеров нет — попробуйте ещё раз чуть позже."
        )
        return

    # фильтр по заказчику (например CUSTOMER=Газпром или CUSTOMER=Сбербанк) —
    # применяется поверх обычного поиска по ключевому слову, см. пояснение в шапке файла
    if customer_filter:
        before = len(all_tenders)
        all_tenders = [
            t for t in all_tenders
            if t.get("customer") and customer_filter.lower() in t["customer"].lower()
        ]
        print(f"[DEBUG] фильтр по заказчику '{customer_filter}': {before} -> {len(all_tenders)}")

    # убираем просроченные заявки (окончание подачи заявок уже прошло)
    now = datetime.now()
    active_tenders = [
        t for t in all_tenders
        if t.get("deadline") is None or t["deadline"] >= now
    ]

    if not active_tenders:
        customer_note = f", заказчик содержит «{customer_filter}»" if customer_filter else ""
        send_telegram_message(
            f"По запросу «{query}»{customer_note} действующих тендеров не найдено "
            f"(диапазон цены: {low_price or 'любая'}–{top_price or 'любая'} ₽)."
        )
        return

    # ближайший дедлайн — первым (без даты — в конец списка)
    active_tenders.sort(key=lambda t: t["deadline"] or datetime.max)

    top_n = active_tenders[:10]
    customer_note = f", заказчик содержит «{customer_filter}»" if customer_filter else ""
    header = (
        f"Найдено тендеров по запросу «{query}»{customer_note}: {len(active_tenders)} "
        f"(показаны {len(top_n)} с ближайшим сроком подачи заявок)\n\n"
    )
    body = "\n\n".join(format_tender_message(t, i + 1) for i, t in enumerate(top_n))
    send_telegram_message(header + body)

    # полный список — отдельным Excel-файлом при каждом поиске: название, платформа,
    # цена и ссылка (плюс номер/заказчик/срок — как дополнительная, но полезная информация)
    df = pd.DataFrame(
        [
            {
                "Название": t.get("title"),
                "Платформа": t.get("platform"),
                "Цена": t.get("price"),
                "Ссылка": t.get("link"),
                "Номер": t.get("number"),
                "Заказчик": t.get("customer"),
                "Окончание подачи заявок": t["deadline"].strftime("%d.%m.%Y") if t.get("deadline") else None,
            }
            for t in active_tenders
        ]
    )
    out_path = "/tmp/tenders.xlsx"
    df.to_excel(out_path, index=False)
    send_telegram_document(out_path, caption=f"Полный список ({len(active_tenders)} тендеров)")


if __name__ == "__main__":
    main()
