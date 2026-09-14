"""Статус прогона по конкурентам, время по Москве, свежесть отчёта в UI.

Мотив: клиент жаловался «часть данных из кеша» — время в шапке считалось
по mtime файлов, которое после перезапуска Streamlit Cloud равно времени
клонирования репозитория. Теперь единственный источник времени — run_status.
"""

import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_status import (  # noqa: E402
    EMPTY_STATE_MESSAGE,
    MSK,
    date_tag_msk,
    fmt_ts,
    normalize_source,
    now_msk,
    parse_run_status,
    parse_ts,
    raw_staleness,
    read_log_tail,
    report_freshness,
    run_finished_at,
    source_line,
    today_msk,
)


# ── Время по Москве ──────────────────────────────────────────────────

class TestMoscowTime:
    def test_utc_evening_is_next_day_in_moscow(self):
        # контейнер облака в UTC: 22:30 UTC 14.09 = 01:30 МСК 15.09
        utc = datetime(2026, 9, 14, 22, 30, tzinfo=timezone.utc)
        assert today_msk(utc) == date(2026, 9, 15)
        assert date_tag_msk(utc) == "20260915"
        assert now_msk(utc).hour == 1

    def test_naive_now_is_treated_as_utc(self):
        naive = datetime(2026, 9, 14, 21, 0)
        assert date_tag_msk(naive) == "20260915"

    def test_utc_afternoon_same_day(self):
        utc = datetime(2026, 9, 14, 12, 21, tzinfo=timezone.utc)
        assert date_tag_msk(utc) == "20260914"
        assert fmt_ts(now_msk(utc)) == "14.09 15:21 МСК"

    def test_default_now_is_aware_moscow(self):
        dt = now_msk()
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(hours=3)

    def test_parse_ts_converts_offset_to_moscow(self):
        dt = parse_ts("2026-09-14T12:21:00+00:00")
        assert dt is not None
        assert (dt.hour, dt.minute) == (15, 21)
        assert dt.tzinfo == MSK

    def test_parse_ts_rejects_naive_and_garbage(self):
        # naive-строка старого формата: пояс контейнера неизвестен
        assert parse_ts("2026-09-02T10:00:00") is None
        assert parse_ts("вчера") is None
        assert parse_ts(None) is None
        assert fmt_ts(None) == "время не записано"


# ── run_status: новый и старый форматы ───────────────────────────────

OLD_FORMAT = {
    "generated_at": "2026-09-02T10:00:00",
    "sources": [
        {"competitor_id": "selectel", "name": "Селектел", "url": "u1",
         "offers": 118, "status": "ok"},
        {"competitor_id": "reg_cloud", "name": "REG.Cloud", "url": "u2",
         "offers": 0, "status": "error"},
    ],
}

NEW_FORMAT = {
    "version": 2,
    "timezone": "Europe/Moscow",
    "date_tag": "20260914",
    "generated_at": "2026-09-14T15:22:10+03:00",
    "sources": [
        {"competitor_id": "selectel", "name": "Селектел", "url": "u1",
         "provider": "selectel", "offers": 128, "status": "ok", "error": None,
         "started_at": "2026-09-14T15:21:00+03:00",
         "finished_at": "2026-09-14T15:21:40+03:00"},
        {"competitor_id": "reg_cloud", "name": "REG.Cloud", "url": "u2",
         "provider": "regcloud", "offers": 0, "status": "error",
         "error": "TimeoutError: Page.goto: Timeout 60000ms exceeded",
         "started_at": "2026-09-14T15:21:40+03:00",
         "finished_at": "2026-09-14T15:22:40+03:00"},
        {"competitor_id": "timeweb", "name": "Timeweb", "url": "u3",
         "provider": "timeweb_cloud", "offers": 0, "status": "empty",
         "error": "скрейп вернул 0 предложений",
         "started_at": "2026-09-14T15:22:40+03:00",
         "finished_at": "2026-09-14T15:22:41+03:00"},
    ],
}


class TestParseRunStatus:
    def test_old_format_reads_without_time(self):
        sources = parse_run_status(OLD_FORMAT)
        assert [s["status"] for s in sources] == ["ok", "error"]
        assert all(s["started_at"] is None and s["finished_at"] is None
                   for s in sources)
        assert sources[0]["provider"] is None
        assert sources[1]["error"] is None
        assert run_finished_at(sources) is None

    def test_new_format_has_moscow_times(self):
        sources = parse_run_status(NEW_FORMAT)
        assert sources[0]["finished_at"].hour == 15
        assert run_finished_at(sources) == parse_ts("2026-09-14T15:22:41+03:00")

    def test_garbage_payloads_are_empty(self):
        assert parse_run_status(None) == []
        assert parse_run_status([]) == []
        assert parse_run_status({"sources": "x"}) == []
        assert parse_run_status({"sources": [1, "a"]}) == []

    def test_normalize_fills_every_key(self):
        s = normalize_source({})
        assert s["status"] == "error" and s["offers"] == 0
        assert s["competitor_id"] == "" and s["error"] is None
        assert normalize_source({"offers": "abc"})["offers"] == 0


class TestSourceLine:
    def test_ok_line(self):
        s = normalize_source(NEW_FORMAT["sources"][0])
        assert source_line(s, "Selectel") == \
            "Selectel — 14.09 15:21 МСК, 128 предложений"

    def test_error_line_shows_text(self):
        s = normalize_source(NEW_FORMAT["sources"][1])
        line = source_line(s, "Reg.cloud")
        assert line.startswith("Reg.cloud — 14.09 15:22 МСК, ошибка: TimeoutError")

    def test_empty_line(self):
        s = normalize_source(NEW_FORMAT["sources"][2])
        assert source_line(s, "Timeweb") == "Timeweb — 14.09 15:22 МСК, данных нет"

    def test_old_format_line_without_time(self):
        ok, err = parse_run_status(OLD_FORMAT)
        assert source_line(ok, "Selectel") == \
            "Selectel — 118 предложений (время прогона не записано)"
        assert source_line(err, "Reg.cloud") == \
            "Reg.cloud — ошибка: данные не получены"


# ── Баннер «не свежий отчёт» и пустое состояние ──────────────────────

class TestReportFreshness:
    TODAY = date(2026, 9, 14)

    def test_no_report_is_empty_state(self):
        r = report_freshness("", [], today=self.TODAY)
        assert r["state"] == "none"
        assert r["message"] == EMPTY_STATE_MESSAGE
        assert "Запустить сравнение" in r["message"]

    def test_yesterday_report_is_stale(self):
        r = report_freshness("20260913", parse_run_status(NEW_FORMAT),
                             today=self.TODAY)
        assert r["state"] == "stale"
        assert r["message"].startswith("Показан отчёт от 13.09.2026")
        assert "не свежий прогон" in r["message"]
        assert "«Запустить сравнение»" in r["message"]

    def test_today_without_status_is_not_fresh(self):
        r = report_freshness("20260914", [], today=self.TODAY)
        assert r["state"] == "no_status"
        assert "время прогона" in r["message"]
        assert "«Запустить сравнение»" in r["message"]

    def test_today_with_status_is_fresh(self):
        r = report_freshness("20260914", parse_run_status(NEW_FORMAT),
                             today=self.TODAY)
        assert r == {"state": "fresh", "message": None}

    def test_default_today_is_moscow(self, monkeypatch):
        # 22:30 UTC 13.09 = уже 14.09 по Москве → отчёт 14.09 свежий
        import run_status as rs
        monkeypatch.setattr(
            rs, "today_msk",
            lambda now=None: date(2026, 9, 14))
        assert rs.report_freshness("20260914",
                                   parse_run_status(NEW_FORMAT))["state"] == "fresh"

    def test_garbage_tag_is_empty_state(self):
        assert report_freshness("latest", [], today=self.TODAY)["state"] == "none"


class TestRawStaleness:
    def test_older_raw_is_marked(self):
        m = raw_staleness("20260831", "20260914")
        assert m["stale"] is True
        assert m["label"] == "скрейп 31.08.2026 — старее отчёта от 14.09.2026"

    def test_same_day_with_time(self):
        when = parse_ts("2026-09-14T15:21:40+03:00")
        m = raw_staleness("20260914", "20260914", when)
        assert m == {"stale": False, "label": "скрейп 14.09.2026 15:21 МСК"}

    def test_missing_raw(self):
        assert raw_staleness(None, "20260914") == {"stale": True,
                                                   "label": "данных нет"}


class TestReadLogTail:
    def test_tail_and_missing(self, tmp_path):
        p = tmp_path / "pipeline.log"
        p.write_text("\n".join(f"line {i}" for i in range(100)), encoding="utf-8")
        tail = read_log_tail(p, lines=3)
        assert tail == "line 97\nline 98\nline 99"
        assert read_log_tail(tmp_path / "nope.log") == ""
        assert read_log_tail(None) == ""


# ── Пайплайн: run_status по конкурентам с моками скрейперов ─────────

def _competitors():
    from config_loader import Competitor
    return [
        Competitor(competitor_id="selectel", name="Селектел", url="u1",
                   currency="RUB", price_period="month",
                   parsing_profile="selectel_nuxt_cdn"),
        Competitor(competitor_id="reg_cloud", name="REG.Cloud", url="u2",
                   currency="RUB", price_period="month",
                   parsing_profile="regcloud_playwright"),
        Competitor(competitor_id="timeweb", name="Timeweb", url="u3",
                   currency="RUB", price_period="month",
                   parsing_profile="timeweb_cloud_nuxt",
                   extra={"locations": ["msk"]}),
    ]


ROW = {
    "plan_id": "P1", "cpu_model": "Intel Xeon E-2386G",
    "cpu_model_norm": "intel xeon e-2386g", "cpu_sockets": 1,
    "cpu_cores_total": 6, "ram_gb": 64, "price_rub": 10000.0,
    "currency": "RUB", "price_period": "month", "quantity_available": 3,
    "disk_pools": [{"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 480}],
}


class TestCollectSources:
    def test_status_per_competitor_with_one_failure(self, tmp_path):
        from competitor_pipeline import collect_sources

        saved = {}

        def scrape(comp):
            if comp.parsing_profile == "regcloud_playwright":
                raise TimeoutError("Page.goto: Timeout 60000ms exceeded")
            if comp.parsing_profile == "timeweb_cloud_nuxt":
                return []
            return [dict(ROW), dict(ROW, plan_id="P2")]

        def save_raw(provider, rows, date_tag):
            saved[provider] = (len(rows), date_tag)

        def check(provider, rows, html=""):
            return {"status": "clean", "discrepancies": [], "detail": ""}

        offers, status = collect_sources(
            _competitors(), no_scrape=False, date_tag="20260914",
            scrape=scrape, save_raw=save_raw, check=check,
        )
        assert len(offers) == 2
        by_id = {s["competitor_id"]: s for s in status}
        assert by_id["selectel"]["status"] == "ok"
        assert by_id["selectel"]["offers"] == 2
        assert by_id["selectel"]["provider"] == "selectel"
        assert by_id["selectel"]["url"] == "u1"
        assert by_id["selectel"]["check"]["status"] == "clean"
        assert by_id["reg_cloud"]["status"] == "error"
        assert by_id["reg_cloud"]["error"].startswith("TimeoutError: Page.goto")
        assert by_id["timeweb"]["status"] == "empty"
        assert by_id["timeweb"]["error"] == "скрейп вернул 0 предложений"
        # raw-JSON пишется только для непустого скрейпа, с датой прогона
        assert saved == {"selectel": (2, "20260914")}
        # время у каждого: tz-aware, по Москве, окончание не раньше начала
        for s in status:
            started, finished = parse_ts(s["started_at"]), parse_ts(s["finished_at"])
            assert started is not None and finished is not None
            assert finished >= started
            assert "+03:00" in s["finished_at"]

    def test_no_scrape_reads_raw_and_marks_missing(self):
        from competitor_pipeline import collect_sources

        def load_raw(provider):
            return [dict(ROW)] if provider == "selectel" else []

        offers, status = collect_sources(
            _competitors(), no_scrape=True, date_tag="20260914",
            load_raw=load_raw, scrape=lambda c: pytest.fail("скрейп не нужен"),
        )
        assert len(offers) == 1
        by_id = {s["competitor_id"]: s for s in status}
        assert by_id["selectel"]["status"] == "ok"
        assert by_id["reg_cloud"]["status"] == "error"
        assert by_id["reg_cloud"]["error"] == "нет сохранённых данных в data/"
        assert "check" not in by_id["selectel"]


class TestWriteRunStatusV2:
    def test_payload_is_versioned_and_moscow(self, tmp_path):
        from competitor_pipeline import write_run_status

        path = write_run_status(NEW_FORMAT["sources"], "20260914", out_dir=tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == 2
        assert data["timezone"] == "Europe/Moscow"
        assert data["date_tag"] == "20260914"
        assert data["generated_at"].endswith("+03:00")
        # приложение читает обратно с временем
        sources = parse_run_status(data)
        assert sources[0]["finished_at"].minute == 21


class TestPipelineLog:
    def test_log_handler_not_duplicated(self, tmp_path):
        import competitor_pipeline as cp

        root = logging.getLogger()
        before = list(root.handlers)
        try:
            p1 = cp.ensure_pipeline_log(tmp_path, "20260914")
            p2 = cp.ensure_pipeline_log(tmp_path, "20260914")
            assert p1 == p2 == (tmp_path / "pipeline_20260914.log").resolve()
            added = [h for h in root.handlers if h not in before]
            assert len(added) == 1
            cp.log.info("проверка записи в лог")
            added[0].flush()
            assert "проверка записи в лог" in p1.read_text(encoding="utf-8")
            # новая дата: старый хендлер снят, новый один
            p3 = cp.ensure_pipeline_log(tmp_path, "20260915")
            added = [h for h in root.handlers if h not in before]
            assert len(added) == 1 and p3.name == "pipeline_20260915.log"
        finally:
            for path, h in list(cp._LOG_HANDLERS.items()):
                root.removeHandler(h)
                h.close()
                del cp._LOG_HANDLERS[path]

    def test_run_pipeline_reports_errors_and_log(self, tmp_path, monkeypatch):
        """Полный отказ всех источников: код 1, текст по конкурентам,
        лог pipeline_<дата>.log с ошибкой, run_status не пишется."""
        import argparse

        import competitor_pipeline as cp

        monkeypatch.setattr(cp, "load_competitors", lambda p: _competitors())
        monkeypatch.setattr(cp, "load_matching_rules", lambda p: None)
        monkeypatch.setattr(cp, "load_cpu_specs", lambda p: None)
        monkeypatch.setattr(cp, "load_reference_configs", lambda p: [])
        monkeypatch.setattr(cp, "load_disk_classes", lambda p: None)

        def scrape(comp):
            raise RuntimeError("сеть недоступна")

        monkeypatch.setattr(cp, "_scrape_competitor", scrape)
        # страховка: в data/ проекта ничего не пишем
        monkeypatch.setattr(
            cp, "_save_raw_json",
            lambda *a, **k: pytest.fail("сырой JSON писаться не должен"))
        args = argparse.Namespace(
            no_scrape=False, xlsx=False, out_dir=tmp_path, configs=None,
            competitors=None, matching=None, cpu_specs=None, disk_classes=None,
        )
        try:
            result = cp.run_pipeline(args)
            assert result.code == 1
            assert result.error.startswith("Ни один конкурент")
            errors = result.source_errors()
            assert len(errors) == 3
            assert errors[0] == "Селектел: RuntimeError: сеть недоступна"
            assert result.log_path.name == f"pipeline_{date_tag_msk()}.log"
            for h in cp._LOG_HANDLERS.values():
                h.flush()
            text = result.log_path.read_text(encoding="utf-8")
            assert "сеть недоступна" in text
            assert "Ни один конкурент" in read_log_tail(result.log_path, 5)
            assert not list(tmp_path.glob("run_status_*.json"))
        finally:
            root = logging.getLogger()
            for path, h in list(cp._LOG_HANDLERS.items()):
                root.removeHandler(h)
                h.close()
                del cp._LOG_HANDLERS[path]


# ── Приложение целиком (streamlit.testing, без скрейпа) ─────────────

def _load_apptest():
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest
    return AppTest


def _texts(at) -> str:
    parts = [m.value for m in at.markdown]
    parts += [w.value for w in at.warning]
    parts += [i.value for i in at.info]
    parts += [e.value for e in at.error]
    parts += [c.value for c in at.caption]
    return "\n".join(str(p) for p in parts)


class TestAppHeadless:
    def _run(self, cwd, monkeypatch):
        AppTest = _load_apptest()
        import streamlit as st
        st.cache_data.clear()  # кеш переживает запуски в одном процессе
        monkeypatch.chdir(cwd)
        monkeypatch.syspath_prepend(str(ROOT))
        at = AppTest.from_file(str(ROOT / "dedicated_app.py"), default_timeout=120)
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        return at

    def test_empty_state_without_reports(self, tmp_path, monkeypatch):
        # после соседнего PR data/ уходит из git — приложение стартует пустым
        at = self._run(tmp_path, monkeypatch)
        text = _texts(at)
        assert EMPTY_STATE_MESSAGE in text
        assert "нет данных" in text
        assert not at.exception

    def test_fresh_report_shows_sources_and_no_banner(self, tmp_path, monkeypatch):
        from competitor_pipeline import write_run_status

        reports = tmp_path / "data" / "reports"
        reports.mkdir(parents=True)
        tag = date_tag_msk()
        (reports / f"dedicated_competitors_{tag}.csv").write_text(
            "config_id,cpu_model,cpu_sockets,cpu_cores_per_socket,ram_gb,disks,"
            "miran_price,selectel_price,reg_cloud_price\n"
            "MIR-001,Intel Xeon E-2386G,1,6,64,2×480 ГБ SSD,12000,10000,\n",
            encoding="utf-8-sig")
        now = now_msk()
        sources = [
            dict(s, started_at=now.isoformat(timespec="seconds"),
                 finished_at=now.isoformat(timespec="seconds"))
            for s in NEW_FORMAT["sources"]
        ]
        write_run_status(sources, tag, out_dir=reports)
        # сырой скрейп старее отчёта → должен быть помечен
        (tmp_path / "data" / "selectel_20260831.json").write_text(
            json.dumps([ROW]), encoding="utf-8")

        at = self._run(tmp_path, monkeypatch)
        text = _texts(at)
        assert f"Selectel — {now:%d.%m %H:%M} МСК, 128 предложений" in text
        assert "Reg.cloud — " in text and "ошибка: TimeoutError" in text
        assert "Timeweb — " in text and "данных нет" in text
        assert "не свежий прогон" not in text
        assert "старее отчёта от" in text
        assert "0 предложений конкурентов" in text

    def test_old_report_shows_stale_banner(self, tmp_path, monkeypatch):
        reports = tmp_path / "data" / "reports"
        reports.mkdir(parents=True)
        (reports / "dedicated_competitors_20260831.csv").write_text(
            "config_id,cpu_model,cpu_sockets,cpu_cores_per_socket,ram_gb,disks,"
            "miran_price,selectel_price\n"
            "MIR-001,Intel Xeon E-2386G,1,6,64,2×480 ГБ SSD,12000,10000\n",
            encoding="utf-8-sig")
        (reports / "run_status_20260831.json").write_text(
            json.dumps(OLD_FORMAT, ensure_ascii=False), encoding="utf-8")
        at = self._run(tmp_path, monkeypatch)
        text = _texts(at)
        assert "Показан отчёт от 31.08.2026 — это не свежий прогон" in text
        assert "время прогона не записано" in text
        assert not at.exception

    def test_real_project_data_renders(self, monkeypatch):
        if not (ROOT / "data" / "reports").exists():
            pytest.skip("в проекте нет data/reports")
        at = self._run(ROOT, monkeypatch)
        assert "Цены конкурентов по эталонным конфигурациям" in _texts(at)
