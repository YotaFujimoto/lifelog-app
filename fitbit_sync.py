#!/usr/bin/env python3
"""Fitbit（Google Health API）の睡眠と運動を、Notion の日次ログに書き込む。

直近 DAYS 日分（既定は3日：今日・昨日・一昨日）について、次の5列を書き直す。
手入力の列（睡眠・0時前に就寝・中途覚醒なし・眠くない・研究など）には触れない。

- 就寝・起床・睡眠(Fitbit)・覚醒(Fitbit): その日の朝に起きた睡眠。夜中に目が覚めて
  記録が分かれたときは、前日21時〜当日12時のあいだで3時間以内の間をおいて続く記録を
  ひと晩としてまとめる（仮眠だけの日は書かない）
  - 睡眠(Fitbit): 眠っていた時間（時間、小数1桁）
  - 覚醒(Fitbit): 就寝から起床までのうち、眠っていなかった時間（時間、小数1桁）
- 運動記録: その日に始めた運動（自動認識を含む）を種類ごとに合計した時間

Fitbit に値がない列は空で上書きしない（前に入れた値を残す）。
日次ログのページは毎日0時に Notion が自動で作るので、そこに書き込む。ページは作らない
（テンプレートの「今日」の日付と重なって、同じ日のページが2つできないように）。
ページがない日は飛ばして知らせる。3日以内にページを作れば、次の回で入る。

使い方:
    NOTION_TOKEN=... GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... GOOGLE_REFRESH_TOKEN=... \\
        python3 fitbit_sync.py              # 直近3日分を書き込む
    ... DAYS=20 python3 fitbit_sync.py      # 20日分（過去分の取り込み）
    ... python3 fitbit_sync.py --dry-run    # 書き込まずに内容を表示する
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DAILY_DATA_SOURCE_ID = os.environ.get("DAILY_DATA_SOURCE_ID", "92ac601c-0894-4ad9-9021-e5e59cdf9f06")
TZ = ZoneInfo("Asia/Tokyo")
NIGHT_GAP = timedelta(hours=3)  # これより短い間で続く睡眠の記録は、ひと晩としてまとめる
NIGHT_FROM, NIGHT_UNTIL = 21, 12  # まとめる記録は、前日21時〜当日12時に収まるものだけ（夕方のうたた寝・昼寝を除く）
MAX_DAYS = 90

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
HEALTH_API = "https://health.googleapis.com/v4/users/me/dataTypes"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# 日次ログの列名
DATE = "日付"
BED, WAKE, SLEEP, AWAKE = "就寝", "起床", "睡眠(Fitbit)", "覚醒(Fitbit)"
EXERCISE = "運動記録"
COLUMNS = (BED, WAKE, SLEEP, AWAKE, EXERCISE)
TEXT_COLUMNS = (BED, WAKE, EXERCISE)

REAUTH = (
    "Google の認証が切れている（または取り消された）。README の「認証をやり直す」の手順で鍵を取り直し、"
    "GitHub のシークレット GOOGLE_REFRESH_TOKEN を更新する"
)

# 運動の種類（Google Health API の exerciseType）。ないものはアプリの表示名を使う
EXERCISE_NAMES = {
    "WALKING": "ウォーキング",
    "POWER_WALKING": "パワーウォーキング",
    "NORDIC_WALKING": "ノルディックウォーキング",
    "WALK_WITH_WEIGHTS": "ウォーキング（重り）",
    "INCLINE_WALK": "坂道ウォーキング",
    "TREADMILL_WALK": "トレッドミル（ウォーキング）",
    "HIKING": "ハイキング",
    "RUNNING": "ランニング",
    "INCLINE_RUN": "坂道ランニング",
    "TRAIL_RUN": "トレイルラン",
    "TREADMILL": "トレッドミル",
    "TRACK_AND_FIELD": "陸上",
    "BIKING": "サイクリング",
    "OUTDOOR_BIKE": "サイクリング",
    "MOUNTAIN_BIKE": "マウンテンバイク",
    "STATIONARY_BIKE": "エアロバイク",
    "SPINNING": "スピンバイク",
    "SWIMMING": "水泳",
    "SWIMMING_POOL": "水泳（プール）",
    "SWIMMING_OPEN_WATER": "水泳（屋外）",
    "TENNIS": "テニス",
    "TABLE_TENNIS": "卓球",
    "BADMINTON": "バドミントン",
    "SQUASH": "スカッシュ",
    "PADEL": "パデル",
    "PICKELBALL": "ピックルボール",
    "RACKET_SPORTS": "ラケットスポーツ",
    "GOLF": "ゴルフ",
    "SOCCER": "サッカー",
    "BASKETBALL": "バスケットボール",
    "BASEBALL": "野球",
    "SOFTBALL": "ソフトボール",
    "VOLLEYBALL": "バレーボール",
    "FOOTBALL_AMERICAN": "アメフト",
    "RUGBY": "ラグビー",
    "HANDBALL": "ハンドボール",
    "SPORT": "スポーツ",
    "WORKOUT": "ワークアウト",
    "STRENGTH_TRAINING": "筋トレ",
    "FUNCTIONAL_STRENGTH_TRAINING": "筋トレ",
    "WEIGHTLIFTING": "ウェイトトレーニング",
    "WEIGHTS": "ウェイトトレーニング",
    "FREE_WEIGHTS": "ウェイトトレーニング",
    "WEIGHT_MACHINES": "マシントレーニング",
    "POWERLIFTING": "パワーリフティング",
    "BODY_WEIGHT": "自重トレーニング",
    "CALISTHENICS": "自重トレーニング",
    "CORE_TRAINING": "体幹トレーニング",
    "CIRCUIT_TRAINING": "サーキットトレーニング",
    "HIIT": "HIIT",
    "INTERVAL_WORKOUT": "インターバルトレーニング",
    "TABATA_WORKOUT": "タバタ",
    "CARDIO_WORKOUT": "有酸素運動",
    "AEROBIC_WORKOUT": "エアロビクス",
    "ELLIPTICAL": "クロストレーナー",
    "ROWING_MACHINE": "ローイングマシン",
    "STAIRCLIMBER": "ステアクライマー",
    "JUMPING_ROPE": "縄跳び",
    "YOGA": "ヨガ",
    "PILATES": "ピラティス",
    "STRETCHING": "ストレッチ",
    "MEDITATE": "瞑想",
    "DANCING": "ダンス",
    "BOXING": "ボクシング",
    "KICKBOXING": "キックボクシング",
    "MARTIAL_ARTS": "武道",
    "CLIMBING": "クライミング",
    "ROCK_CLIMBING": "クライミング",
    "INDOOR_CLIMBING": "クライミング（屋内）",
    "SKIING": "スキー",
    "SNOWBOARDING": "スノーボード",
    "SKATING": "スケート",
    "ICE_SKATING": "スケート",
    "SURFING": "サーフィン",
    "HOUSEHOLD_CHORES": "家事",
    "CLEANING": "掃除",
    "OTHER": "その他",
    "EXERCISE_TYPE_UNSPECIFIED": "運動",
}


# ---------------------------------------------------------------- 通信


class ApiError(Exception):
    def __init__(self, service: str, what: str, code: int, detail: str) -> None:
        super().__init__(f"{service}: {what} → {code}\n{detail}")
        self.service, self.what, self.code, self.detail = service, what, code, detail


def _retry_after(header: str | None, attempt: int) -> float:
    try:
        return float(header)
    except (TypeError, ValueError):
        return float(2**attempt)


def request_json(
    service: str,
    what: str,
    method: str,
    url: str,
    headers: dict[str, str],
    data: bytes | None = None,
    *,
    retry: bool = True,
) -> dict:
    """JSON を返す API を呼ぶ。429（混雑）は常に、5xx と接続エラーは retry=True のとき再試行する。"""
    for attempt in range(6):
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read()
            return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            if attempt < 5 and (error.code == 429 or (retry and error.code >= 500)):
                time.sleep(_retry_after(error.headers.get("Retry-After"), attempt))
                continue
            raise ApiError(service, what, error.code, detail) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt < 5 and retry:
                time.sleep(2**attempt)
                continue
            raise SystemExit(f"{service} に接続できない: {what} → {error}") from None
    raise AssertionError("unreachable")


def google_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """リフレッシュトークンから、1時間有効のアクセストークンをもらう。"""
    data = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        result = request_json("Google", "認証", "POST", TOKEN_URL, headers, data)
    except ApiError as error:
        if "invalid_grant" in error.detail:
            raise SystemExit(REAUTH) from None
        if "invalid_client" in error.detail or "unauthorized_client" in error.detail:
            raise SystemExit(
                "GOOGLE_CLIENT_ID か GOOGLE_CLIENT_SECRET が違う。Google Cloud の「Clients」で作ったものと、"
                "GitHub のシークレットを見比べる"
            ) from None
        raise SystemExit(f"Google の認証に失敗した: {error}") from None
    return result["access_token"]


class Health:
    """Google Health API の最小限のクライアント（読み取りだけ）。"""

    def __init__(self, access_token: str) -> None:
        self.token = access_token

    def call(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> dict:
        url = HEALTH_API + path
        if params:
            url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json", "Accept-Language": "ja"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        return request_json("Google Health API", f"{method} {path}", method, url, headers, data)

    def points(self, data_type: str, filter_: str, *, reconcile: bool = False) -> list[dict]:
        """データ点の一覧。reconcile=True なら、複数の機器の重なる記録を1本にまとめたもの。"""
        path = f"/{data_type}/dataPoints" + (":reconcile" if reconcile else "")
        params = {"filter": filter_, "pageSize": 25}  # 睡眠と運動は25件が上限
        found: list[dict] = []
        while True:
            result = self.call("GET", path, params=params)
            found += result.get("dataPoints", [])
            if not result.get("nextPageToken"):
                return found
            params["pageToken"] = result["nextPageToken"]


class Notion:
    """Notion API の最小限のクライアント（日次ログの読み書きだけ）。"""

    def __init__(self, token: str) -> None:
        self.token = token
        self.last_call = 0.0

    def call(self, method: str, path: str, body: dict | None = None, *, retry: bool = True) -> dict:
        # Notion の上限（平均 3 リクエスト/秒）に収まるよう間隔をあける
        time.sleep(max(0.0, 0.35 - (time.monotonic() - self.last_call)))
        self.last_call = time.monotonic()
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        data = None if body is None else json.dumps(body).encode()
        return request_json("Notion", f"{method} {path}", method, NOTION_API + path, headers, data, retry=retry)

    def daily_pages(self, first: date, last: date) -> list[dict]:
        body: dict = {
            "filter": {
                "and": [
                    {"property": DATE, "date": {"on_or_after": first.isoformat()}},
                    {"property": DATE, "date": {"on_or_before": last.isoformat()}},
                ]
            },
            "page_size": 100,
        }
        pages: list[dict] = []
        while True:
            result = self.call("POST", f"/data_sources/{DAILY_DATA_SOURCE_ID}/query", body)
            pages += result["results"]
            if not result.get("has_more"):
                return pages
            body["start_cursor"] = result["next_cursor"]

    def update(self, page_id: str, properties: dict) -> None:
        self.call("PATCH", f"/pages/{page_id}", {"properties": properties})


# ---------------------------------------------------------------- 時刻


DURATION = re.compile(r"^(-?\d+(?:\.\d+)?)s$")
FRACTION = re.compile(r"\.(\d+)")


def seconds(duration: str | None) -> float | None:
    """"900s" や "3.5s" の形の長さを秒にする。"""
    found = DURATION.match(duration or "")
    return float(found[1]) if found else None


def parse_time(value: str) -> datetime:
    """RFC 3339 の時刻（"2026-10-07T14:32:00.123Z" など）。"""
    value = value.replace("Z", "+00:00")
    value = FRACTION.sub(lambda m: "." + (m[1] + "000000")[:6], value, count=1)
    return datetime.fromisoformat(value)


def civil_date(value: dict | None) -> date | None:
    parts = (value or {}).get("date")
    if not parts:
        return None
    return date(parts["year"], parts["month"], parts["day"])


@dataclass(frozen=True)
class Moment:
    """ある瞬間。physical は世界共通の時刻、local はその人がいた土地の時刻（タイムゾーンなし）。"""

    physical: datetime
    local: datetime


def moment(interval: dict, edge: str) -> Moment:
    """interval の startTime / endTime（edge は "start" か "end"）。"""
    physical = parse_time(interval[f"{edge}Time"])
    civil = interval.get(f"civil{edge.capitalize()}Time")
    day = civil_date(civil)
    if day:
        clock = civil.get("time") or {}  # 0の項目は省かれる
        local = datetime(day.year, day.month, day.day, clock.get("hours", 0), clock.get("minutes", 0), clock.get("seconds", 0))
    else:
        offset = seconds(interval.get(f"{edge}UtcOffset"))
        zone = timezone(timedelta(seconds=offset)) if offset is not None else TZ
        local = physical.astimezone(zone).replace(tzinfo=None)
    return Moment(physical, local)


# ---------------------------------------------------------------- 睡眠


ASLEEP_STAGES = {"LIGHT", "DEEP", "REM", "ASLEEP"}


@dataclass
class Sleep:
    """睡眠の記録1件。"""

    start: Moment
    end: Moment
    asleep: int | None  # 眠っていた分数。処理中で分からなければ None
    main: bool  # その日の主な睡眠
    nap: bool  # 仮眠

    @property
    def minutes(self) -> float:
        return (self.end.physical - self.start.physical).total_seconds() / 60


def parse_sleep(point: dict) -> Sleep | None:
    sleep = point.get("sleep")
    if not sleep or "interval" not in sleep:
        return None
    try:
        start, end = moment(sleep["interval"], "start"), moment(sleep["interval"], "end")
    except (KeyError, ValueError, TypeError):
        return None
    summary = sleep.get("summary")
    asleep: int | None = None
    if summary:
        asleep = int(summary.get("minutesAsleep", 0))
    elif sleep.get("stages"):
        total = 0.0
        for stage in sleep["stages"]:
            if stage.get("type") in ASLEEP_STAGES:
                total += (parse_time(stage["endTime"]) - parse_time(stage["startTime"])).total_seconds() / 60
        asleep = round(total)
    metadata = sleep.get("metadata") or {}
    return Sleep(start, end, asleep, bool(metadata.get("mainSleep")), bool(metadata.get("nap")))


@dataclass
class Night:
    """ひと晩の睡眠（途中で目が覚めて記録が分かれた場合は、その全部）。"""

    sessions: list[Sleep]

    @property
    def bed(self) -> str:
        return f"{self.sessions[0].start.local:%H:%M}"

    @property
    def wake(self) -> str:
        return f"{self.sessions[-1].end.local:%H:%M}"

    @property
    def asleep(self) -> int | None:
        if any(s.asleep is None for s in self.sessions):
            return None
        return sum(s.asleep for s in self.sessions)

    @property
    def awake(self) -> int | None:
        if self.asleep is None:
            return None
        span = (self.sessions[-1].end.physical - self.sessions[0].start.physical).total_seconds() / 60
        return max(0, round(span) - self.asleep)


def overlaps(a: Sleep, b: Sleep) -> bool:
    return a.start.physical < b.end.physical and b.start.physical < a.end.physical


def nights(sessions: list[Sleep], days: list[date]) -> dict[date, Night]:
    """日付ごとの「その日の朝に起きた睡眠」。

    その日に終わった記録のうち、仮眠でない主な睡眠（なければ仮眠でない一番長いもの）を芯にする。
    仮眠しかない日は書かない。芯の前後に NIGHT_GAP 以内の間で続く記録を、近いものから1つずつ足す。
    足すのは前日 NIGHT_FROM 時〜当日 NIGHT_UNTIL 時に収まり、すでに入れた記録と重ならないものだけ
    （同じ睡眠が別の機器からも届いたときに二重に数えないように）。
    新しい日から順に決める（前日の夜に寝始めた分を、その翌朝の睡眠に入れるため）。
    """
    sessions = sorted(sessions, key=lambda s: s.start.physical)
    used: set[int] = set()
    result: dict[date, Night] = {}
    for day in sorted(days, reverse=True):
        candidates = [i for i, s in enumerate(sessions) if i not in used and s.end.local.date() == day]
        pool = [i for i in candidates if not sessions[i].nap]
        if not pool:
            continue
        pool = [i for i in pool if sessions[i].main] or pool
        members = [max(pool, key=lambda i: sessions[i].minutes)]
        earliest = datetime.combine(day - timedelta(days=1), datetime.min.time()).replace(hour=NIGHT_FROM)
        latest = datetime.combine(day, datetime.min.time()).replace(hour=NIGHT_UNTIL)
        while True:
            first = min(sessions[i].start.physical for i in members)
            last = max(sessions[i].end.physical for i in members)
            best, best_gap = None, None
            for i, s in enumerate(sessions):
                if i in used or i in members or s.start.local < earliest or s.end.local > latest:
                    continue
                if any(overlaps(s, sessions[m]) for m in members):
                    continue
                if s.end.physical <= first:
                    gap = first - s.end.physical
                elif s.start.physical >= last:
                    gap = s.start.physical - last
                else:
                    continue  # 記録と記録のあいだにはさまるもの（ふつうはない）
                if gap <= NIGHT_GAP and (best_gap is None or gap < best_gap):
                    best, best_gap = i, gap
            if best is None:
                break
            members.append(best)
        used.update(members)
        result[day] = Night(sorted((sessions[i] for i in members), key=lambda s: s.start.physical))
    return result


# ---------------------------------------------------------------- 運動


@dataclass
class Workout:
    """運動の記録1件。"""

    kind: str
    name: str
    start: Moment
    end: Moment
    minutes: float


def parse_exercise(point: dict) -> Workout | None:
    exercise = point.get("exercise")
    if not exercise or "interval" not in exercise:
        return None
    try:
        start, end = moment(exercise["interval"], "start"), moment(exercise["interval"], "end")
    except (KeyError, ValueError, TypeError):
        return None
    active = seconds(exercise.get("activeDuration"))  # 一時停止を除いた時間
    minutes = active / 60 if active else (end.physical - start.physical).total_seconds() / 60
    kind = exercise.get("exerciseType") or ""
    shown = (exercise.get("displayName") or "").strip()
    name = (shown if kind == "OTHER" else "") or EXERCISE_NAMES.get(kind) or shown or kind or "運動"
    return Workout(kind, name, start, end, minutes)


def overlap(a: Workout, b: Workout) -> float:
    """2つの記録が重なっている割合（短いほうの長さに対して）。"""
    shared = (min(a.end.physical, b.end.physical) - max(a.start.physical, b.start.physical)).total_seconds()
    shorter = min((a.end.physical - a.start.physical).total_seconds(), (b.end.physical - b.start.physical).total_seconds())
    return shared / shorter if shared > 0 and shorter > 0 else 0.0


def exercise_text(workouts: list[Workout]) -> str | None:
    """「テニス 92分、ウォーキング 21分」。同じ運動が別の機器から重ねて届いたときは長いほうだけ数える
    （BIKING と OUTDOOR_BIKE のように種類の名前が違っても、表示名が同じなら同じ運動とみる）。"""
    kept: list[Workout] = []
    for w in sorted(workouts, key=lambda w: -w.minutes):
        if not any(k.name == w.name and overlap(k, w) > 0.5 for k in kept):
            kept.append(w)
    totals: dict[str, float] = {}
    for w in sorted(kept, key=lambda w: w.start.physical):
        totals[w.name] = totals.get(w.name, 0.0) + w.minutes
    parts = [f"{name} {round(minutes)}分" for name, minutes in totals.items() if round(minutes) >= 1]
    return "、".join(parts) or None


# ---------------------------------------------------------------- Notion の列


def day_values(night: Night | None, exercise: str | None) -> dict:
    """書き込む値（Fitbit に値があるものだけ）。"""
    values: dict = {}
    if night:
        values[BED], values[WAKE] = night.bed, night.wake
        if night.asleep is not None:
            values[SLEEP] = round(night.asleep / 60, 1)
            values[AWAKE] = round(night.awake / 60, 1)
    if exercise:
        values[EXERCISE] = exercise
    return values


def encode(values: dict) -> dict:
    properties = {}
    for name, value in values.items():
        if name in TEXT_COLUMNS:
            properties[name] = {"rich_text": [{"type": "text", "text": {"content": str(value)}}]}
        else:
            properties[name] = {"number": value}
    return properties


def missing_columns(page: dict) -> list[str]:
    return [name for name in COLUMNS if name not in page.get("properties", {})]


def current_values(page: dict) -> dict:
    values: dict = {}
    for name in COLUMNS:
        prop = page.get("properties", {}).get(name) or {}
        if prop.get("type") == "rich_text":
            values[name] = "".join(part.get("plain_text", "") for part in prop["rich_text"]) or None
        elif prop.get("type") == "number":
            values[name] = prop["number"]
    return values


def page_day(page: dict) -> date | None:
    start = ((page.get("properties", {}).get(DATE) or {}).get("date") or {}).get("start")
    return date.fromisoformat(start[:10]) if start else None


# ---------------------------------------------------------------- 実行


def sleep_filter(first: date, end: date) -> str:
    """起きた日（その土地の日付）が first 以上 end 未満の睡眠。"""
    return f'sleep.interval.civil_end_time >= "{first.isoformat()}" AND sleep.interval.civil_end_time < "{end.isoformat()}"'


def exercise_filter(first: date, end: date) -> str:
    """始めた日（その土地の日付）が first 以上 end 未満の運動。"""
    return (
        f'exercise.interval.civil_start_time >= "{first.isoformat()}" '
        f'AND exercise.interval.civil_start_time < "{end.isoformat()}"'
    )


def run(health: Health, notion: Notion, today: date, days: int, *, dry_run: bool = False) -> None:
    window = [today - timedelta(days=n) for n in reversed(range(days))]
    first, last = window[0], window[-1]
    one = timedelta(days=1)

    # 前日に終わった記録も、ひと晩の前半としてまとめられるよう1日前から読む
    sleeps = [s for s in map(parse_sleep, health.points("sleep", sleep_filter(first - one, last + one), reconcile=True)) if s]
    workouts = [w for w in map(parse_exercise, health.points("exercise", exercise_filter(first, last + one))) if w]
    print(f"Fitbit（{first:%m/%d}〜{last:%m/%d}）: 睡眠 {len(sleeps)} 件、運動 {len(workouts)} 件を読み取った")
    by_night = nights(sleeps, window)
    by_day: dict[date, list[Workout]] = {}
    for w in workouts:
        by_day.setdefault(w.start.local.date(), []).append(w)

    pages: dict[date, list[dict]] = {}
    for page in notion.daily_pages(first, last):
        day = page_day(page)
        if day:
            pages.setdefault(day, []).append(page)

    skipped = []
    for day in window:
        label = f"{day:%m/%d}"
        values = day_values(by_night.get(day), exercise_text(by_day.get(day, [])))
        same_day = sorted(pages.get(day, []), key=lambda p: p.get("created_time", ""))
        if not same_day:
            if values:
                skipped.append(label)
            continue
        if len(same_day) > 1:
            print(f"注意: {label} のページが {len(same_day)} つある。最初に作られたページに書き込む")
        page = same_day[0]
        missing = missing_columns(page)
        if missing:
            raise SystemExit(
                f"日次ログに列「{'」「'.join(missing)}」がない。列の名前を変えたときは、"
                "fitbit_sync.py の「日次ログの列名」も同じ名前に変える"
            )
        current = current_values(page)
        changed = {name: value for name, value in values.items() if current.get(name) != value}
        if not values:
            print(f"{label}: Fitbit のデータがまだない")
        elif not changed:
            print(f"{label}: 変更なし")
        elif dry_run:
            print(f"{label}: 書き込む内容 {changed}")
        else:
            notion.update(page["id"], encode(changed))
            print(f"{label}: 更新した（{'・'.join(changed)}）")
    if skipped:
        print(
            f"Fitbit のデータはあるが、日次ログにページがないので飛ばした: {', '.join(skipped)}"
            "（その日のページを作れば、3日以内なら次の回で入る。もっと前なら日数を増やして手動で実行する）"
        )


def explain(error: ApiError) -> str:
    """よくある失敗に、直し方を添える（Google Health API はエラーの理由を文字列で返す）。"""
    hint = ""
    if error.service == "Google Health API":
        reasons = [
            ("API_PRIVATE_PREVIEW_ACCESS_DENIED", "Google Health API は、今はこのプロジェクトでは使えない（Google が新しいプロジェクトの受け付けを止めている）。鍵を取り直しても直らない"),
            ("ACCOUNT_NOT_LINKED", "鍵を取った Google アカウントが Fitbit（Google Health アプリ）とつながっていない。アプリで使っているアカウントで鍵を取り直す"),
            ("MISSING_OAUTH_SCOPE", "許可が足りない。鍵を取るときに睡眠と運動の両方にチェックを入れて、鍵を取り直す"),
            ("SERVICE_DISABLED", "Google Cloud で Google Health API が有効になっていない（「API とサービス」→「ライブラリ」で有効にする）"),
            ("accessNotConfigured", "Google Cloud で Google Health API が有効になっていない（「API とサービス」→「ライブラリ」で有効にする）"),
        ]
        hint = next((text for reason, text in reasons if reason in error.detail), "")
        if not hint and error.code == 401:
            hint = REAUTH
        elif not hint and error.code == 403:
            hint = (
                "Google Health API を使う権限がない。Google Cloud で Google Health API が有効か、"
                "鍵を取るときに睡眠と運動の両方にチェックを入れたかを確かめる"
            )
    elif error.service == "Notion":
        if error.code == 401:
            hint = "NOTION_TOKEN が違うか、使えなくなっている"
        elif error.code in (403, 404):
            hint = "日次ログにアクセスできない。Notion のコネクトが「ライフログ」ページにつながっているか確かめる"
    return f"{hint}\n\n{error}" if hint else str(error)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fitbit（Google Health API）の睡眠と運動を Notion の日次ログに書き込む")
    parser.add_argument("--days", help="何日分を書き直すか（既定は環境変数 DAYS、なければ 3）")
    parser.add_argument("--dry-run", action="store_true", help="書き込まずに内容を表示する")
    args = parser.parse_args()
    raw = args.days or os.environ.get("DAYS") or "3"
    try:
        days = int(raw)
    except ValueError:
        sys.exit(f"日数は数字で指定する（例: 14）。指定された値: {raw}")
    if not 1 <= days <= MAX_DAYS:
        sys.exit(f"日数は 1〜{MAX_DAYS}")
    names = ("NOTION_TOKEN", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        sys.exit(f"環境変数が設定されていない: {', '.join(missing)}（GitHub のシークレットを確かめる）")
    token = google_access_token(os.environ["GOOGLE_CLIENT_ID"], os.environ["GOOGLE_CLIENT_SECRET"], os.environ["GOOGLE_REFRESH_TOKEN"])
    try:
        run(Health(token), Notion(os.environ["NOTION_TOKEN"]), datetime.now(TZ).date(), days, dry_run=args.dry_run)
    except ApiError as error:
        sys.exit(explain(error))


if __name__ == "__main__":
    main()
