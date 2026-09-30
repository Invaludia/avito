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
    assert n.children["RTX 4060"].item.price_max == 25000
    assert len(t.root.children) == 4


def test_search_url_spb_radius():
    u = search_url(by_model["RTX 4060"], settings)
    assert u.startswith("https://www.avito.ru/sankt-peterburg/tovary_dlya_kompyutera/komplektuyuschie/videokarty?")
    assert "radius=50" in u and "pmin=15000" in u and "pmax=25000" in u


def L(title, price=20000, text=""):
    return Listing(id="1", title=title, price=price, url="u", text=title + " " + text)


def test_filters():
    it = by_model["RTX 4060"]
    assert accept(L("Видеокарта MSI RTX 4060 Ventus 8GB"), it, settings)
    assert not accept(L("RTX 4060 Ti 8GB"), it, settings)                 # минус-слово
    assert not accept(L("RTX 4070 Super"), it, settings)                  # другая модель
    assert not accept(L("RTX 4060", text="есть артефакты"), it, settings) # стоп-слово
    assert not accept(L("RTX 4060", price=40000), it, settings)           # дороже диапазона
    assert not accept(L("RTX 4060 на запчасти"), it, settings)
    ti = by_model["RTX 4060 Ti"]
    assert accept(L("Palit RTX 4060 Ti Dual"), ti, settings)
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
