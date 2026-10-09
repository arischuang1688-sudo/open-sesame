"""Offline calendar and actual workflow guard regression tests (stdlib only)."""
import contextlib
import copy
import io
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import re
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import trading_day as gate

WORKFLOW = (ROOT / ".github/workflows/main.yml").read_text(encoding="utf-8")
STEPS = dict(re.findall(r"^      - name: ([^\n]+)\n(.*?)(?=^      - name: |\Z)",
                        WORKFLOW, re.MULTILINE | re.DOTALL))
GENERATOR = "60% Generate latest date-specific TWSE data"
FRESHNESS = "65% New trading day guard"
DOWNSTREAM = ["80% Strict same-trading-day validation", "85% Finalize ranking",
              "95% Publish dashboard data"]


def moment(value):
    return datetime.fromisoformat(value)


def allowed(step, outputs):
    """Evaluate the real, simple Actions output comparisons used by this workflow."""
    condition = re.search(r"^        if: (.+)$", STEPS[step], re.MULTILINE).group(1)
    expression = re.sub(r"steps\.(\w+)\.outputs\.(\w+)",
                        lambda m: repr(outputs.get((m[1], m[2]), "")), condition)
    return eval(expression.replace("&&", "and"), {"__builtins__": {}}, {})


def inline_script(step):
    code = STEPS[step].split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    return compile(textwrap.dedent(code), str(ROOT / ".github/workflows/main.yml"), "exec")


def run_inline(step, old, new, now=None):
    """Execute the shipped inline code and parse GITHUB_OUTPUT like Actions does."""
    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as streams:
        output = Path(tmp) / "output"
        dashboard = Path(tmp) / "dashboard.json"
        dashboard.write_text(json.dumps(new), encoding="utf-8")
        real_open = open

        def redirect(path, *args, **kwargs):
            return streams.enter_context(real_open(
                dashboard if path == "data/dashboard.json" else path, *args, **kwargs))

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now.replace(tzinfo=None)

        with patch("builtins.open", side_effect=redirect), \
                patch("os.popen", return_value=io.StringIO(json.dumps(old))), \
                patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), \
                contextlib.redirect_stdout(io.StringIO()), \
                patch("datetime.datetime", Clock if now else datetime):
            exec(inline_script(step), {})
        raw = output.read_text(encoding="utf-8")
        # Regression: a literal backslash-n made the old skip output 'true\\n'.
        if raw not in ("skip=true\n", "skip=false\n"):
            raise AssertionError(f"Malformed Actions output: {raw!r}")
        return dict(line.split("=", 1) for line in raw.splitlines())


def dashboard(day="2026-10-08", margins=0):
    return {"market": {"quote": {"date": day}},
            "stocks": [{"margin": "100 張" if i < margins else "資料待補"}
                       for i in range(200)]}


class TradingCalendarTest(unittest.TestCase):
    def decide(self, day, event="schedule", backfill=False, **kwargs):
        return gate.decision(moment(day + "T21:05:00+08:00"), event, backfill, **kwargs)

    def test_normal_trading_days_and_official_open_rows(self):
        for day in ("2026-01-02", "2026-02-11", "2026-02-23", "2026-10-08",
                    "2026-10-12", "2026-10-27", "2026-12-31"):
            with self.subTest(day=day):
                self.assertFalse(self.decide(day)["skip"])

    def test_all_2026_official_closures_and_weekends(self):
        closed = ["01-01", "02-12", "02-13", "02-15", "02-16", "02-17", "02-18",
                  "02-19", "02-20", "02-27", "02-28", "04-03", "04-04", "04-05",
                  "04-06", "05-01", "06-19", "09-25", "09-28", "10-09", "10-10",
                  "10-25", "10-26", "12-25"]
        actual = gate.load_calendar(2026)
        self.assertEqual({d for d, row in actual.items() if not row["open"]},
                         {"2026-" + d for d in closed})
        for day in closed + ["10-11", "10-17", "10-18"]:
            with self.subTest(day=day):
                self.assertTrue(self.decide("2026-" + day)["skip"])

    def test_manual_backfill_is_explicit_and_dispatch_only(self):
        for day in ("2026-10-09", "2026-10-26", "2026-10-11"):
            self.assertTrue(self.decide(day, "workflow_dispatch")["skip"])
            self.assertFalse(self.decide(day, "workflow_dispatch", True)["skip"])
            self.assertTrue(self.decide(day, "schedule", True)["skip"])

    def test_delayed_run_uses_taipei_execution_date(self):
        before = gate.decision(moment("2026-10-08T15:59:59+00:00"), "schedule")
        after = gate.decision(moment("2026-10-08T16:00:00+00:00"), "schedule")
        self.assertEqual((before["date"], before["skip"]), ("2026-10-08", False))
        self.assertEqual((after["date"], after["skip"]), ("2026-10-09", True))

    def test_unknown_year_fails_closed_including_manual_backfill(self):
        for event, backfill in [("schedule", False), ("workflow_dispatch", True)]:
            with self.assertRaisesRegex(gate.CalendarError, "2027 missing or invalid"):
                gate.decision(moment("2026-12-31T16:00:00+00:00"), event, backfill)

    def test_cross_year_selects_each_year_and_resumes_with_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = json.loads((gate.CALENDAR_DIR / "twse-2026.json").read_text())
            (Path(tmp) / "twse-2026.json").write_text(json.dumps(source))
            # Synthetic next-year fixture only: not a published 2027 calendar.
            next_year = {**source, "year": 2027, "coverage": ["2027-01-01", "2027-12-31"],
                         "entries": [{"date": "2027-01-01", "name": "test closure", "open": False}]}
            (Path(tmp) / "twse-2027.json").write_text(json.dumps(next_year))
            self.assertFalse(self.decide("2026-12-31", directory=tmp)["skip"])
            self.assertTrue(gate.decision(moment("2026-12-31T16:00:00+00:00"),
                                          "schedule", directory=tmp)["skip"])
            self.assertFalse(self.decide("2027-01-04", directory=tmp)["skip"])

    def test_missing_corrupt_incomplete_or_wrong_year_calendar_stops(self):
        original = json.loads((gate.CALENDAR_DIR / "twse-2026.json").read_text())
        cases = ["bad json", "{}", json.dumps({**original, "year": 2025}),
                 json.dumps({**original, "coverage": ["2026-01-01", "2026-10-31"]}),
                 json.dumps({**original, "entries": []}),
                 json.dumps({**original, "sources": []})]
        for field, value in [("date", "2027-01-01"), ("date", "2026-02-30"),
                             ("open", "false"), ("name", "")]:
            bad = copy.deepcopy(original)
            bad["entries"][0][field] = value
            cases.append(json.dumps(bad))
        bad = copy.deepcopy(original)
        bad["entries"].append(bad["entries"][0])
        cases.append(json.dumps(bad))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(gate.CalendarError):
                self.decide("2026-10-08", directory=tmp)
            for content in cases:
                with self.subTest(content=content[:60]):
                    (Path(tmp) / "twse-2026.json").write_text(content)
                    with self.assertRaises(gate.CalendarError):
                        self.decide("2026-10-08", directory=tmp)

    def test_invalid_clock_event_and_backfill_stop(self):
        for now, event, backfill in [(datetime(2026, 10, 8), "schedule", False),
                                    (moment("2026-10-08T21:05:00+08:00"), "push", False),
                                    (moment("2026-10-08T21:05:00+08:00"), "schedule", "false")]:
            with self.assertRaises(gate.CalendarError):
                gate.decision(now, event, backfill)

    def test_cli_is_offline_and_emits_exact_actions_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            summary = Path(tmp) / "summary"
            with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule", "BACKFILL": "false",
                                         "GITHUB_OUTPUT": str(output),
                                         "GITHUB_STEP_SUMMARY": str(summary)}), \
                    patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")) as http, \
                    contextlib.redirect_stdout(io.StringIO()):
                gate.main(moment("2026-10-09T21:05:00+08:00"))
                http.assert_not_called()
            self.assertEqual(output.read_text(), "skip=true\ntaipei_date=2026-10-09\n")
            self.assertIn("skip=true", summary.read_text())


class WorkflowGuardsTest(unittest.TestCase):
    def test_calendar_runs_before_any_data_fetch_and_all_steps_are_gated(self):
        names = list(STEPS)
        self.assertLess(names.index("12% Offline TWSE trading day guard"), names.index(GENERATOR))
        self.assertIn("BACKFILL: ${{ inputs.backfill || false }}", STEPS["12% Offline TWSE trading day guard"])
        outputs = {("calendar", "skip"): "true", ("cooldown", "skip"): "false",
                   ("freshness", "skip"): "false"}
        for step in ["20% Setup Python", GENERATOR, FRESHNESS] + DOWNSTREAM:
            action = Mock()
            if allowed(step, outputs):
                action()
            action.assert_not_called()
        # Fail closed if a guard ever fails to emit its expected output.
        for step in [GENERATOR, FRESHNESS] + DOWNSTREAM:
            self.assertFalse(allowed(step, {}))
        for step in DOWNSTREAM:
            self.assertFalse(allowed(step, {("calendar", "skip"): "false"}))

    def test_trading_day_runs_and_manual_backfill_cannot_bypass_cooldown(self):
        outputs = {("calendar", "skip"): "false", ("cooldown", "skip"): "false",
                   ("freshness", "skip"): "false"}
        for step in [GENERATOR, FRESHNESS] + DOWNSTREAM:
            self.assertTrue(allowed(step, outputs))
            self.assertFalse(allowed(step, {**outputs, ("cooldown", "skip"): "true"}))

    def test_actual_cooldown_15_minute_boundary(self):
        now = moment("2026-10-09T13:05:00+00:00")
        for age, skip in [(0, "true"), (899, "true"), (900, "false"), (901, "false")]:
            with self.subTest(age=age):
                data = {"updated_at": (now - timedelta(seconds=age)).isoformat()}
                self.assertEqual(run_inline("15% Cooldown guard", {}, data, now)["skip"], skip)

    def test_freshness_same_older_new_and_margin_exception(self):
        old = dashboard(margins=100)
        cases = [(dashboard(margins=100), "true"), (dashboard(margins=99), "true"),
                 (dashboard("2026-10-07", margins=200), "true"),
                 (dashboard("2026-10-12", margins=0), "false"),
                 (dashboard(margins=101), "false")]
        for new, skip in cases:
            with self.subTest(date=new["market"]["quote"]["date"], skip=skip):
                self.assertEqual(run_inline(FRESHNESS, old, new)["skip"], skip)
        with self.assertRaisesRegex(SystemExit, "generated trading date is missing"):
            run_inline(FRESHNESS, old, dashboard(""))

    def test_holiday_manual_backfill_allows_only_new_or_improved_data_once(self):
        now = moment("2026-10-09T21:05:00+08:00")
        result = gate.decision(now, "workflow_dispatch", True)
        old, new = dashboard("2026-10-07", 100), dashboard("2026-10-08", 100)
        for previous, candidate, publish in [(old, new, True), (new, new, False),
                                             (new, dashboard(margins=101), True)]:
            outputs = {("calendar", "skip"): str(result["skip"]).lower(),
                       ("cooldown", "skip"): "false",
                       ("freshness", "skip"): run_inline(FRESHNESS, previous, candidate)["skip"]}
            for step in DOWNSTREAM:
                self.assertEqual(allowed(step, outputs), publish)

    def test_schedule_concurrency_and_latest_checkout_preserved(self):
        self.assertIn('cron: "5 13 * * 1-5"', WORKFLOW)
        self.assertNotRegex(WORKFLOW, r"(?m)^\s+timezone:")
        self.assertIn("group: open-sesame-data-update", WORKFLOW)
        self.assertIn("cancel-in-progress: false", WORKFLOW)
        self.assertIn("ref: ${{ github.ref }}", STEPS["10% Checkout source"])
        self.assertIn("if: github.event_name == 'workflow_dispatch'", STEPS["15% Cooldown guard"])
        self.assertNotIn("force-update", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
