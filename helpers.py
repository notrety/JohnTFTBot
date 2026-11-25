import asyncio
import discord
import requests
import dicts
import time
from datetime import datetime, timedelta, time as dt_time
import pytz
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from pulsefire.clients import RiotAPIClient
from pulsefire.taskgroups import TaskGroup
from pulsefire.middlewares import json_response_middleware, http_error_middleware, rate_limiter_middleware
from pulsefire.ratelimiters import RiotAPIRateLimiter
import os
import re
import hashlib

CACHE_DIR = "image_cache"
current_tft_unix = 1787742000 
current_lol_unix = 1767873600

os.makedirs(CACHE_DIR, exist_ok=True)

_inflight: dict[str, asyncio.Task] = {}

def _cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.sha256(url.encode()).hexdigest() + ".img")

def _load_sync(url: str) -> Image.Image:
    """Blocking; runs in a worker thread."""
    path = _cache_path(url)

    if os.path.exists(path):
        try:
            with Image.open(path) as im:
                return im.convert("RGBA")
        except Exception:
            print(f"Corrupt cache entry for {url}, refetching")
            try:
                os.remove(path)
            except OSError:
                pass

    resp = requests.get(url, timeout=(3, 7))  # (connect, read)
    resp.raise_for_status()

    # Decode BEFORE caching, so garbage never reaches the cache
    with Image.open(BytesIO(resp.content)) as im:
        img = im.convert("RGBA")

    with open(path, "wb") as f:
        f.write(resp.content)

    print(f"Fetched and cached {url}")
    return img

async def fetch_image(url: str, size: tuple | None = None) -> Image.Image:
    # De-duplicate concurrent requests for the same URL
    task = _inflight.get(url)
    if task is None:
        task = asyncio.create_task(asyncio.to_thread(_load_sync, url))
        _inflight[url] = task
        task.add_done_callback(lambda _: _inflight.pop(url, None))

    base = await asyncio.shield(task)  # one caller being cancelled won't kill the others

    return base.resize(size, Image.LANCZOS) if size else base.copy()

# ---------------------------------------------------------------------------
# Riot API clients: background traffic is paced so commands keep priority
# ---------------------------------------------------------------------------

# Requests/second the background DB updater may use. Personal/dev keys allow
# 100 requests / 2 min (~0.83 rps), so 0.4 leaves about half for commands.
BACKGROUND_RPS = float(os.getenv("BACKGROUND_RPS", "0.4"))
# How many users the background updater processes at once.
BACKGROUND_CONCURRENCY = int(os.getenv("BACKGROUND_CONCURRENCY", "2"))

# One limiter shared by every client so Riot's real limits are respected globally.
_SHARED_LIMITER = RiotAPIRateLimiter()


class _Pacer:
    """Spaces out awaiting callers so they run at most `rps` times per second."""

    def __init__(self, rps: float):
        self.interval = 1.0 / rps if rps > 0 else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self):
        if self.interval <= 0:
            return
        async with self._lock:
            delay = self._next - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = time.monotonic() + self.interval


_background_pacer = _Pacer(BACKGROUND_RPS)


def _pacer_middleware(pacer: _Pacer):
    def constructor(next):
        async def middleware(invocation):
            await pacer.wait()
            return await next(invocation)
        return middleware
    return constructor


def riot_client(token, background=False):
    """Riot API client. Background clients are throttled by the pacer; command clients are not."""
    middlewares = [json_response_middleware(), http_error_middleware()]
    if background:
        middlewares.append(_pacer_middleware(_background_pacer))
    middlewares.append(rate_limiter_middleware(_SHARED_LIMITER))
    return RiotAPIClient(default_headers={"X-Riot-Token": token}, middlewares=middlewares)


# Per-user coordination between the background updater and commands
_user_locks: dict[int, asyncio.Lock] = {}
_last_updated: dict[int, float] = {}
# Users whose full-season backfill is running; other updates skip them meanwhile
_backfilling: set[int] = set()
# Keep references to backfill tasks so they aren't garbage collected
_backfill_tasks: set[asyncio.Task] = set()


def _only_newest_gets_elo(rows):
    """On a first sync only the newest game's elo is known (the current rank); clear the rest.

    Rows are newest first and elo is the last column.
    """
    return rows[:1] + [row[:-1] + (None,) for row in rows[1:]]


async def update_db_games(pool, tft_token, lol_token):
    async with pool.acquire() as conn:
        users = await conn.fetch('SELECT discord_id FROM users;')

    async with TaskGroup(asyncio.Semaphore(BACKGROUND_CONCURRENCY)) as tg:
        for user in users:
            await tg.create_task(update_user_games(pool, int(user['discord_id']), tft_token, lol_token, background=True))

    print("User games updated.")


_LP_UPSERT = """
    INSERT INTO lp (match_id, puuid, game, update_time, elo)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (puuid)
    DO UPDATE SET
        match_id = EXCLUDED.match_id,
        game = EXCLUDED.game,
        update_time = EXCLUDED.update_time,
        elo = EXCLUDED.elo
    WHERE EXCLUDED.match_id > lp.match_id;
"""

_LAST_LP_QUERY = '''
    SELECT match_id, elo, update_time
    FROM lp
    WHERE puuid = $1 AND game = $2
    ORDER BY update_time DESC
    LIMIT 1
'''


async def update_user_games(pool, user_id, tft_token, lol_token, background=False, max_age=0, _backfill=False):
    """Fetch new ranked games for a user and store them.

    background: pace Riot calls so commands keep priority (used by the scheduler).
    max_age: skip the update if this user was updated less than max_age seconds ago.

    If a game has no lp row yet (first sync, e.g. a newly linked account), every
    ranked game since current_tft_unix / current_lol_unix is fetched, and only the
    newest one gets the current elo.
    """
    user_id = int(user_id)
    if user_id in _backfilling and not _backfill:
        print(f"[SKIP] {user_id} is being backfilled")
        return
    lock = _user_locks.setdefault(user_id, asyncio.Lock())

    async with lock:
        if max_age and time.time() - _last_updated.get(user_id, 0) < max_age:
            print(f"[SKIP] {user_id} updated less than {max_age}s ago")
            return

        try:
            # (a) Read state with a short-lived connection
            async with pool.acquire() as conn:
                user = await conn.fetchrow('SELECT * FROM users WHERE discord_id = $1', user_id)
                if not user:
                    print(f"[WARN] No user found with discord_id={user_id}")
                    return
                last_tft_match = await conn.fetchrow(_LAST_LP_QUERY, user['tft_puuid'], 'TFT')
                last_lol_match = await conn.fetchrow(_LAST_LP_QUERY, user['league_puuid'], 'League')

            region = user['region']
            mass_region = user['mass_region']

            # (b) Riot API calls, no DB connection held
            last_tft_time = last_tft_match['update_time'] if last_tft_match else current_tft_unix
            _, tft_match_list = await find_all_match_ids(
                user['tft_puuid'], "TFT", mass_region, tft_token, max(last_tft_time, current_tft_unix), background
            )
            tft_rows, tft_elo = [], None
            if tft_match_list:
                tft_elo = (await calculate_elo(user['tft_puuid'], "TFT", tft_token, region, background))[0]
                tft_rows = await fetch_match_rows(user['tft_puuid'], "TFT", mass_region, tft_token, tft_match_list, tft_elo, background)
                if not last_tft_match:
                    tft_rows = _only_newest_gets_elo(tft_rows)

            last_lol_time = last_lol_match['update_time'] if last_lol_match else current_lol_unix
            _, lol_match_list = await find_all_match_ids(
                user['league_puuid'], "League", mass_region, lol_token, last_lol_time, background
            )
            lol_rows, lol_elo = [], None
            if lol_match_list:
                lol_elo = (await calculate_elo(user['league_puuid'], "League", lol_token, region, background))[0]
                lol_rows = await fetch_match_rows(user['league_puuid'], "League", mass_region, lol_token, lol_match_list, lol_elo, background)
                if not last_lol_match:
                    lol_rows = _only_newest_gets_elo(lol_rows)

            # (c) Write results with a short-lived connection
            if tft_match_list or lol_match_list:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        if tft_match_list:
                            await insert_match_rows(conn, "TFT", tft_rows)
                            await conn.execute(_LP_UPSERT, tft_match_list[0], user['tft_puuid'], 'TFT', int(time.time()), tft_elo)
                        if lol_match_list:
                            await insert_match_rows(conn, "League", lol_rows)
                            await conn.execute(_LP_UPSERT, lol_match_list[0], user['league_puuid'], 'League', int(time.time()), lol_elo)

            _last_updated[user_id] = time.time()
            print(f"[INFO] Updated matches for {user['game_name']}")

        except Exception as e:
            print(f"[ERROR] update_user_games failed for {user_id}: {e}")

        finally:
            print(f"[DONE] Completed update for {user_id}")


async def backfill_user_games(pool, user_id, tft_token, lol_token):
    """Fetch a newly linked user's ranked games since current_tft_unix / current_lol_unix.

    Runs paced like the background updater. Commands and the scheduler skip this
    user until it finishes, so nobody waits on it.
    """
    user_id = int(user_id)
    _backfilling.add(user_id)
    start = time.monotonic()
    try:
        await update_user_games(pool, user_id, tft_token, lol_token, background=True, _backfill=True)
        print(f"[BACKFILL] Finished for {user_id} in {time.monotonic() - start:.0f}s")
    finally:
        _backfilling.discard(user_id)


def start_backfill(pool, user_id, tft_token, lol_token):
    """Start a backfill in the background without waiting for it."""
    task = asyncio.create_task(backfill_user_games(pool, user_id, tft_token, lol_token))
    _backfill_tasks.add(task)
    task.add_done_callback(_backfill_tasks.discard)
    return task


async def fetch_match_rows(puuid, game, mass_region, token, match_list, elo=None, background=False):
    """Fetch match details from Riot and return insertable rows (ranked, current set only)."""
    rows = []
    if not match_list:
        return rows

    target_queues = {
        "TFT": dicts.game_type_to_id["Ranked"],
        "League": dicts.game_type_to_id["Ranked Solo/Duo"]
    }
    async with riot_client(token, background) as client:
        for match_id in match_list:
            try:
                print(f"Fetching {game} match id {match_id} for {puuid}")
                if game == 'TFT':
                    match_info = await client.get_tft_match_v1_match(region=mass_region, id=match_id)
                    if match_info['info'].get('queue_id') != target_queues['TFT']:
                        print(f"{game} match id {match_id} not ranked")
                        continue

                    game_datetime = match_info['info']['game_datetime']
                    if game_datetime < current_tft_unix * 1000:
                        print(f"{game} match id {match_id} before current set, skipping")
                        continue

                    p = next((p for p in match_info['info']['participants'] if p['puuid'] == puuid), None)
                    if p:
                        units = p.get('units', [])
                        champions = [c['character_id'] for c in units]
                        items = [i for c in units for i in c.get('itemNames', [])]
                        traits = [{"name": t["name"], "style": t["style"]} for t in p.get("traits", [])]
                        rows.append((
                            match_id, puuid, game_datetime, p['placement'], champions, items, traits,
                            p['total_damage_to_players'], p['level'], elo
                        ))

                elif game == 'League':
                    match_info = await client.get_lol_match_v5_match(region=mass_region, id=match_id)
                    if match_info['info'].get('queueId') != target_queues['League']:
                        print(f"{game} match id {match_id} not ranked")
                        continue

                    game_datetime = match_info['info']['gameEndTimestamp']
                    if game_datetime < current_lol_unix * 1000:
                        print(f"{game} match id {match_id} before current set, skipping")
                        continue

                    p = next((p for p in match_info['info']['participants'] if p['puuid'] == puuid), None)
                    if p:
                        cs = p['totalMinionsKilled'] + p['neutralMinionsKilled']
                        rows.append((
                            match_id, puuid, game_datetime, p['win'], p['championName'], p['kills'],
                            p['deaths'], p['assists'], cs, match_info['info']['gameDuration'], elo
                        ))
            except Exception as e:
                print(f"[ERROR] Failed to fetch {game} match {match_id}: {e}")
    return rows


async def insert_match_rows(conn, game, rows):
    if not rows:
        return
    if game == 'TFT':
        await conn.executemany('''
            INSERT INTO tft_games (match_id, tft_puuid, game_datetime, placement, champions, items, traits, damage_dealt, level, elo)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (match_id, tft_puuid) DO NOTHING;
        ''', rows)
    else:
        await conn.executemany('''
            INSERT INTO league_games (match_id, league_puuid, game_datetime, win_loss, champion, kills, deaths, assists, cs, game_duration, elo)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (match_id, league_puuid) DO NOTHING;
        ''', rows)
    print(f"Inserted {len(rows)} {game} match(es)")


async def get_match_lp_delta(pool, game, puuid, match_id):
    """LP change from a stored ranked game: its elo minus the player's previous game's elo.

    Returns None when it can't be known reliably: the game isn't stored, it or the
    previous game has no elo, or it shares its elo with a neighbouring game (several
    games found in one update get the same elo, so the split between them is unknown).
    """
    table, puuid_col = ("tft_games", "tft_puuid") if game == "TFT" else ("league_games", "league_puuid")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f'''
            SELECT elo, prev_elo, next_elo
            FROM (
                SELECT match_id, elo,
                       LAG(elo) OVER w AS prev_elo,
                       LEAD(elo) OVER w AS next_elo
                FROM {table}
                WHERE {puuid_col} = $1
                WINDOW w AS (ORDER BY game_datetime)
            ) t
            WHERE match_id = $2;
        ''', puuid, match_id)

    if not row or row['elo'] is None or row['prev_elo'] is None:
        return None
    if row['elo'] == row['prev_elo'] or row['elo'] == row['next_elo']:
        return None
    return row['elo'] - row['prev_elo']


def format_lp_delta(delta):
    return f"LP: +{delta}" if delta >= 0 else f"LP: {delta}"


async def add_new_match(conn, puuid, game, mass_region, token, match_list, elo=None):
    """Fetch and insert in one step (used by find_missing_games)."""
    rows = await fetch_match_rows(puuid, game, mass_region, token, match_list, elo)
    await insert_match_rows(conn, game, rows)

async def find_missing_games(pool, tft_token, lol_token):
    async with pool.acquire() as conn:

        rows = await conn.fetch("""
        WITH last_match AS (
            SELECT DISTINCT ON (puuid)
                puuid,
                game,
                update_time
            FROM lp
            ORDER BY puuid, game, update_time DESC
        )
        SELECT
            u.discord_id,
            u.game_name,
            u.tag_line,
            u.tft_puuid,
            u.league_puuid,
            u.region,
            u.mass_region,
            tft.update_time AS last_tft_time,
            lol.update_time AS last_lol_time
        FROM users u
        LEFT JOIN last_match tft
            ON tft.puuid = u.tft_puuid AND tft.game = 'TFT'
        LEFT JOIN last_match lol
            ON lol.puuid = u.league_puuid AND lol.game = 'League';
        """)

        for row in rows:
            err, tft_match_ids = await find_all_match_ids(row['tft_puuid'], "TFT", row['mass_region'], tft_token, timestamp=current_tft_unix)

            if tft_match_ids:
                tft_elo, _, _, _ = await calculate_elo(row['tft_puuid'], "TFT", tft_token, row['region'])
                await add_new_match(conn, row['tft_puuid'], 'TFT', row['mass_region'], tft_token, tft_match_ids, tft_elo)

                last_match_id = tft_match_ids[0]
                await conn.execute("""
                    INSERT INTO lp (match_id, puuid, game, update_time, elo)
                    VALUES ($1,$2,'TFT',$3,$4)
                    ON CONFLICT (puuid)
                    DO UPDATE SET
                        match_id = EXCLUDED.match_id,
                        game = EXCLUDED.game,
                        update_time = EXCLUDED.update_time,
                        elo = EXCLUDED.elo
                    WHERE EXCLUDED.match_id > lp.match_id;
                """,
                last_match_id,row['tft_puuid'],int(time.time()),tft_elo)

            # ---------- League ----------
            err, lol_match_ids = await find_all_match_ids(row['league_puuid'], "League", row['mass_region'], lol_token, timestamp=current_lol_unix)

            if lol_match_ids:

                last_lol_match_id = lol_match_ids[0]

                lol_elo, _, _, _ = await calculate_elo(row['league_puuid'], "League", lol_token, row['region'])
                await add_new_match(conn, row['league_puuid'], 'League', row['mass_region'], lol_token, lol_match_ids, lol_elo)
                await conn.execute("""
                    INSERT INTO lp (match_id, puuid, game, update_time, elo)
                    VALUES ($1, $2, 'League', $3, $4)
                    ON CONFLICT (puuid)
                    DO UPDATE SET
                        match_id = EXCLUDED.match_id,
                        game = EXCLUDED.game,
                        update_time = EXCLUDED.update_time,
                        elo = EXCLUDED.elo
                    WHERE EXCLUDED.match_id > lp.match_id;
                """, last_lol_match_id, row['league_puuid'], int(time.time()), lol_elo)

            print(f"[INFO] Processed {row['game_name']} ({row['discord_id']})")
    print("All missing matches found!")

async def get_rank_info(region, puuid, tft_token, background=False):
    async with riot_client(tft_token, background) as client:
        info = await client.get_tft_league_v1_entries_by_puuid(region=region, puuid=puuid)
        return info

async def get_lol_rank_info(region, puuid, lol_token, background=False):
    async with riot_client(lol_token, background) as client:
        info = await client.get_lol_league_v4_entries_by_puuid(region=region, puuid=puuid)
        return info

# ---------------------------------------------------------------------------
# Challenger / Grandmaster LP cutoffs (stored daily in the rank_cutoffs table)
# ---------------------------------------------------------------------------

APEX_ELO = 2800  # elo at Master 0 LP (see dicts.rank_to_elo)

# Spots per tier and minimum LP per region. Riot changes these occasionally, so
# check the current values for your regions if cutoffs look off.
CUTOFF_RULES = {
    "TFT":    {"chall_spots": 250, "gm_spots": 500, "chall_min": 500, "gm_min": 200},
    "League": {"chall_spots": 300, "gm_spots": 700, "chall_min": 500, "gm_min": 200},
}

# Latest known cutoffs: {(game, region): (challenger_lp, grandmaster_lp)}
_rank_cutoffs: dict[tuple[str, str], tuple[int, int]] = {}


def _compute_cutoffs(lps, rules):
    """Return (challenger_lp, grandmaster_lp) from every Master+ player's LP."""
    lps = sorted(lps, reverse=True)
    chall_idx = rules["chall_spots"] - 1
    gm_idx = rules["chall_spots"] + rules["gm_spots"] - 1
    chall = max(rules["chall_min"], lps[chall_idx]) if len(lps) > chall_idx else rules["chall_min"]
    gm = max(rules["gm_min"], lps[gm_idx]) if len(lps) > gm_idx else rules["gm_min"]
    return chall, gm


async def fetch_cutoffs(game, region, token, background=False):
    """Fetch the Master, Grandmaster and Challenger ladders from Riot and compute the cutoffs."""
    async with riot_client(token, background) as client:
        if game == "TFT":
            leagues = await asyncio.gather(
                client.get_tft_league_v1_challenger_league(region=region),
                client.get_tft_league_v1_grandmaster_league(region=region),
                client.get_tft_league_v1_master_league(region=region),
            )
        else:
            queue = "RANKED_SOLO_5x5"
            leagues = await asyncio.gather(
                client.get_lol_league_v4_challenger_league_by_queue(region=region, queue=queue),
                client.get_lol_league_v4_grandmaster_league_by_queue(region=region, queue=queue),
                client.get_lol_league_v4_master_league_by_queue(region=region, queue=queue),
            )
    lps = [entry.get('leaguePoints', 0) for league in leagues for entry in league.get('entries', [])]
    return _compute_cutoffs(lps, CUTOFF_RULES[game])


# Kept for existing callers: TFT cutoffs, fetched live
async def get_cutoff(tft_token, region):
    return await fetch_cutoffs("TFT", region, tft_token)


async def ensure_cutoff_table(pool):
    async with pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS rank_cutoffs (
                game TEXT NOT NULL,
                region TEXT NOT NULL,
                snapshot_date DATE NOT NULL,
                challenger_lp INTEGER NOT NULL,
                grandmaster_lp INTEGER NOT NULL,
                updated_at BIGINT NOT NULL,
                PRIMARY KEY (game, region, snapshot_date)
            );
        ''')


# Riot recalculates the Grandmaster/Challenger ladders once a day at a fixed local
# time on each server. Riot doesn't document the exact time, so it is configurable:
# LADDER_RESET_TIME="HH:MM" in .env (server-local time), default 23:45.
LADDER_RESET_TIME = os.getenv("LADDER_RESET_TIME", "23:45")

# Server time zone per platform region (unknown regions fall back to UTC)
REGION_TIMEZONES = {
    "na1": "America/Los_Angeles",
    "br1": "America/Sao_Paulo",
    "la1": "America/Mexico_City",
    "la2": "America/Santiago",
    "euw1": "Europe/London",
    "eun1": "Europe/Warsaw",
    "tr1": "Europe/Istanbul",
    "ru": "Europe/Moscow",
    "kr": "Asia/Seoul",
    "jp1": "Asia/Tokyo",
    "oc1": "Australia/Sydney",
    "ph2": "Asia/Manila",
    "sg2": "Asia/Singapore",
    "th2": "Asia/Bangkok",
    "tw2": "Asia/Taipei",
    "vn2": "Asia/Ho_Chi_Minh",
}

# When each (game, region) cutoff was last stored (unix time), and the last failed attempt
_cutoff_updated_at: dict[tuple[str, str], float] = {}
_cutoff_failed_at: dict[tuple[str, str], float] = {}
CUTOFF_RETRY_SECONDS = 600


def region_timezone(region):
    return pytz.timezone(REGION_TIMEZONES.get(region, "UTC"))


def last_ladder_reset(region, now=None):
    """Most recent daily ladder reset for a region, as an aware datetime."""
    tz = region_timezone(region)
    now = now or datetime.now(pytz.utc)
    local_now = now.astimezone(tz)
    hour, minute = (int(x) for x in LADDER_RESET_TIME.split(":"))
    reset = tz.localize(datetime(local_now.year, local_now.month, local_now.day, hour, minute))
    if reset > local_now:
        reset = tz.localize(datetime.combine(local_now.date() - timedelta(days=1), dt_time(hour, minute)))
    return reset


async def load_cutoffs(pool):
    """Fill the in-memory cache with the most recent stored cutoffs."""
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT DISTINCT ON (game, region) game, region, challenger_lp, grandmaster_lp, updated_at
            FROM rank_cutoffs
            ORDER BY game, region, snapshot_date DESC, updated_at DESC;
        ''')
    for row in rows:
        key = (row['game'], row['region'])
        _rank_cutoffs[key] = (row['challenger_lp'], row['grandmaster_lp'])
        _cutoff_updated_at[key] = row['updated_at']


async def update_due_cutoffs(pool, tft_token, lol_token, default_region):
    """Store new cutoffs for every (game, region) that hasn't been updated since its last ladder reset.

    Called every minute: normally each region updates once, right after its daily
    reset. If the bot was offline at reset time, it catches up as soon as it runs,
    and a region with no stored cutoffs at all is fetched straight away.
    Each row is stored under the server-local date of the reset.
    """
    async with pool.acquire() as conn:
        regions = {row['region'] for row in await conn.fetch('SELECT DISTINCT region FROM users WHERE region IS NOT NULL;')}
    regions.add(default_region)

    now = time.time()
    for region in sorted(regions):
        reset = last_ladder_reset(region)
        for game, token in (("TFT", tft_token), ("League", lol_token)):
            key = (game, region)
            if _cutoff_updated_at.get(key, 0) >= reset.timestamp():
                continue  # already stored since the last reset
            if now - _cutoff_failed_at.get(key, 0) < CUTOFF_RETRY_SECONDS:
                continue  # failed recently, wait before retrying

            try:
                chall, gm = await fetch_cutoffs(game, region, token, background=True)
            except Exception as e:
                _cutoff_failed_at[key] = time.time()
                print(f"[ERROR] Cutoff fetch failed for {game} {region}: {e}")
                continue

            updated_at = int(time.time())
            async with pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO rank_cutoffs (game, region, snapshot_date, challenger_lp, grandmaster_lp, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (game, region, snapshot_date)
                    DO UPDATE SET challenger_lp = EXCLUDED.challenger_lp,
                                  grandmaster_lp = EXCLUDED.grandmaster_lp,
                                  updated_at = EXCLUDED.updated_at;
                ''', game, region, reset.date(), chall, gm, updated_at)
            _rank_cutoffs[key] = (chall, gm)
            _cutoff_updated_at[key] = updated_at
            _cutoff_failed_at.pop(key, None)
            print(f"[CUTOFF] {game} {region} (reset {reset:%Y-%m-%d %H:%M %Z}): Challenger {chall} LP, Grandmaster {gm} LP")


def get_stored_cutoffs(game, region):
    """Latest stored (challenger_lp, grandmaster_lp), or None if none are known yet."""
    return _rank_cutoffs.get((game, region))

# Function to get the trait icon path
def get_trait_icon(traits_data, traitName):
    for trait in traits_data:
        if trait.get("apiName") == traitName:
            return trait.get("icon", "")[:-4] # Removes the beginning of the datadragon icon_path 
    print("Trait not Found")
    return None  # Return None if the trait_id is not found

# Function to get the champ icon path
def get_champ_icon(champs_data, characterName):
    # Loop through each champion in the list
    for champion in champs_data:        
        # Check if the apiName matches the provided characterName
        if champion.get("apiName").lower() == characterName.lower():
            # Assuming 'champion' dictionary contains a 'squareIcon' key with the icon pat
            return champion.get("tileIcon", "")[:-4]  # Remove the last 4 characters (usually file extension)
    
    print(f"tft champion {characterName} Not Found")
    return None

def clean_character_id(character_id):
    return character_id.replace("DA", "").replace("AD", "").replace("AP", "").replace("18", "").replace("Small", "").replace("_", "").lower()

def get_champ_asset(set, characterId):
    characterId = clean_character_id(characterId)
    if characterId == "luxbase":
        return f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/assets/characters/tft18_lux/skins/base/images/tft18_lux_splash_tile_7.tft_set18.png"
    
    return f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/assets/characters/tft{set}_{characterId}/tft{set}_{characterId}_square.tft_set{set}.png"

# Function to get the item icon path
# tftitems.json lists some icons as "<name>.tft_setNN.png", but CommunityDragon
# serves them as "<name>.png" (all Set 18 emblems, potions and boosters, for example).
_TFT_SET_SUFFIX = re.compile(r"\.tft_set\d+(?=\.png$)", re.IGNORECASE)

def get_item_icon(items_data, itemName):
    for item in items_data:
        if item.get("nameId") == itemName:
            return _TFT_SET_SUFFIX.sub("", item.get("squareIconPath", "")[21:])
    print(f"tft item {itemName} Not Found")
    return "assets/maps/tft/icons/items/hexcore/tft_item_blank.png"

def get_companion_icon(companions_json, contentId):
    for companion in companions_json:
        if contentId == companion.get("contentId"):
            return companion.get("loadoutsIcon")[21:]
    print(f"companion {contentId} Not Found")
    return None

def get_lol_item_icon(lol_item_json, itemId):
    if itemId == 0:
        return "assets/items/icons2d/gp_ui_placeholder.png"
    for item in lol_item_json:
        if itemId == item.get("id"):
            return item.get("iconPath", "")[21:]
    print(f"league item {itemId}Not Found")
    return None

def get_lol_champ_icon(championId):
    return f"v1/champion-icons/{championId}"

LOL_CDRAGON_BASE = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/"


def blank_icon(size):
    """Transparent placeholder used when an icon can't be found or loaded."""
    return Image.new("RGBA", size or (1, 1), (0, 0, 0, 0))


async def fetch_lol_icon(path, size):
    """Fetch a League icon by its CommunityDragon path, or a blank placeholder if
    the path is unknown (e.g. Riot sends rune id 0) or the image fails to load."""
    if not path:
        return blank_icon(size)
    try:
        return await fetch_image(LOL_CDRAGON_BASE + path.lower(), size)
    except Exception as e:
        print(f"[WARN] Icon failed to load ({path}): {e}")
        return blank_icon(size)


def get_perk_ids(participant):
    """(keystone id, secondary rune tree id) from a match participant; None for any missing part."""
    styles = participant.get("perks", {}).get("styles", [])
    keystone = None
    if styles and styles[0].get("selections"):
        keystone = styles[0]["selections"][0].get("perk")
    sub_style = styles[1].get("style") if len(styles) > 1 else None
    return keystone, sub_style


def get_keystone_icon(keystones_json, style):
    for keystone in keystones_json:
        if style == keystone.get("id"):
            return keystone.get("iconPath", "")[21:]
    print(f"keystone {style} Not Found")
    return None

def get_rune_icon(runes_json, style):
    for rune in runes_json:
        if style == rune.get("id"):
            return rune.get("iconPath", "")[21:]
    print(f"rune {style} Not Found")
    return None

def get_summs_icon(summs_json, summonerId):
    for summ in summs_json:
        if summonerId == summ.get("id"):
            return summ.get("iconPath", "")[21:]
    print(f"summoner {summonerId} Not Found")
    return None 

async def get_pfp(region, puuid, lol_token):
    async with RiotAPIClient(default_headers={"X-Riot-Token": lol_token}) as client:
        profile = await client.get_lol_summoner_v4_by_puuid(region=region, puuid=puuid)
        pfp_id = profile['profileIconId']
        url = f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/profile-icons/{pfp_id}.jpg"
        return url

def center_square_crop(image: Image.Image) -> Image.Image:
    w, h = image.size
    min_side = min(w, h)
    left = (w - min_side) // 2
    top = (h - min_side) // 2
    return image.crop((left, top, left + min_side, top + min_side))

def circular_crop(image: Image.Image) -> Image.Image:
    """
    Returns a circularly cropped version of the given RGBA image.
    Keeps transparency outside the circle.
    """
    # Ensure RGBA mode
    image = image.convert("RGBA")

    # Create same-sized mask
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)

    # Draw white filled circle in the center
    width, height = image.size
    draw.ellipse((0, 0, width, height), fill=255)

    # Apply the mask as alpha channel
    result = Image.new("RGBA", image.size, (0, 0, 0, 0))
    result.paste(image, (0, 0), mask)

    return result

# Function to fetch PUUID
async def get_puuid(gameName, tagLine, mass_region, riot_token):
    try:
        async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
            account = await client.get_account_v1_by_riot_id(region=mass_region, game_name=gameName, tag_line=tagLine)
        return account['puuid']

    except Exception as err:
        print(f"Failed to retrieve PUUID for {gameName}#{tagLine}.{err}")
        return None

# Function to calculate ranked elo based on given PUUID
async def calculate_elo(puuid, game, token, region, background=False):
    while True:
        try:
            # Fetch summoner data
            if game == "TFT":
                rank_info = await get_rank_info(region, puuid, token, background)
                for entry in rank_info:
                    if entry['queueType'] == 'RANKED_TFT':
                        return dicts.rank_to_elo[entry['tier'] + " " + entry['rank']] + int(entry['leaguePoints']), entry['tier'], entry['rank'], entry['leaguePoints']
                return 0, "UNRANKED", "", 0  # If no ranked TFT entry is found
            else:
                rank_info = await get_lol_rank_info(region, puuid, token, background)
                for entry in rank_info:
                    if entry['queueType'] == 'RANKED_SOLO_5x5':
                        return dicts.rank_to_elo[entry['tier'] + " " + entry['rank']] + int(entry['leaguePoints']), entry['tier'], entry['rank'], entry['leaguePoints']
                return 0, "UNRANKED", "", 0  # If no ranked League entry is found

        except requests.exceptions.HTTPError as e:
            raise e  # Re-raise other errors

# Function to get TFT rank info from puuid and return embed with rank icon
async def get_rank_embed(name, tagLine, region, tft_token, puuid):
    gameName = name.replace("_", " ")
    try: 
        rank_info = await get_rank_info(region, puuid, tft_token)
    except Exception as err:
        return None, f"Error fetching rank info for {gameName}#{tagLine}: {err}"
    if rank_info:
        for entry in rank_info:
            if entry['queueType'] == 'RANKED_TFT':
                tier = entry['tier']
                rank = entry['rank']
                lp = entry['leaguePoints']
                total_games = entry['wins'] + entry['losses']
                top_four_rate = round(entry['wins'] / total_games * 100, 2) if total_games else 0

                # Get rank icon URL from Data Dragon github 
                rank_icon_url = "https://raw.githubusercontent.com/InFinity54/LoL_DDragon/refs/heads/master/extras/tier/" + tier.lower() + ".png"

                # Create an embed message
                embed = discord.Embed(
                    description=f"🏆 **{tier} {rank}** ({lp} LP)\n🎯 **Top 4 Rate:** {top_four_rate}%\n📊 **Total Games:** {total_games}",
                    color=discord.Color.blue()
                )
                embed.set_thumbnail(url=rank_icon_url)  # Set the rank icon
                embed.set_footer(text="Powered by Riot API | Data from TFT Ranked")
                embed.set_author(
                    name=f"TFT Stats for {gameName}#{tagLine}",
                    url=f"https://lolchess.gg/profile/{region[:-1]}/{gameName.replace(" ", "%20")}-{tagLine}/",
                    icon_url="https://static.wikia.nocookie.net/leagueoflegends/images/6/67/Teamfight_Tactics_icon.png/revision/latest/scale-to-width/360?cb=20191018215638"
                )

                return embed, None  # Return the embed
            
    return None, f"{gameName}#{tagLine} is unranked."

# Function to get LOL rank info from puuid and return embed with rank icon
async def get_lol_rank_embed(name, tagLine, region, lol_token, puuid):
    gameName = name.replace("_", " ")
    try: 
        rank_info = await get_lol_rank_info(region, puuid, lol_token)
    except Exception as err:
        return None, f"Error fetching rank info for {gameName}#{tagLine}: {err}"
    if rank_info:
        for entry in rank_info:
            if entry['queueType'] == 'RANKED_SOLO_5x5':
                tier = entry['tier']
                rank = entry['rank']
                lp = entry['leaguePoints']
                total_games = entry['wins'] + entry['losses']
                win_rate = round(entry['wins'] / total_games * 100, 2) if total_games else 0

                # Get rank icon URL from Data Dragon github 
                rank_icon_url = "https://raw.githubusercontent.com/InFinity54/LoL_DDragon/refs/heads/master/extras/tier/" + tier.lower() + ".png"

                # Create an embed message
                embed = discord.Embed(
                    description=f"🏆 **{tier} {rank}** ({lp} LP)\n🎯 **Win Rate:** {win_rate}%\n📊 **Total Games:** {total_games}",
                    color=discord.Color.blue()
                )
                embed.set_thumbnail(url=rank_icon_url)  # Set the rank icon
                embed.set_footer(text="Powered by Riot API | Data from LOL Ranked")
                embed.set_author(
                    name=f"LOL Stats for {gameName}#{tagLine}",
                    url=f"https://op.gg/lol/summoners/{region[:-1]}/{gameName.replace(" ", "%20")}-{tagLine}",
                    icon_url="https://static.wikia.nocookie.net/leagueoflegends/images/9/9a/League_of_Legends_Update_Logo_Concept_05.jpg/revision/latest/scale-to-width-down/250?cb=20191029062637"
                )

                return embed, None  # Return the embed
            
    return None, f"{gameName}#{tagLine} is unranked."

def round_elo_to_rank(avg_elo):
    if avg_elo > 2800:
        return "Master+", int(round(avg_elo - 2800))
    rounded_elo = (avg_elo // 100) * 100
    rank = next((key for key, val in dicts.rank_to_elo.items() if val == rounded_elo), None)
    return rank, 0

async def find_all_match_ids(puuid, game, mass_region, token, timestamp, background=False):
    match_list = []
    counter = 0

    try:
        async with riot_client(token, background) as client:
            while counter < 1000:
                if game == "TFT":
                    if timestamp:
                        matches = await client.get_tft_match_v1_match_ids_by_puuid(
                            region=mass_region,
                            puuid=puuid,
                            queries={"start": counter, "startTime": timestamp, "count": 100}
                        )
                    else:
                        matches = await client.get_tft_match_v1_match_ids_by_puuid(
                            region=mass_region,
                            puuid=puuid,
                            queries={"start": counter, "count": 100}
                        )

                elif game == "League":
                    if timestamp:
                        matches = await client.get_lol_match_v5_match_ids_by_puuid(
                            region=mass_region,
                            puuid=puuid,
                            queries={"queue": 420, "type": "ranked", "start": counter, "startTime": timestamp, "count": 100}
                        )
                    else:
                        matches = await client.get_lol_match_v5_match_ids_by_puuid(
                            region=mass_region,
                            puuid=puuid,
                            queries={"queue": 420, "type": "ranked", "start": counter, "count": 100}
                        )

                else:
                    return f"Invalid game type: {game}", None, puuid

                if not matches:
                    break

                match_list.extend(matches)
                counter += 100  # Move to next batch

        if not match_list:
            return f"No matches found for {puuid}.", None

        return None, match_list

    except Exception as err:
        return f"Error fetching matches for {puuid}: {err}", None


async def nth_league_match(token, region, puuid, queue, n):
    print(f"Fetching match {n} for PUUID: {puuid} in region: {region} for queue: {queue}")
    async with RiotAPIClient(default_headers={"X-Riot-Token": token}) as client:
        ids = await client.get_lol_match_v5_match_ids_by_puuid(region=region, puuid=puuid, queries={"start": n - 1, "count": 1, "queue": queue})
    print(f"Fetched match IDs: {ids} for PUUID: {puuid}")
    return ids[0] if ids else None

async def generate_board_preview(index, puuid, region, mass_region, match_id, tft_token, mappings):

    async with RiotAPIClient(default_headers={"X-Riot-Token": tft_token}) as client:
        match_info = await client.get_tft_match_v1_match(region=mass_region, id=match_id)

    participants = match_info['info']['participants']

    # Sort in ascending order
    if puuid not in [p['puuid'] for p in participants]:
        return f"Could not find participant with PUUID: {puuid}"

    participants_sorted = sorted(participants, key=lambda x: x['placement'])
    puuid_list = [p['puuid'] for p in participants_sorted]
    current_index = puuid_list.index(puuid)
    participant = participants_sorted[index]

    # --- Traits Processing ---
    traits = participant['traits']
    filtered_traits = [trait for trait in traits if trait['style'] >= 1]
    sorted_traits = sorted(filtered_traits, key=lambda x: dicts.style_order.get(x['style'], 5))
    num_traits = len(sorted_traits)
    start_y = 15

    if num_traits == 0:
        trait_img_width = 0
    else:
        trait_img_width = int(min((675 / num_traits), 71))

    trait_img_height = 110
    trait_final_width = 675
    if puuid == participant['puuid']:
        background_color = "#2F3136"
    else:
        background_color = (0,0,0,0)

    trait_final_image = Image.new("RGBA", (trait_final_width, trait_img_height), background_color)

    async def process_trait(trait, i):
        temp_image = await trait_image(trait['name'], trait['style'], mappings["trait_icon_mapping"])
        if temp_image:
            temp_image = temp_image.convert("RGBA")
            temp_image.thumbnail((trait_img_width, int(trait_img_width*1.16)))
            mask = temp_image.split()[3]
            trait_final_image.paste(temp_image, (trait_img_width * i, start_y), mask)

    # Fetch trait images concurrently
    await asyncio.gather(*[process_trait(trait, i) for i, trait in enumerate(sorted_traits)])
    font = ImageFont.truetype("fonts/NotoSans-Bold.ttf", size=28)
    bold_font = ImageFont.truetype("fonts/NotoSans-Black.ttf", size=60)  

    companion_height = 235
    companion_width = 225
    companion_size = 100
    companion_final_image = Image.new("RGBA", (companion_width,  companion_height), background_color)
    companion_id = participant.get("companion", {}).get("content_ID")
    companion_path = get_companion_icon(mappings["companion_mapping"], companion_id)
    companion_url = f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/" + companion_path.lower()
    companion_image = await fetch_image(companion_url)
    square = center_square_crop(companion_image)
    circle_img = circular_crop(square)
    circle_img.thumbnail((companion_size,companion_size))
    draw = ImageDraw.Draw(companion_final_image)

    def truncate_text(draw, text, font, max_width):
        # Truncate text with ellipsis (…) if it exceeds the given pixel width.
        ellipsis = "…"
        if draw.textlength(text, font=font) <= max_width:
            return text
        while text and draw.textlength(text + ellipsis, font=font) > max_width:
            text = text[:-1]
        return text + ellipsis
        
    display_name = truncate_text(draw, participant.get('riotIdGameName', 'Unknown'), font, 210)
    bbox = font.getbbox(display_name)  # (x_min, y_min, x_max, y_max)
    name_w = bbox[2] - bbox[0]

    placements = ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"]
    font_color = ['#F0B52B', "#B8B5B5", '#A45F00', '#595988', "#748283", '#748283', '#748283', '#748283']
    player_placement = participant.get("placement")
    
    draw.text((15, 5), placements[player_placement - 1], font=bold_font, fill=font_color[player_placement - 1])
    if name_w <= companion_size:
        draw_centered(draw, display_name, font, 65, y=190, fill="white")
    else:
        draw.text((15,190), display_name, font=font, fill="white")
    companion_final_image.paste(circle_img, (15,90), circle_img)

    try: 
        rank_info = await get_rank_info(region, participant["puuid"], tft_token)
    except Exception as err:
        return None, f"Error fetching rank info for {gameName}#{tagLine}: {err}"

    tier = "UNRANKED"
    rank = "I"
    if rank_info:
        for entry in rank_info:
            if entry['queueType'] == 'RANKED_TFT':
                tier = entry['tier']
                rank = entry['rank']

    rank_str = f"{dicts.rank_to_acronym[tier]}{dicts.rank_to_number[rank]}"

    bbox = font.getbbox(rank_str)  # (x_min, y_min, x_max, y_max)
    text_w = bbox[2] - bbox[0]
    padding_x = 12
    x, y = 130, 140
    rect_coords = [
        x, 
        y, 
        x + text_w + padding_x, 
        180
    ]
    draw.rounded_rectangle(rect_coords, radius=8, fill=dicts.rank_to_text_fill[tier], outline=None)
    draw.circle((100,175),15, fill="Black", outline="White")
    draw_centered(draw, str(participant.get("level","?")), font, 100, y=155, fill="White")
    draw.text((x + padding_x/2, y), rank_str, font=font, fill="#fdda82" if tier == "CHALLENGER" else "white")

    # --- Champions Processing ---
    units = participant.get('units', [])
    champ_unit_data_unsorted = []

    # Calculate total champion image width
    champ_img_width = int(min((655) / len(units), 70)) if units else 70
    champ_img_height = 125
    champ_final_image = Image.new("RGBA", (675, champ_img_height), background_color)

    async def process_unit(unit):
        champion_name = unit["character_id"]
        tier = unit["tier"]
        rarity = unit["rarity"]
        item_names = unit["itemNames"]

        custom_rarity = dicts.rarity_map.get(rarity, rarity)
        # champ_icon_path = get_champ_icon(mappings["champ_mapping"], champion_name).lower()
        rarity_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft-team-planner/global/default/images/cteamplanner_championbutton_tier{custom_rarity}.png"

        # if champ_icon_path:
        #     champion_url = f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{champ_icon_path}.png"

        champion_url = get_champ_asset(18, champion_name)

        champ_task = fetch_image(champion_url, (64, 64))
        rarity_task = fetch_image(rarity_url, (72, 72))
        
        icon_resized, rarity_resized = await asyncio.gather(champ_task, rarity_task)

        print(f"Finished: {champion_name}")

        champ_unit_data_unsorted.append({
            "champion_name": champion_name,
            "icon_resized": icon_resized,
            "rarity_resized": rarity_resized,
            "rarity": rarity,
            "tier": tier,
            "item_names": item_names
        })

    # Fetch all champion icons & rarity images concurrently
    print("Starting gather...")

    await asyncio.gather(*[process_unit(unit) for unit in units])

    print("Finished gather!")
    print(f"Got {len(champ_unit_data_unsorted)} units")

    champ_unit_data = sorted(
        champ_unit_data_unsorted,
        key=lambda x: x["rarity"]
    )

    print("Finished sorting!")
    print("Finished fetching champion icons and rarity images.")

    async def paste_champion(unit, i): 
        champ_image = await champion_image(unit, mappings["item_mapping"])
        if champ_image:
            champ_image.thumbnail((champ_img_width, champ_img_height))
            champ_final_image.paste(champ_image, (((champ_img_width + 1)* i), 0), champ_image)

    # Process and paste champions concurrently
    await asyncio.gather(*[paste_champion(unit, i) for i, unit in enumerate(champ_unit_data)])

    stats_height = 50
    stats_image = Image.new("RGBA", (900, stats_height), background_color)
    gold_image = await fetch_image("https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft/global/default/images/home/tft_icon_coins.png", (40,40))
    damage_image = await fetch_image("https://cdn.tft.tools/general/announce_icon_combat.png", (40,40))

    gold_left = str(participant.get('gold_left','Unknown')) + " |"
    stage, round_num  = divmod(participant.get('last_round','Unknown') - 4 , 7)
    total_damage_to_players = str(participant.get('total_damage_to_players','Unknown'))
    round_str = f"| {stage+2} - {round_num}"

    draw = ImageDraw.Draw(stats_image)
    bbox = font.getbbox(gold_left)  # (x_min, y_min, x_max, y_max)
    gold_w = bbox[2] - bbox[0]
    bbox = font.getbbox(total_damage_to_players) # (x_min, y_min, x_max, y_max)
    damage_w = bbox[2] - bbox[0]
    
    stats_image.paste(gold_image, (5,5), gold_image)
    draw.text((55,5), gold_left, font=font, fill="White")
    stats_image.paste(damage_image, (65 + gold_w, 5), damage_image)
    draw.text((105 + gold_w, 5), total_damage_to_players, font=font, fill="White")
    draw.text((115 + gold_w + damage_w, 5), round_str, font=font, fill="White")

    # --- Combine Images ---
    final_combined_image = Image.new("RGBA", (900, companion_height + stats_height), (0, 0, 0, 0))
    final_combined_image.paste(trait_final_image, (companion_width, 0), trait_final_image)
    final_combined_image.paste(champ_final_image, (companion_width, trait_img_height), champ_final_image)
    final_combined_image.paste(companion_final_image, (0,0), companion_final_image)
    final_combined_image.paste(stats_image, (0,companion_height), stats_image)

    # Get summoner's gameName and tagLine from the match_info
    gameName = participant.get('riotIdGameName', 'Unknown')
    tagLine = participant.get('riotIdTagline', 'Unknown')

    # Save & Return Image & Embed
    final_combined_image.save("player_board.png")
    embed_colors = ['#F0B52B', "#B8B5B5", '#A45F00', '#595988', "#748283", '#748283', '#748283', '#748283']
    embed = discord.Embed(
        color=discord.Color(int(embed_colors[player_placement - 1].strip("#"), 16))
    )

    file = discord.File("player_board.png", filename="player_board.png")
    embed.set_image(url="attachment://player_board.png")

    return embed, file, final_combined_image


# Function to grab previous match data
async def last_match(gameName, tagLine, mode, mass_region, tft_token, region, game_num):
    puuid = await get_puuid(gameName, tagLine, mass_region, tft_token)
    if not puuid:
        return f"Could not find PUUID for {gameName}#{tagLine}.", None, None, 0, None, None

    if not (1 <= game_num <= 20):
        return f"Please enter a number between 1 and 20.", None, None, 0, None, None

    try:
        async with RiotAPIClient(default_headers={"X-Riot-Token": tft_token}) as client:
            match_list = await client.get_tft_match_v1_match_ids_by_puuid(region=mass_region, puuid=puuid)
            if not match_list:
                return f"No matches found for {gameName}#{tagLine}.", None, None, 0, None, None

            target_queue = dicts.game_type_to_id[mode]
            match_id = None

            for match in match_list:
                match_info = await client.get_tft_match_v1_match(region=mass_region, id=match)
                queue_id = match_info['info']['queue_id']
                if (mode == "GameMode" and queue_id > target_queue) or (mode != "GameMode" and queue_id == target_queue):
                    game_num -= 1
                    if game_num == 0:
                        match_id = match
                        break

            if not match_id:
                return f"No recent {mode.lower()} matches found for {gameName}#{tagLine}.", None, None, 0, None, None

            match_info = await client.get_tft_match_v1_match(region=mass_region, id=match_id)
            participants = match_info['info']['participants']
            timestamp = match_info['info']['game_datetime'] / 1000
            formatted_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
            time_and_time_ago = formatted_time + ", " + time_ago(timestamp)
            riot_ids_tasks = [client.get_account_v1_by_puuid(region=mass_region, puuid=p['puuid']) for p in participants]
            rank_info_tasks = [get_rank_info(region, p['puuid'], tft_token) for p in participants]
            riot_ids, ranks = await asyncio.gather(asyncio.gather(*riot_ids_tasks), asyncio.gather(*rank_info_tasks))

            players_data = []
            player_elos = 0
            ranked_players = 8

            for i, participant in enumerate(participants):
                placement = participant['placement']
                riot_id = riot_ids[i]
                rank_info = ranks[i]

                name = f"{riot_id.get('gameName', 'Unknown')}#{riot_id.get('tagLine', '')}" if 'gameName' in riot_id else "Unknown Player"
                tier_and_rank = ""
                lp = 0

                for entry in rank_info:
                    if entry['queueType'] == 'RANKED_TFT':
                        tier = entry['tier']
                        rank = entry['rank']
                        tier_and_rank = f"{tier} {rank}"
                        lp = entry['leaguePoints']
                        break

                if tier_and_rank:
                    player_elos += dicts.rank_to_elo.get(tier_and_rank, 0) + lp
                    rank_icon = dicts.tier_to_rank_icon.get(tier, dicts.tier_to_rank_icon["UNRANKED"])
                else:
                    ranked_players -= 1
                    rank_icon = dicts.tier_to_rank_icon["UNRANKED"]

                players_data.append((placement, name, rank_icon))

            players_data.sort()
            avg_elo = player_elos / ranked_players if ranked_players else 0
            avg_rank, master_lp = round_elo_to_rank(avg_elo)

            result = ""
            full_name = f"{gameName}#{tagLine}"
            for placement, name, icon in players_data:
                if custom_equal(full_name, name, "_ "):
                    result += f"{icon} **{placement}** - **__{name}__**\n"
                    player_placement = placement
                else:
                    result += f"{icon} **{placement}** - {name}\n"

            return result, match_id, avg_rank, master_lp, time_and_time_ago, player_placement

    except Exception as err:
        return f"Error fetching last match for {gameName}#{tagLine}: {err}", None, None, 0, None

# Function to grab previous match data
async def league_last_match(gameName, tagLine, mass_region, lol_token, puuid, match_id, mode, mappings, background_color, header, lp_delta=None):
    try:
        async with RiotAPIClient(default_headers={"X-Riot-Token": lol_token}) as client:

            match_info = await client.get_lol_match_v5_match(region=mass_region, id=match_id)

            duration = match_info["info"]["gameDuration"]
            participants = match_info["info"]["participants"]
            endstamp = match_info["info"]["gameEndTimestamp"] / 1000
            maxDamage = participants[0]["totalDamageDealtToChampions"]
            maxTaken = participants[0]["totalDamageTaken"]
            blue_team = [p for p in participants if p["teamId"] == 100]
            red_team = [p for p in participants if p["teamId"] == 200]

            blue_win = match_info["info"]["teams"][0]["win"]
            remake = False
            for participant in participants:
                maxDamage = max(participant["totalDamageDealtToChampions"],maxDamage)
                maxTaken = max(participant["totalDamageTaken"],maxTaken)
                if participant["gameEndedInEarlySurrender"] == True:
                    remake = True

                if participant["puuid"] == puuid:
                    cs = participant["totalMinionsKilled"] + participant["neutralMinionsKilled"]
                    level = participant["champLevel"]
                    kills = participant["kills"]
                    deaths = participant["deaths"]
                    assists = participant["assists"]
                    gold = participant["goldEarned"]
                    killparticipation = participant.get("challenges", {}).get("killParticipation", 0)
                    win = participant["win"]
                    tab_final = Image.new("RGBA", (500, 100), background_color)

                    champ_path = get_lol_champ_icon(participant["championId"])
                    keystone_id, sub_style_id = get_perk_ids(participant)
                    keystone_path = get_keystone_icon(mappings["keystone_mapping"], keystone_id)
                    runes_path = get_rune_icon(mappings["runes_mapping"], sub_style_id)
                    summ1_path = get_summs_icon(mappings["summs_mapping"], participant["summoner1Id"])
                    summ2_path = get_summs_icon(mappings["summs_mapping"], participant["summoner2Id"])
                    items = [participant[f"item{i}"] for i in range(7)]
                    item_paths = [get_lol_item_icon(mappings['lol_item_mapping'], item) for item in items]

                    # --- Fetch images concurrently ---
                    fetch_tasks = [
                        fetch_lol_icon(f"{champ_path}.png", (60,60)),
                        fetch_lol_icon(keystone_path, (30,30)),
                        fetch_lol_icon(runes_path, (20,20)),
                        fetch_lol_icon(summ1_path, (30,30)),
                        fetch_lol_icon(summ2_path, (30,30)),
                        fetch_image(f"https://wiki.leagueoflegends.com/en-us/images/thumb/Gold_colored_icon.png/20px-Gold_colored_icon.png?39991", (20,20)),
                    ]
                    # Add item icons
                    fetch_tasks.extend([fetch_lol_icon(p, (30,30)) for p in item_paths])

                    images = await asyncio.gather(*fetch_tasks)
                    champ_image, keystone_image, runes_image, summ1_image, summ2_image, gold_image, *item_icons = images

                    for i, item_icon in enumerate(item_icons):
                        tab_final.paste(item_icon, (170 + 30*i, 70), item_icon)

                    tab_final.paste(champ_image, (170,0))
                    tab_final.paste(keystone_image, (260,0))
                    tab_final.paste(runes_image,(265,35))
                    tab_final.paste(summ1_image,(230,0))
                    tab_final.paste(summ2_image,(230,30))
                    tab_final.paste(gold_image, (390,75))
                    draw = ImageDraw.Draw(tab_final)

                    font = ImageFont.truetype("fonts/NotoSans-Bold.ttf", 15)
                    bold_font = ImageFont.truetype("fonts/NotoSans-Black.ttf", 17)  

                    kda_str = f"{kills} / {deaths} / {assists}"
                    if deaths == 0:
                        kda_ratio_str = "Perfect"
                    else:
                        kda_ratio = (kills + assists) / deaths
                        kda_ratio_str = f"{kda_ratio:.2f}:1  KDA"
                    minutes, secs = divmod(duration, 60)
                    cspm = cs * 60 / duration
                    time_str = f"{minutes}m {secs}s"
                    kp_str = f"P/Kill {killparticipation:.0%}"
                    cs_str = f"CS {cs} ({cspm:.1f})"
                    gold_str = f" {gold:,}"

                    if win:
                        font_color = "#5485eb"
                    else:
                        font_color = "#e64253"

                    if remake:
                        font_color = "#8a8a8a"

                    kda_color = "#8a8a8a"
                    if kda_ratio_str == "Perfect":
                        kda_color = "#f78324"
                    elif kda_ratio >= 5.00:
                        kda_color = "#f78324"
                    elif kda_ratio >= 4.00:
                        kda_color = "#188ae9"
                    elif kda_ratio >= 3.00:
                        kda_color = "#29b0a3"

                    if win:
                        result_str = "Victory"
                    elif remake:
                        result_str = "Remake"
                    else:
                        result_str = "Defeat"

                    end_str = time_ago(endstamp)

                    draw.text((15,0), f"{mode}", font=bold_font, fill=font_color)
                    draw.text((15,23), end_str, font=font, fill="white")
                    draw.text((15,61), result_str, font=font, fill="white")
                    draw.text((15,81), time_str, font=font, fill="white")
                    if lp_delta is not None:
                        # Right-aligned in the empty space between the result/duration and the champion icon
                        lp_str = f"{lp_delta:+d} LP"
                        lp_color = "#5485eb" if lp_delta > 0 else "#e64253" if lp_delta < 0 else "#8a8a8a"
                        lp_x = 160 - draw.textlength(lp_str, font=bold_font)
                        draw.text((lp_x, 70), lp_str, font=bold_font, fill=lp_color)
                    draw.text((295,0), kda_str, font=bold_font, fill="white")
                    draw.text((295,26), kda_ratio_str, font=font, fill=kda_color)
                    draw.text((390,0), kp_str, font=font, fill="white")
                    draw.text((390,26), cs_str, font=font, fill="white")
                    draw.text((410,75), gold_str, font=font, fill="white")
                    draw.rectangle([210,40,228,60], fill="black", outline=None)
                    draw_centered(draw, str(participant["champLevel"]), font, 220, y=40, fill="white")
                    buffer = BytesIO()
                    tab_final.save(buffer, format="PNG")
                    buffer.seek(0)

                    filename = f"tab_{match_id}.png"
                    final_file = discord.File(buffer, filename=filename)

                    tab_embed = discord.Embed(
                        title=f"Recent League match for {gameName}#{tagLine}"if header else None,
                        color=int(font_color.strip("#"), 16)
                    )

                    tab_embed.set_image(url=f"attachment://{filename}")

            return None, final_file, tab_embed, maxDamage, maxTaken, duration, blue_team, red_team, blue_win

    except Exception as err:
        return f"Error fetching last match for {gameName}#{tagLine}: {err}",  None, None, None, None, None, None, None, None
        
# Get recent x matches
async def recent_matches(gameName, tagLine, puuid, mode, mass_region, tft_token, num_matches):
    try:
        # Fetch the latest x matches
        placements = []
        real_num_matches = 0
        if num_matches > 20 or num_matches < 0:
            print(f"Please enter a number between 1 and 20.")
            return f"Please enter a number between 1 and 20.", None, None
        
        async with RiotAPIClient(default_headers={"X-Riot-Token": tft_token}) as client:
            match_list = await client.get_tft_match_v1_match_ids_by_puuid(region=mass_region, puuid=puuid)
            if not match_list:
                print(f"No matches found for {gameName}#{tagLine}.")
                return f"No matches found for {gameName}#{tagLine}.", None, None

            for match in match_list:
                match_info = await client.get_tft_match_v1_match(region=mass_region, id=match)
                if mode == "GameMode":
                    if match_info['info']['queue_id'] > dicts.game_type_to_id[mode]:
                        num_matches -= 1
                        real_num_matches += 1
                        for participant in match_info['info']['participants']:
                            player_puuid = participant['puuid']
                            if player_puuid == puuid:
                                placements.append(participant['placement'])
                                break
                else:
                    if match_info['info']['queue_id'] == dicts.game_type_to_id[mode]:
                        num_matches -= 1
                        real_num_matches += 1
                        for participant in match_info['info']['participants']:
                            player_puuid = participant['puuid']
                            if player_puuid == puuid:
                                placements.append(participant['placement'])
                                break
                if num_matches <= 0:
                    break
                
        if real_num_matches == 0:
            print(f"No recent {mode} matches found for {gameName}#{tagLine}.")
            return f"No recent {mode} matches found for {gameName}#{tagLine}.", None, None
        return None, placements, real_num_matches
        
    except Exception as err:
        print(f"Error fetching recent matches for {gameName}#{tagLine}: {err}")
        return f"Error fetching recent matches for {gameName}#{tagLine}: {err}", None, None

# Custom equal to handle spaces in usernames
def custom_equal(str1, str2, chars_to_ignore):
    str1 = str1.lower().translate(str.maketrans('', '', chars_to_ignore))
    str2 = str2.lower().translate(str.maketrans('', '', chars_to_ignore))
    return str1 == str2

# Function to check if user is linked based on discord id
async def check_data(id, pool, game):

    user_id = int(id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT league_puuid, tft_puuid, game_name, tag_line, region, mass_region
            FROM users
            WHERE discord_id = $1
        ''', user_id)
    if row:
        gameName = row['game_name']
        tagLine = row['tag_line']
        region = row['region']
        mass_region = row['mass_region']
        puuid = row['tft_puuid'] if game == "TFT" else row['league_puuid']
        return True, gameName, tagLine, region, mass_region, puuid
    else:
        return False, None, None, None, None, None
    
# Function to check if user is linked based on name and tag
async def check_data_name_tag(name, tag, pool, game):
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT discord_id, league_puuid, tft_puuid, game_name, tag_line, region, mass_region
            FROM users
            WHERE game_name = $1 AND tag_line = $2
        ''', name, tag)

    if row:
        gameName = row['game_name']
        tagLine = row['tag_line']
        region = row['region']
        mass_region = row['mass_region']
        puuid = row['tft_puuid'] if game.upper() == "TFT" else row['league_puuid']
        discord_id = row['discord_id']
        return True, gameName, tagLine, region, mass_region, puuid, discord_id
    else:
        # User not found
        return False, name, tag, None, None, None, None

# Command to get trait icon with texture 
async def trait_image(trait_name: str, style: int, trait_icon_mapping):
    try:
        # Download the trait texture  
        texture_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft/global/default/{dicts.style_to_texture.get(style, 'default_texture')}.png"
        texture = await fetch_image(texture_url)

        # Download the trait icon
        icon_path = get_trait_icon(trait_icon_mapping, trait_name).lower()
        icon_url = f"https://raw.communitydragon.org/latest/game/{icon_path}.png"

        icon = await fetch_image(icon_url)

        # Convert white parts of the icon to black for better visibility
        icon = icon.convert("RGBA")
        pixels = icon.load()
        for i in range(icon.width):
            for j in range(icon.height):
                r, g, b, a = pixels[i, j]
                if r > 200 and g > 200 and b > 200:  # If the pixel is white
                    pixels[i, j] = (0, 0, 0, a)  # Change it to black (retain transparency)

        # Resize the icon
        icon_resized = icon.resize((64, 64), Image.LANCZOS)

        # Ensure the texture is large enough to paste the icon
        if texture.width < 64 or texture.height < 64:
            print(f"Warning: Texture is too small ({texture.width}x{texture.height}), skipping overlay.")
            return None

        # Paste the icon onto the texture
        texture.paste(icon_resized, (12, 18), icon_resized)

        return texture

    except Exception as e:
        print(f"Error in trait_image for {trait_name} (style {style}): {e}")
        return None

async def champion_image(unit, item_mapping):

    try:
        tier = unit["tier"]
        item_names = unit["item_names"]

        rarity_resized = unit["rarity_resized"]
        icon_resized = unit["icon_resized"]
        tile_image = Image.new("RGBA", (72, 115), (0, 0, 0, 0))

        tile_image.paste(rarity_resized, (0, 20), rarity_resized)
        tile_image.paste(icon_resized, (4, 24), icon_resized)

        # Add tier stars (2★ or 3★)
        if tier in {2, 3}:
            tier_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft/global/default/tft-piece-star-{tier}.png"
            tier_icon = await fetch_image(tier_url, (72, 36))
            if tier_icon:
                tile_image.paste(tier_icon, (0, 0), tier_icon)

        # Add item icons if available
        if item_names:
            item_urls = [
                f"https://raw.communitydragon.org/latest/game/{get_item_icon(item_mapping, item).lower()}"
                for item in item_names
            ]

            fetch_tasks = [
                fetch_image(url, (24,24))
                for url in item_urls
            ]
            # A missing item icon leaves an empty slot instead of dropping the whole unit
            item_icons = await asyncio.gather(*fetch_tasks, return_exceptions=True)

            # Center the items horizontally below the portrait
            for i, (url, item_icon) in enumerate(zip(item_urls, item_icons)):
                if isinstance(item_icon, Exception):
                    print(f"Item icon failed for {url}: {item_icon}")
                    continue
                tile_image.paste(item_icon, (24 * i, 92), item_icon)

        return tile_image

    except Exception as e:
        print(f"Error building champion image for {unit.get('champion_name', 'Unknown')}: {e}")
        return None

# Calculate how long ago timestamp was to include in !r response
def time_ago(timestamp):
    # Get the current time in Unix timestamp format
    current_time = time.time()
    
    # Calculate the difference between the current time and the given timestamp
    time_difference = current_time - timestamp

    # Define time units in seconds
    seconds_in_minute = 60
    seconds_in_hour = seconds_in_minute * 60
    seconds_in_day = seconds_in_hour * 24
    seconds_in_week = seconds_in_day * 7
    seconds_in_month = seconds_in_day * 30
    seconds_in_year = seconds_in_day * 365
    
    # Check which time unit is appropriate for the difference
    if time_difference < seconds_in_minute:
        return f"{int(time_difference)} seconds ago"
    elif time_difference < seconds_in_hour:
        minutes = time_difference // seconds_in_minute
        return f"{int(minutes)} minutes ago"
    elif time_difference < seconds_in_day:
        hours = time_difference // seconds_in_hour
        return f"{int(hours)} hours ago"
    elif time_difference < seconds_in_week:
        days = time_difference // seconds_in_day
        return f"{int(days)} days ago"
    elif time_difference < seconds_in_month:
        weeks = time_difference // seconds_in_week
        return f"{int(weeks)} weeks ago"
    elif time_difference < seconds_in_year:
        months = time_difference // seconds_in_month
        return f"{int(months)} months ago"
    else:
        years = time_difference // seconds_in_year
        return f"{int(years)} years ago"

def lp_to_div(elo_value, game=None, region=None):
    # Apex tiers all share the same base elo, so pick the tier from the stored cutoffs.
    # Without known cutoffs, apex players are shown as Master.
    if elo_value >= APEX_ELO:
        lp = elo_value - APEX_ELO
        tier = "MASTER"
        cutoffs = get_stored_cutoffs(game, region) if game and region else None
        if cutoffs:
            chall, gm = cutoffs
            if lp >= chall:
                tier = "CHALLENGER"
            elif lp >= gm:
                tier = "GRANDMASTER"
        return tier, "I", lp

    # Reverse mapping: Elo -> rank string
    elo_to_rank = {v: k for k, v in dicts.rank_to_elo.items()}
    sorted_ranks = sorted(elo_to_rank.items(), key=lambda x: x[0])

    rank_name = "UNRANKED"
    threshold = 0

    for value, name in sorted_ranks:
        if elo_value >= value:
            rank_name = name
            threshold = value
        else:
            break

    excess = elo_value - threshold

    if rank_name != "UNRANKED":
        tier, division = rank_name.split(" ")
    else:
        tier, division = rank_name, None

    return tier, division, excess

async def is_user_in_guild(guild: discord.Guild, user_id: int) -> bool:
    try:
        await guild.fetch_member(user_id)
        return True
    except discord.NotFound:
        return False
    except discord.Forbidden:
        print("Missing permissions to fetch member.")
        return False
    except discord.HTTPException as e:
        print(f"HTTP error while fetching member: {e}")
        return False

async def parse_args(ctx, args):
    gameNum, gameName, tagLine, user_id = None, None, None, None
    
    if len(args) == 3: # <gameNum, username, tagline>
        if args[0].isdigit():
            gameNum = int(args[0])
            gameName, tagLine = args[1], args[2]
        else:
            return None, None, None, None, "Format should be <game #, username, tagline>"
        
    elif len(args) == 2 and args[1].startswith("<@"): # <gameNum, @mention>
        if args[0].isdigit():
            gameNum = int(args[0])
            user_id = args[1].strip("<@!>")
        else:
            return None, None, None, None, "Format should be <game #, @mention>"
        
    elif len(args) == 2: # <username, tagline>
        gameName, tagLine = args[0], args[1]

    elif len(args) == 1 and args[0].startswith("<@"): # <@mention>
        user_id = args[0].strip("<@!>")

    elif len(args) == 1 and args[0].isdigit(): # <gameNum>
        gameNum = int(args[0])
        user_id = ctx.author.id

    elif len(args) == 0: # all defaults 
        user_id = ctx.author.id

    else:
        return None, None, None, None, (
            "Usage: `<matches?> <name> <tag>` | `<matches?> @mention` | "
            "`<matches?>` (self) | no args (self)"
        )

    return gameNum, gameName, tagLine, user_id, None

def draw_centered(draw, text, font, center_x, y, fill="white"):
    bbox = font.getbbox(text)
    text_w = bbox[2] - bbox[0]
    # left x so that text's midpoint sits on center_x
    x = center_x - text_w // 2
    draw.text((x, y), text, font=font, fill=fill)