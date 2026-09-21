# Снимки источников 15.09.2026 (для tests/test_golden_cases.py)

| Файл | Источник | Как снят |
|---|---|---|
| `regcloud_dedicated.html` | https://reg.cloud/dedicated/ | Playwright 04:52 МСК после прокрутки, 155 карточек; у карточки оставлены data-id/data-price и блоки title/tags/cpu-title/cpu-power/gpu/ram/hdds/price/more-button |
| `timeweb_cloud_nuxt.json` | `__NUXT_DATA__` со страницы https://timeweb.cloud/services/dedicated-server?location=ru | requests 04:46 МСК, весь flat-массив (все ДЦ) |
| `timeweb_presets_ru-1.json` | https://timeweb.cloud/landing-api/dedicated/presets | 04:46 МСК, только location=ru-1 (СПб) |
| `selectel_servers.json` | https://api.selectel.ru/servers/v2/pub/service/server | 04:49 МСК, 156 конфигов, оставлены поля name/cpu/ram/disk/gpu/is_order/is_preorder/price_collection (RUB.month), available и location_price_collection только по локациям витрины msk/spb/nsk или с остатком > 0 |
| `selectel_location.json` | https://api.selectel.ru/servers/v2/pub/location | копия снимка 14.09.2026 (15.09 API не ответил за 40 с ×3); uuid локаций стабильны |

Факт витрины Timeweb 15.09 (Playwright, `timeweb_ru.png`): вкладка периода по умолчанию — «12 Месяцев / Скидка 10%», карточка «E-2236 / 16 / 480» показывает «10 764 ₽ в месяц / при оплате за год».
