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
    assert len(db.top("k", 10, True)) == 2
