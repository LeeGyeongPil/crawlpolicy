"""AC-6: diff lists only the cells whose verdict changed and exits 1 when any did."""
from __future__ import annotations


def test_changed_cells_only(cli, work, report):
    res = cli("diff", "robots.old.txt", "robots.txt")
    assert res.code == 1
    rep = report(work)
    assert rep["mode"] == "diff" and rep["cells"] == [] and rep["source"] is None
    assert rep["old"]["path"] == "robots.old.txt" and rep["new"]["path"] == "robots.txt"
    pairs = {(c["bot"], c["url"]): (c["before"]["verdict"], c["after"]["verdict"]) for c in rep["changed"]}
    assert len(pairs) == 7 == len(rep["changed"])
    assert {b for b, _ in pairs} == {"Google-Extended", "CCBot"}
    assert pairs[("Google-Extended", "/admin/users")] == ("Google-Extended /admin/users => deny @L9",
                                                          "Google-Extended /admin/users => allow @L0")
    assert pairs[("CCBot", "/")] == ("CCBot / => allow @L0", "CCBot / => deny @L7")
    assert ("GPTBot", "/") not in pairs                       # same verdict, only the rule line may differ
    assert "7 of 75 cells changed" in res.out


def test_identical_exit0(cli, work, report):
    res = cli("diff", "robots.txt", "robots.txt")
    assert res.code == 0
    assert report(work)["changed"] == []
    assert "No verdict changed" in res.out


def test_diff_with_given_urls_and_bots(cli, work, report):
    res = cli("diff", "robots.old.txt", "robots.txt", "--bots", "CCBot", "--url", "/blog/")
    assert res.code == 1
    assert [c["bot"] for c in report(work)["changed"]] == ["CCBot"]
