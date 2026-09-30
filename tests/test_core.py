from avito_bot import catalog
from avito_bot.avito import Listing, accept, parse_listings, search_url, Blocked
import pytest

items, settings = catalog.load("avito_catalog.xlsx")
by_model = {i.model: i for i in items}


def test_tree_follows_diagram():
    t = catalog.Tree(items)
    n = t.root
    for title in ["Видеокарты", "NVIDIA", "RTX", "40", "4060"]:
        n = n.children[title]
    assert set(n.children) == {"RTX 4060", "RTX 4060 Ti"}
    assert n.children["RTX 4060"].items[0].price_max == 25000
    assert len(t.root.children) == 4


def test_search_url_spb_radius():
    u = search_url(by_model["RTX 4060"], settings)
    assert u.startswith("https://www.avito.ru/sankt-peterburg/tovary_dlya_kompyutera/komplektuyuschie/videokarty?")
    assert "radius=50" in u and "pmin=15000" in u and "pmax=25000" in u


def L(title, price=20000, text="", region="sankt-peterburg"):
    return Listing(id="1", title=title, price=price, url=f"https://www.avito.ru/{region}/tovary/x_1", text=title + " " + text)


def test_only_spb_and_lo():
    it = by_model["RTX 4060"]
    assert accept(L("RTX 4060", region="gatchina"), it, settings)
    assert accept(L("RTX 4060", region="leningradskaya_oblast_kirishi"), it, settings)
    assert not accept(L("RTX 4060", region="moskva"), it, settings)


def test_memory_variants_one_button():
    rows = [["Раздел 1", "Модель (кнопка)", "Вариант", "Цена от, ₽", "Цена до, ₽", "Поисковый запрос", "Обязательные слова", "Минус-слова (доп.)"],
            ["Видеокарты", "RTX 4060 Ti", "8GB", 25000, 30000, "RTX 4060 Ti", "Ti, 8GB", ""],
            ["Видеокарты", "RTX 4060 Ti", "16GB", 30000, 35000, "RTX 4060 Ti", "Ti, 16GB", ""]]
    its = catalog.parse_rows(rows)
    t = catalog.Tree(its)
    node = t.root.children["Видеокарты"].children["RTX 4060 Ti"]
    assert [i.variant for i in node.items] == ["8GB", "16GB"] and len({i.key for i in its}) == 2
    v8, v16 = node.items
    assert accept(L("Palit RTX 4060 Ti 16 Гб", 32000), v16, settings)
    assert not accept(L("Palit RTX 4060 Ti 16 Гб", 32000), v8, settings)
    assert accept(L("RTX 4060 Ti Dual", 27000, text="память 8gb"), v8, settings)
    assert not accept(L("RTX 4060 Ti 16GB", 40000), v16, settings)  # дороже своего диапазона
    # без колонки «Вариант» версии называются по обязательным словам
    rows2 = [[c for j, c in enumerate(r) if j != 2] for r in rows]
    assert [i.variant for i in catalog.parse_rows(rows2)] == ["Ti, 8GB", "Ti, 16GB"]


def test_filters():
    it = by_model["RTX 4060"]
    assert accept(L("Видеокарта MSI RTX 4060 Ventus 8GB"), it, settings)
    assert not accept(L("RTX 4060 Ti 8GB"), it, settings)                 # минус-слово
    assert not accept(L("RTX 4070 Super"), it, settings)                  # другая модель
    assert not accept(L("RTX 4060", text="есть артефакты"), it, settings) # стоп-слово
    assert not accept(L("RTX 4060", price=40000), it, settings)           # дороже диапазона
    assert not accept(L("RTX 4060 на запчасти"), it, settings)
    ti = by_model["RTX 4060 Ti"]
    assert accept(L("Palit RTX 4060 Ti Dual 16GB", 32000), ti, settings)
    assert accept(L("GTX 1060 6GB Palit", 5000), by_model["GTX 1060 6GB"], settings)
    assert not accept(L("GTX 1060 3GB", 5000), by_model["GTX 1060 6GB"], settings)


SAMPLE = """<div data-marker="catalog-serp">
<div data-marker="item" data-item-id="111"><a data-marker="item-title" href="/sankt-peterburg/tovary_dlya_kompyutera/rtx_4060_111" title="RTX 4060 Gigabyte"><h3 itemprop="name">RTX 4060 Gigabyte</h3></a>
<meta itemprop="price" content="23000"><div data-marker="item-address">Купчино</div></div>
<div data-marker="item" data-item-id="222"><a data-marker="item-title" href="/x/rtx_4060_222" title="RTX 4060"></a><span data-marker="item-price">21 500 ₽</span></div>
</div>"""


def test_parse():
    ls = parse_listings(SAMPLE)
    assert [(l.id, l.price) for l in ls] == [("111", 23000), ("222", 21500)]
    assert ls[0].url.startswith("https://www.avito.ru/") and ls[0].place == "Купчино"
    with pytest.raises(Blocked):
        parse_listings("<html><title>Доступ ограничен: проблема с IP</title></html>")


def test_db_new_only_after_first_run(tmp_path):
    from avito_bot.db import DB
    db = DB(str(tmp_path / "t.db"))
    a, b = Listing("1", "a", 1, "u1"), Listing("2", "b", 2, "u2")
    assert db.save("k", [a]) == []          # первый проход: без уведомлений
    assert [l.id for l in db.save("k", [a, b])] == ["2"]
    assert db.rows("k") == []                 # пока страницы не проверены — не показываем
    db.set_detail("1", True); db.set_detail("2", False, "бронь")
    assert [l.id for l in db.rows("k")] == ["1"]


def test_real_table_rows():
    """Строки из рабочей таблицы (30.09): процессоры с суффиксами, отрицания в описании."""
    rows = [["Раздел 1", "Модель (кнопка)", "Вкл", "Поисковый запрос", "Обязательные слова", "Минус-слова (доп.)"],
            ["Процессоры", "i5-12400F", "да", "i5 12400F", "12400", "ES, QS"],
            ["Процессоры", "i5-12600K", "да", "i5 12600K", "12600", "KF"],
            ["Процессоры", "Ryzen 5 5600", "да", "Ryzen 5 5600", "5600", "5600X, 5600G"]]
    its = {i.model: i for i in catalog.parse_rows(rows)}
    assert accept(L("Intel Core i5-12400F", 9000), its["i5-12400F"], settings)
    assert not accept(L("Intel Core i5-12600KF", 15000), its["i5-12600K"], settings)
    assert accept(L("Intel Core i5-12600K OEM", 15000), its["i5-12600K"], settings)
    assert not accept(L("AMD Ryzen 5 5600X", 8000), its["Ryzen 5 5600"], settings)
    gpu = by_model["RTX 4060"]
    assert accept(L("RTX 4060", text="без артефактов, без дефектов, всё работает"), gpu, settings)
    assert accept(L("RTX 4060", text="отвечу на сколько угодно вопросов"), gpu, settings)
    assert not accept(L("RTX 4060", text="небольшой скол на кожухе"), gpu, settings)
    assert not accept(L("RTX 4060Ti 8gb"), gpu, settings)


def test_accessories_and_reserved():
    s2 = catalog.Settings(dict(settings.raw))
    s2.raw["стоп-слова для всех строк (только исправное)"] += ", зарезервирован, в резерве, забронирован, бронь, продана, продано"
    it = by_model["RTX 4060"]
    assert not accept(L("Коробка от видеокарты RTX 4060 Palit", 20000), it, s2)
    assert accept(L("RTX 4060 Palit", 20000, text="полный комплект, в коробке"), it, s2)
    assert not accept(L("RTX 4060 Palit", 20000, text="Зарезервирован"), it, s2)
    assert not accept(L("RTX 4060 Palit", 20000, text="уже забронирована до вечера"), it, s2)
    assert not accept(L("Куплю RTX 4060", 20000), it, s2)
    assert accept(L("RTX 4060 Palit", 20000, text="без брони, свободна"), it, s2)


def test_prices_from_google_export():
    assert catalog.to_int(16000.0) == 16000
    assert catalog.to_int("16 000") == 16000
    assert catalog.to_int("16000.0") == 16000
    assert catalog.to_int("16 000 ₽") == 16000


def test_detail_page():
    from avito_bot.avito import check_detail
    page = lambda status, desc: (f'<html><body><h1>RTX 3060</h1><div>{status}</div>'
                                 f'<div data-marker="item-view/item-description">{desc}</div>'
                                 f'<script>var x="зарезервирован"</script></body></html>')
    assert check_detail(page("", "Карта в идеале, без артефактов"), settings)[:2] == (True, "")
    assert not check_detail(page("Зарезервирован", "Карта в идеале"), settings)[0]
    assert not check_detail(page("", "Работала в майнинге, есть артефакты при нагрузке"), settings)[0]
    assert not check_detail(page("", "Уже продана, спасибо"), settings)[0]
    assert check_detail(page("Продано 25 товаров", "Карта в идеале"), settings)[0]


def test_display_refilters_stale_rows(tmp_path):
    import os
    os.environ["BOT_TOKEN"] = "1:a"
    from avito_bot.bot import App
    from avito_bot.db import DB
    a = App.__new__(App)
    a.fallback = False
    a.tree = catalog.Tree(items)
    a.settings = settings
    a.db = DB(str(tmp_path / "d.db"))
    n = a.tree.root
    for t in ["Видеокарты", "NVIDIA", "RTX", "40", "4060", "RTX 4060"]:
        n = n.children[t]
    it = n.items[0]
    a.db.save(it.key, [Listing("1", "Коробка от видеокарты RTX 4060", 300, "https://www.avito.ru/sankt-peterburg/a/b_1"),
                       Listing("2", "RTX 4060 Palit", 20000, "https://www.avito.ru/sankt-peterburg/a/b_2"),
                       Listing("3", "RTX 4060 MSI", 90000, "https://www.avito.ru/sankt-peterburg/a/b_3")])
    for i in ("1", "2", "3"):
        a.db.set_detail(i, True)
    text, _ = a.listings_view(n)
    assert "Palit" in text and "Коробка" not in text and "MSI" not in text


STOP_V2 = ("перебо, пропада, пропал, не появ, нет картинки, нет изображения, черный экран, чёрный экран, "
           "полосы, мерцает, мерцание, вылета, вылет, глючит, глюк, фриз, зависает, перезагружа, "
           "ошибк, код 43, проблем, причина не ясна, отключается, слетает драйвер, греется, перегрев, "
           "реболл, прогрев, перепай, после пайки, замена чипа, донор, требует ремонта")


def test_symptom_words():
    from avito_bot.avito import check_detail
    s2 = catalog.Settings(dict(settings.raw))
    s2.raw["стоп-слова для всех строк (только исправное)"] += ", " + STOP_V2
    real = ("Видеокарта работает с перебоями. 3 года работала идеально, в последнее время стала пропадать "
            "картина. Последний раз - картинка не появилась, причина не ясна. Внешне карта целая. Коробки нет")
    page = lambda d: f'<html><head><meta name="description" content="{d}"></head><body><h1>RTX 3070</h1></body></html>'
    assert not check_detail(page(real), s2)[0]               # описание только в мета-теге — тоже ловим
    assert check_detail(page("Работает идеально, проблем нет, в играх без артефактов"), s2)[0]
    assert check_detail(page("Никаких проблем, ошибок не было, не майнила"), s2)[0]
    assert not check_detail(page("Иногда выдаёт ошибку 43"), s2)[0]
    assert check_detail(page("Не греется, не глючит, тихая"), s2)[0]
    assert not check_detail(page("Сильно греется под нагрузкой"), s2)[0]


def test_new_stop_word_hides_already_checked(tmp_path):
    from avito_bot.db import DB
    db = DB(str(tmp_path / "s.db"))
    it = by_model["RTX 4060"]
    db.save(it.key, [Listing("7", "RTX 4060 Palit", 20000, "https://www.avito.ru/sankt-peterburg/a/b_7")])
    db.set_detail("7", True, "", "работает с перебоями, причина не ясна")
    assert [l.id for l in db.rows(it.key) if accept(l, it, settings)] == ["7"]
    s2 = catalog.Settings(dict(settings.raw))
    s2.raw["стоп-слова для всех строк (только исправное)"] += ", перебо"
    assert [l.id for l in db.rows(it.key) if accept(l, it, s2)] == []


def test_laptop_title():
    s2 = catalog.Settings(dict(settings.raw))
    s2.raw["минус-слова в заголовке для всех строк"] = "ноутбук, laptop, лептоп, mobile, max-q"
    it = by_model["RTX 4060"]
    assert not accept(L("RTX 4060 Laptop GPU 8GB"), it, s2)
    assert not accept(L("Игровой ноутбук MSI RTX 4060"), it, s2)
    assert accept(L("RTX 4060 Palit Dual"), it, s2)
