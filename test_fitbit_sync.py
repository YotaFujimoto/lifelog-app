"""fitbit_sync.py のテスト（Google・Notion には接続しない）。python3 -m unittest で動く。"""

import contextlib
import io
import unittest
import urllib.parse
from datetime import date, datetime, timezone
from unittest import mock

import fitbit_sync as fs


def civil(y, mo, d, h=None, mi=None):
    value = {"date": {"year": y, "month": mo, "day": d}}
    clock = {}
    if h:  # 0 の項目は省かれて届く
        clock["hours"] = h
    if mi:
        clock["minutes"] = mi
    if clock:
        value["time"] = clock
    return value


def interval(start, end):
    """start/end は日本時間の (年, 月, 日, 時, 分)。"""

    def utc(t):
        local = datetime(*t, tzinfo=fs.TZ)
        return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")

    return {
        "startTime": utc(start),
        "startUtcOffset": "32400s",
        "endTime": utc(end),
        "endUtcOffset": "32400s",
        "civilStartTime": civil(*start),
        "civilEndTime": civil(*end),
    }


def sleep_point(start, end, asleep=None, main=False, nap=False, summary=True):
    sleep = {"interval": interval(start, end), "type": "STAGES", "metadata": {"processed": True}}
    if main:
        sleep["metadata"]["mainSleep"] = True
    if nap:
        sleep["metadata"]["nap"] = True
    if summary and asleep is not None:
        sleep["summary"] = {"minutesAsleep": str(asleep), "minutesAwake": "10"}
    return {"dataPointName": "users/1/dataTypes/sleep/dataPoints/x", "sleep": sleep}


def exercise_point(kind, start, end, active=None, name=None):
    exercise = {"interval": interval(start, end), "exerciseType": kind, "displayName": name or kind.title()}
    if active is not None:
        exercise["activeDuration"] = f"{active}s"
    return {"exercise": exercise}


def sleeps(*points):
    return [fs.parse_sleep(p) for p in points]


class TimeTest(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(fs.seconds("900s"), 900)
        self.assertEqual(fs.seconds("3.5s"), 3.5)
        self.assertEqual(fs.seconds("-18000s"), -18000)
        self.assertIsNone(fs.seconds(None))
        self.assertIsNone(fs.seconds("15m"))

    def test_parse_time(self):
        self.assertEqual(fs.parse_time("2026-10-07T14:41:00Z"), datetime(2026, 10, 7, 14, 41, tzinfo=timezone.utc))
        self.assertEqual(
            fs.parse_time("2026-10-07T14:41:00.123456789Z"), datetime(2026, 10, 7, 14, 41, 0, 123456, tzinfo=timezone.utc)
        )
        self.assertEqual(fs.parse_time("2026-10-07T23:41:00+09:00").astimezone(timezone.utc).hour, 14)

    def test_moment_uses_civil_time(self):
        m = fs.moment(interval((2026, 10, 8, 0, 5), (2026, 10, 8, 7, 0)), "start")
        self.assertEqual(m.local, datetime(2026, 10, 8, 0, 5))  # 時が0なので省かれている
        self.assertEqual(m.physical, datetime(2026, 10, 7, 15, 5, tzinfo=timezone.utc))

    def test_moment_without_civil_time_uses_offset(self):
        raw = interval((2026, 10, 8, 6, 30), (2026, 10, 8, 7, 0))
        del raw["civilStartTime"]
        raw["startUtcOffset"] = "-14400s"  # 旅行先（UTC-4）
        self.assertEqual(fs.moment(raw, "start").local, datetime(2026, 10, 7, 17, 30))


class SleepTest(unittest.TestCase):
    D = date(2026, 10, 8)

    def test_one_night(self):
        result = fs.nights(sleeps(sleep_point((2026, 10, 7, 23, 41), (2026, 10, 8, 6, 58), 394, main=True)), [self.D])
        night = result[self.D]
        self.assertEqual((night.bed, night.wake), ("23:41", "06:58"))
        self.assertEqual(night.asleep, 394)
        self.assertEqual(night.awake, 437 - 394)
        self.assertEqual(fs.day_values(night, None), {"就寝": "23:41", "起床": "06:58", "睡眠(Fitbit)": 6.6, "覚醒(Fitbit)": 0.7})

    def test_split_night_is_one_night(self):
        # 夜中に目が覚めて75分起きていた → 記録が2つに分かれる
        first = sleep_point((2026, 10, 7, 22, 0), (2026, 10, 8, 0, 30), 140, nap=True)
        second = sleep_point((2026, 10, 8, 1, 45), (2026, 10, 8, 7, 30), 300, main=True)
        night = fs.nights(sleeps(second, first), [self.D])[self.D]
        self.assertEqual((night.bed, night.wake), ("22:00", "07:30"))
        self.assertEqual(night.asleep, 440)
        self.assertEqual(night.awake, 570 - 440)

    def test_first_part_ending_the_day_before(self):
        evening = sleep_point((2026, 10, 7, 21, 0), (2026, 10, 7, 23, 30), 140)
        main = sleep_point((2026, 10, 8, 0, 30), (2026, 10, 8, 7, 0), 360, main=True)
        night = fs.nights(sleeps(evening, main), [self.D])[self.D]
        self.assertEqual((night.bed, night.wake), ("21:00", "07:00"))

    def test_afternoon_nap_is_not_part_of_the_night(self):
        main = sleep_point((2026, 10, 8, 0, 0), (2026, 10, 8, 7, 0), 400, main=True)
        nap = sleep_point((2026, 10, 8, 14, 0), (2026, 10, 8, 14, 40), 35, nap=True)
        night = fs.nights(sleeps(main, nap), [self.D])[self.D]
        self.assertEqual(night.wake, "07:00")
        self.assertEqual(night.asleep, 400)

    def test_going_back_to_sleep_in_the_morning(self):
        main = sleep_point((2026, 10, 8, 0, 0), (2026, 10, 8, 6, 0), 330, main=True)
        again = sleep_point((2026, 10, 8, 7, 30), (2026, 10, 8, 9, 0), 80, nap=True)
        night = fs.nights(sleeps(main, again), [self.D])[self.D]
        self.assertEqual((night.bed, night.wake, night.asleep, night.awake), ("00:00", "09:00", 410, 540 - 410))

    def test_overlapping_record_is_not_counted_twice(self):
        main = sleep_point((2026, 10, 7, 23, 0), (2026, 10, 8, 7, 0), 420, main=True)
        copy = sleep_point((2026, 10, 7, 23, 10), (2026, 10, 8, 6, 50), 400)
        night = fs.nights(sleeps(main, copy), [self.D])[self.D]
        self.assertEqual(len(night.sessions), 1)
        self.assertEqual(night.asleep, 420)

    def test_each_day_gets_its_own_morning(self):
        d7 = sleep_point((2026, 10, 6, 23, 0), (2026, 10, 7, 7, 0), 420, main=True)
        d8 = sleep_point((2026, 10, 7, 23, 30), (2026, 10, 8, 6, 30), 380, main=True)
        result = fs.nights(sleeps(d8, d7), [date(2026, 10, 6), date(2026, 10, 7), self.D])
        self.assertEqual(sorted(result), [date(2026, 10, 7), self.D])
        self.assertEqual(result[date(2026, 10, 7)].wake, "07:00")
        self.assertEqual(result[self.D].bed, "23:30")

    def test_longest_non_nap_is_the_core(self):
        short = sleep_point((2026, 10, 8, 4, 0), (2026, 10, 8, 4, 30), 25, nap=True)
        long = sleep_point((2026, 10, 7, 22, 0), (2026, 10, 8, 0, 0), 110)
        night = fs.nights(sleeps(short, long), [self.D])[self.D]
        self.assertEqual((night.bed, night.wake), ("22:00", "00:00"))  # 4時間あいた仮眠は別

    def test_nap_only_day_is_skipped(self):
        # 夜の記録がない（時計を充電していた）日の昼寝を、就寝・起床として書かない
        nap = sleep_point((2026, 10, 8, 14, 0), (2026, 10, 8, 14, 40), 35, main=True, nap=True)
        self.assertEqual(fs.nights(sleeps(nap), [self.D]), {})

    def test_evening_doze_is_not_bedtime(self):
        doze = sleep_point((2026, 10, 7, 20, 0), (2026, 10, 7, 20, 40), 35, nap=True)
        main = sleep_point((2026, 10, 7, 23, 30), (2026, 10, 8, 7, 0), 420, main=True)
        night = fs.nights(sleeps(doze, main), [self.D])[self.D]
        self.assertEqual((night.bed, night.wake, night.asleep), ("23:30", "07:00", 420))

    def test_late_morning_nap_is_not_wake_time(self):
        main = sleep_point((2026, 10, 8, 1, 0), (2026, 10, 8, 9, 0), 450, main=True)
        nap = sleep_point((2026, 10, 8, 11, 0), (2026, 10, 8, 12, 30), 80, nap=True)
        self.assertEqual(fs.nights(sleeps(main, nap), [self.D])[self.D].wake, "09:00")

    def test_two_copies_of_the_first_part_count_once(self):
        first = sleep_point((2026, 10, 7, 22, 0), (2026, 10, 8, 0, 30), 140, nap=True)
        copy = sleep_point((2026, 10, 7, 22, 5), (2026, 10, 8, 0, 25), 130, nap=True)
        main = sleep_point((2026, 10, 8, 1, 45), (2026, 10, 8, 7, 30), 300, main=True)
        night = fs.nights(sleeps(first, copy, main), [self.D])[self.D]
        self.assertEqual(len(night.sessions), 2)
        self.assertEqual(night.asleep, 440)

    def test_evening_part_goes_to_the_next_morning(self):
        # 10/7 の朝の記録がない（時計を充電していた）とき、10/7 21時からの分は 10/8 の睡眠に入る
        evening = sleep_point((2026, 10, 7, 21, 0), (2026, 10, 7, 23, 30), 140)
        main = sleep_point((2026, 10, 8, 0, 30), (2026, 10, 8, 7, 0), 360, main=True)
        result = fs.nights(sleeps(evening, main), [date(2026, 10, 7), self.D])
        self.assertEqual(sorted(result), [self.D])
        self.assertEqual((result[self.D].bed, result[self.D].wake), ("21:00", "07:00"))

    def test_still_processing(self):
        point = sleep_point((2026, 10, 7, 23, 0), (2026, 10, 8, 7, 0), summary=False)
        night = fs.nights(sleeps(point), [self.D])[self.D]
        self.assertIsNone(night.asleep)
        self.assertEqual(fs.day_values(night, None), {"就寝": "23:00", "起床": "07:00"})

    def test_asleep_from_stages_when_summary_is_missing(self):
        point = sleep_point((2026, 10, 7, 23, 0), (2026, 10, 8, 1, 0), summary=False)
        base = point["sleep"]["interval"]["startTime"][:11]  # 2026-10-07T
        point["sleep"]["stages"] = [
            {"type": "AWAKE", "startTime": base + "14:00:00Z", "endTime": base + "14:20:00Z"},
            {"type": "LIGHT", "startTime": base + "14:20:00Z", "endTime": base + "15:20:00Z"},
            {"type": "DEEP", "startTime": base + "15:20:00Z", "endTime": base + "16:00:00Z"},
        ]
        self.assertEqual(fs.parse_sleep(point).asleep, 100)

    def test_zero_minutes_asleep_is_omitted_in_summary(self):
        point = sleep_point((2026, 10, 8, 3, 0), (2026, 10, 8, 3, 20), 0)
        point["sleep"]["summary"] = {"minutesAwake": "20"}
        self.assertEqual(fs.parse_sleep(point).asleep, 0)

    def test_real_response_shape(self):
        # 本物の応答（2026-10-09に確認）は interval に civilStartTime/civilEndTime がなく、時差だけが付く
        point = {
            "dataPointName": "users/1/dataTypes/sleep/dataPoints/2",
            "sleep": {
                "interval": {"startUtcOffset": "32400s", "endUtcOffset": "32400s",
                             "startTime": "2026-10-07T15:10:00Z", "endTime": "2026-10-08T01:40:00Z"},
                "summary": {"minutesInSleepPeriod": "630", "minutesAsleep": "420", "minutesAwake": "210"},
                "type": "STAGES",
                "metadata": {"mainSleep": True, "processed": True, "stagesStatus": "SUCCEEDED"},
            },
        }
        night = fs.nights(sleeps(point), [self.D])[self.D]
        self.assertEqual(fs.day_values(night, None), {"就寝": "00:10", "起床": "10:40", "睡眠(Fitbit)": 7.0, "覚醒(Fitbit)": 3.5})

    def test_broken_points_are_ignored(self):
        self.assertIsNone(fs.parse_sleep({}))
        self.assertIsNone(fs.parse_sleep({"sleep": {"interval": {"startTime": "x"}}}))


class ExerciseTest(unittest.TestCase):
    def workouts(self, *points):
        return [fs.parse_exercise(p) for p in points]

    def test_names_and_totals(self):
        text = fs.exercise_text(
            self.workouts(
                exercise_point("WALKING", (2026, 10, 8, 8, 0), (2026, 10, 8, 8, 21)),
                exercise_point("TENNIS", (2026, 10, 8, 10, 0), (2026, 10, 8, 11, 40), active=92 * 60),
                exercise_point("WALKING", (2026, 10, 8, 18, 0), (2026, 10, 8, 18, 18)),
            )
        )
        self.assertEqual(text, "ウォーキング 39分、テニス 92分")

    def test_unknown_type_uses_display_name(self):
        w = fs.parse_exercise(exercise_point("FENCING", (2026, 10, 8, 8, 0), (2026, 10, 8, 9, 0), name="フェンシング"))
        self.assertEqual(w.name, "フェンシング")

    def test_other_uses_custom_name(self):
        w = fs.parse_exercise(exercise_point("OTHER", (2026, 10, 8, 8, 0), (2026, 10, 8, 9, 0), name="坂ダッシュ"))
        self.assertEqual(w.name, "坂ダッシュ")
        w = fs.parse_exercise(exercise_point("OTHER", (2026, 10, 8, 8, 0), (2026, 10, 8, 9, 0), name=" "))
        self.assertEqual(w.name, "その他")

    def test_same_workout_from_two_sources_counts_once(self):
        text = fs.exercise_text(
            self.workouts(
                exercise_point("RUNNING", (2026, 10, 8, 7, 0), (2026, 10, 8, 7, 30)),
                exercise_point("RUNNING", (2026, 10, 8, 7, 2), (2026, 10, 8, 7, 28)),
            )
        )
        self.assertEqual(text, "ランニング 30分")

    def test_same_ride_with_different_type_names_counts_once(self):
        text = fs.exercise_text(
            self.workouts(
                exercise_point("BIKING", (2026, 10, 8, 7, 0), (2026, 10, 8, 8, 0)),
                exercise_point("OUTDOOR_BIKE", (2026, 10, 8, 7, 1), (2026, 10, 8, 7, 59)),
            )
        )
        self.assertEqual(text, "サイクリング 60分")

    def test_empty(self):
        self.assertIsNone(fs.exercise_text([]))
        self.assertIsNone(fs.exercise_text(self.workouts(exercise_point("WALKING", (2026, 10, 8, 8, 0), (2026, 10, 8, 8, 0)))))


class RequestTest(unittest.TestCase):
    def test_points_follow_pages_and_encode_filter(self):
        calls = []
        replies = [{"dataPoints": [{"a": 1}], "nextPageToken": "t2"}, {"dataPoints": [{"a": 2}]}]

        def fake(service, what, method, url, headers, data=None, **kwargs):
            calls.append((method, url, headers))
            return replies[len(calls) - 1]

        with mock.patch.object(fs, "request_json", fake):
            found = fs.Health("tok").points("sleep", fs.sleep_filter(date(2026, 10, 5), date(2026, 10, 9)), reconcile=True)
        self.assertEqual(found, [{"a": 1}, {"a": 2}])
        method, url, headers = calls[0]
        self.assertEqual(method, "GET")
        self.assertTrue(url.startswith("https://health.googleapis.com/v4/users/me/dataTypes/sleep/dataPoints:reconcile?"))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(
            query["filter"],
            ['sleep.interval.civil_end_time >= "2026-10-05" AND sleep.interval.civil_end_time < "2026-10-09"'],
        )
        self.assertEqual(query["pageSize"], ["25"])
        self.assertNotIn("+", url)  # 空白は %20 にする
        self.assertEqual(headers["Authorization"], "Bearer tok")
        self.assertIn("pageToken=t2", calls[1][1])

    def test_expired_refresh_token(self):
        def fake(*args, **kwargs):
            raise fs.ApiError("Google", "認証", 400, '{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}')

        with mock.patch.object(fs, "request_json", fake):
            with self.assertRaises(SystemExit) as raised:
                fs.google_access_token("id", "secret", "refresh")
        self.assertIn("GOOGLE_REFRESH_TOKEN", str(raised.exception))

    def test_explain(self):
        def health(code, reason):
            detail = '{"error": {"code": %d, "status": "X", "details": [{"reason": "%s"}]}}' % (code, reason)
            return fs.explain(fs.ApiError("Google Health API", "GET /sleep", code, detail))

        self.assertIn("受け付けを止めている", health(403, "API_PRIVATE_PREVIEW_ACCESS_DENIED"))
        self.assertIn("つながっていない", health(400, "ACCOUNT_NOT_LINKED"))
        self.assertIn("両方にチェック", health(403, "MISSING_OAUTH_SCOPE"))
        self.assertIn("ライブラリ", health(403, "SERVICE_DISABLED"))
        self.assertIn("両方にチェック", health(403, "PERMISSION_DENIED"))
        self.assertIn("GOOGLE_REFRESH_TOKEN", health(401, "UNAUTHENTICATED"))
        self.assertIn("コネクト", fs.explain(fs.ApiError("Notion", "POST /x", 404, "object_not_found")))


# ---------------------------------------------------------------- run() 全体


def text(value):
    return {"type": "rich_text", "rich_text": [{"plain_text": value}] if value else []}


def number(value):
    return {"type": "number", "number": value}


def page(page_id, day, created, **values):
    properties = {"日付": {"type": "date", "date": {"start": day}}, "一言": {"type": "title", "title": []}}
    for name in fs.COLUMNS:
        value = values.get(name)
        properties[name] = text(value) if name in fs.TEXT_COLUMNS else number(value)
    return {"id": page_id, "created_time": created, "properties": properties}


class FakeHealth:
    def __init__(self, sleep=(), exercise=()):
        self.data = {"sleep": list(sleep), "exercise": list(exercise)}
        self.asked = []

    def points(self, data_type, filter_, *, reconcile=False):
        self.asked.append((data_type, filter_, reconcile))
        return self.data[data_type]


class FakeNotion:
    """ページを作る機能は持たせない（作ろうとしたら AttributeError でテストが落ちる）。"""

    def __init__(self, pages):
        self.pages = pages
        self.updated = []

    def daily_pages(self, first, last):
        self.range = (first, last)
        return self.pages

    def update(self, page_id, properties):
        self.updated.append((page_id, properties))


TODAY = date(2026, 10, 8)


def run(*args, **kwargs):
    """fs.run の表示は出さない（Actions のログで本番の出力と紛れないように）。"""
    with contextlib.redirect_stdout(io.StringIO()):
        fs.run(*args, **kwargs)


class RunTest(unittest.TestCase):
    def health(self):
        return FakeHealth(
            sleep=[sleep_point((2026, 10, 7, 23, 41), (2026, 10, 8, 6, 58), 394, main=True)],
            exercise=[exercise_point("TENNIS", (2026, 10, 7, 10, 0), (2026, 10, 7, 11, 32))],
        )

    def test_writes_only_changes(self):
        notion = FakeNotion(
            [
                page("p8", "2026-10-08", "2026-10-07T15:00:58.000Z", 就寝="23:41"),
                page("p7", "2026-10-07", "2026-10-06T15:00:40.000Z", 運動記録="テニス 92分"),
                page("p6", "2026-10-06", "2026-10-05T15:00:56.000Z"),
            ]
        )
        health = self.health()
        run(health, notion, TODAY, 3)
        self.assertEqual(notion.range, (date(2026, 10, 6), TODAY))
        self.assertIn(("sleep", fs.sleep_filter(date(2026, 10, 5), date(2026, 10, 9)), True), health.asked)
        self.assertIn(("exercise", fs.exercise_filter(date(2026, 10, 6), date(2026, 10, 9)), False), health.asked)
        self.assertEqual(len(notion.updated), 1)  # 10/7 は変更なし、10/6 はデータなし
        page_id, properties = notion.updated[0]
        self.assertEqual(page_id, "p8")
        self.assertEqual(
            properties,
            {
                "起床": {"rich_text": [{"type": "text", "text": {"content": "06:58"}}]},
                "睡眠(Fitbit)": {"number": 6.6},
                "覚醒(Fitbit)": {"number": 0.7},
            },
        )

    def test_does_not_clear_existing_values(self):
        notion = FakeNotion([page("p6", "2026-10-06", "2026-10-05T15:00:56.000Z", 運動記録="テニス 60分")])
        run(FakeHealth(), notion, date(2026, 10, 6), 1)
        self.assertEqual(notion.updated, [])

    def test_missing_pages_are_skipped_and_reported(self):
        notion = FakeNotion([page("p8", "2026-10-08", "2026-10-07T15:00:58.000Z")])  # 10/7 のページがない
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            fs.run(self.health(), notion, TODAY, 3)
        self.assertEqual([page_id for page_id, _ in notion.updated], ["p8"])
        self.assertIn("ページがないので飛ばした: 10/07", output.getvalue())
        self.assertNotIn("10/06", output.getvalue().split("飛ばした")[-1])  # データもない日は知らせない

    def test_duplicate_pages_write_to_the_first(self):
        notion = FakeNotion(
            [
                page("later", "2026-10-08", "2026-10-08T03:00:00.000Z"),
                page("first", "2026-10-08", "2026-10-07T15:00:58.000Z"),
            ]
        )
        run(self.health(), notion, TODAY, 1)
        self.assertEqual([page_id for page_id, _ in notion.updated], ["first"])

    def test_dry_run_writes_nothing(self):
        notion = FakeNotion([page("p8", "2026-10-08", "2026-10-07T15:00:58.000Z")])
        run(self.health(), notion, TODAY, 2, dry_run=True)
        self.assertEqual(notion.updated, [])

    def test_renamed_column_stops(self):
        broken = page("p8", "2026-10-08", "2026-10-07T15:00:58.000Z")
        del broken["properties"]["運動記録"]
        with self.assertRaises(SystemExit) as raised:
            run(self.health(), FakeNotion([broken]), TODAY, 1)
        self.assertIn("運動記録", str(raised.exception))

    def test_current_values(self):
        values = fs.current_values(page("p", "2026-10-08", "", 就寝="23:41", **{"覚醒(Fitbit)": 1.5}))
        self.assertEqual(values["就寝"], "23:41")
        self.assertEqual(values["覚醒(Fitbit)"], 1.5)
        self.assertIsNone(values["起床"])
        self.assertEqual(sorted(values), sorted(fs.COLUMNS))  # 歩数・アクティブゾーンは読まない


if __name__ == "__main__":
    unittest.main()
