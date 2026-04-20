# Standard library imports
import datetime
import enum
import logging
import os
import re
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
from typing import Optional

# Third-party imports
import anthropic
import pandas as pd
from django.apps import apps

from thetower.backend.env_config import get_csv_data

# Local imports
from .archive_utils import list_archives, read_archive, reconstruct_at
from .constants import leagues, legend
from .data import get_banned_ids, get_player_id_lookup, get_shun_ids, get_sus_ids, get_tourneys
from .models import PromptTemplate, TourneyResult, TourneyRow
from .shun_config import include_shun_enabled_for

# Initialize logging
logging.basicConfig(level=logging.INFO)

# ── Tournament state detection ─────────────────────────────────────────────

# Tournament days: Wednesday=2, Saturday=5 (Python weekday() values)
TOURNAMENT_DAYS = {2, 5}

# Weekday groupings for offset calculation (same logic as get_live_results.py)
_WEEKDAYS_WED = [2, 3, 4]
_WEEKDAYS_SAT = [5, 6, 0, 1]


class TourneyState(enum.Enum):
    """Current state of the tournament cycle.

    Tournaments run for 28 hours total:
    - ENTRY_OPEN: First 24 hours (00:00–24:00 UTC on tournament day).
      Players can enter and play.
    - EXTENDED: Final 4 hours (00:00–04:00 UTC the following day).
      No new entries; players finish their runs.
    - INACTIVE: No tournament is currently running.
    """

    INACTIVE = "inactive"
    ENTRY_OPEN = "entry_open"
    EXTENDED = "extended"

    @property
    def is_active(self) -> bool:
        """True if a tournament is currently running (entry or extended)."""
        return self in (TourneyState.ENTRY_OPEN, TourneyState.EXTENDED)

    @property
    def is_entry_open(self) -> bool:
        """True if players can still enter the tournament."""
        return self == TourneyState.ENTRY_OPEN


def get_tourney_state(dt: Optional[datetime.datetime] = None) -> TourneyState:
    """Determine the current tournament state based on UTC time.

    Tournament timing (all UTC):
    - Tournament day :00 through :23:59 → ENTRY_OPEN (24 hours)
    - Next day 00:00 through 03:59 → EXTENDED (4 hours, no new entries)
    - Otherwise → INACTIVE

    Args:
        dt: Datetime to check. Defaults to current UTC time.

    Returns:
        Current TourneyState.
    """
    if dt is None:
        dt = datetime.datetime.now(datetime.timezone.utc)

    weekday = dt.weekday()

    # Calculate offset from last tournament day
    if weekday in _WEEKDAYS_WED:
        offset = weekday - 2  # 0 on Wed, 1 on Thu, 2 on Fri
    elif weekday in _WEEKDAYS_SAT:
        offset = (weekday - 5) % 7  # 0 on Sat, 1 on Sun, 2 on Mon, 3 on Tue
    else:
        return TourneyState.INACTIVE

    if offset == 0:
        return TourneyState.ENTRY_OPEN
    elif offset == 1 and dt.hour < 4:
        return TourneyState.EXTENDED
    else:
        return TourneyState.INACTIVE


def create_tourney_rows(tourney_result: TourneyResult) -> None:
    """Idempotent function to process tourney result during the csv import process.

    The idea is that:
     - if there are not rows created, create them,
     - if there are already rows created, update all positions at least (positions should never
    be set manually, that doesn't make sense?),
     - if there are things like wave changed, assume people changed this manually from admin.
    """

    csv_path = tourney_result.result_file.path

    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        # try other path
        csv_path = csv_path.replace("uploads", "src/thetower/backend/uploads")

        df = pd.read_csv(csv_path)

    if df.empty:
        logging.error(f"Empty csv file: {csv_path}")
        return

    if 0 in df.columns:
        df = df.rename(columns={0: "id", 1: "tourney_name", 2: "wave"})
        # df["tourney_name"] = df["tourney_name"].map(lambda x: x.strip())  # We're stripping white space on csv save so we shouldn't need this anymore.
        df["avatar"] = df.tourney_name.map(lambda name: int(avatar[0]) if (avatar := re.findall(r"\#avatar=([-\d]+)\${5}", name)) else -1)
        df["relic"] = df.tourney_name.map(lambda name: int(relic[0]) if (relic := re.findall(r"\#avatar=\d+\${5}relic=([-\d]+)", name)) else -1)
        df["tourney_name"] = df.tourney_name.map(lambda name: name.split("#")[0])
    if "player_id" in df.columns:
        df = df.rename(columns={"player_id": "id", "name": "tourney_name", "wave": "wave"})
        # Make sure that users with all digit tourney_name's don't trick the column into being a float
        df["tourney_name"] = df["tourney_name"].astype("str")
        # df["tourney_name"] = df["tourney_name"].map(lambda x: x.strip())  # We're stripping white space on csv save so we shouldn't need this anymore.
        logging.info(f"There are {len(df.query('tourney_name.str.len() == 0'))} blank tourney names.")
        df.loc[df["tourney_name"].str.len() == 0, "tourney_name"] = df["id"]

    # Exclude suspicious and banned IDs. Also exclude shunned IDs unless the
    # per-operation shun flag (configured via include_shun.json) allows inclusion.
    # for create_tourney_rows is enabled.
    excluded_ids = get_sus_ids() | get_banned_ids()
    if not include_shun_enabled_for("create_tourney_rows"):
        excluded_ids = excluded_ids | get_shun_ids()
    positions = calculate_positions(df.id, df.index, df.wave, excluded_ids)

    df["position"] = positions

    create_data = []

    for _, row in df.iterrows():
        create_data.append(
            dict(
                player_id=row.id,
                result=tourney_result,
                nickname=row.tourney_name,
                wave=row.wave,
                position=row.position,
                avatar_id=row.avatar,
                relic_id=row.relic,
            )
        )

    TourneyRow.objects.bulk_create([TourneyRow(**data) for data in create_data])


def calculate_positions(ids: list[int], indices: list[int], waves: list[int], exclude_ids: set[int]) -> list[int]:
    """Calculate positions for tournament participants.

    Args:
        ids: List of player IDs
        indices: List of indices corresponding to player positions
        waves: List of wave numbers reached by players
        exclude_ids: Set of player IDs to exclude from position calculation

    Returns:
        List of calculated positions where excluded players get -1
    """
    positions = []
    current = 0
    borrow = 1
    last_valid_wave = None

    # Flatten list of exclude_ids if it's nested
    if any(isinstance(item, (list, set)) for item in exclude_ids):
        exclude_ids = set().union(*exclude_ids)
    else:
        exclude_ids = set(exclude_ids)

    for id_, idx, wave in zip(ids, indices, waves):
        if id_ in exclude_ids:
            positions.append(-1)
            continue

        # Compare with the last valid (non-excluded) player's wave
        if last_valid_wave is not None and wave == last_valid_wave:
            borrow += 1
        else:
            current += borrow
            borrow = 1

        positions.append(current)
        last_valid_wave = wave

    return positions


def reposition(tourney_result: TourneyResult, testrun: bool = False, verbose: bool = False) -> int:
    """Recalculates positions for tournament results and updates the database.

    Args:
        tourney_result: Tournament result to reposition
        testrun: If True, only calculate changes without updating database
        verbose: If True, log detailed position changes

    Returns:
        Number of position changes made
    """
    qs = tourney_result.rows.all().order_by("-wave")
    bulk_data = qs.values_list("player_id", "wave", "nickname")
    indexes = [idx for idx, _ in enumerate(bulk_data)]
    ids = [datum[0] for datum in bulk_data]
    waves = [datum[1] for datum in bulk_data]
    nicknames = [datum[2] for datum in bulk_data]

    # Exclude suspicious and banned IDs. Also exclude shunned IDs unless the
    # per-operation shun flag (configured via include_shun.json) allows inclusion.
    # for reposition is enabled.
    excluded_ids = get_sus_ids() | get_banned_ids()
    if not include_shun_enabled_for("reposition"):
        excluded_ids = excluded_ids | get_shun_ids()
    positions = calculate_positions(ids, indexes, waves, excluded_ids)

    bulk_update_data = []
    changes = 0

    for index, obj in enumerate(qs):
        if obj.position != positions[index]:
            changes += 1
            if verbose:
                logging.info(
                    f"Player {obj.player_id} ({nicknames[index]}) at wave {waves[index]}: "
                    f"Position changing from {obj.position} to {positions[index]}"
                )
            obj.position = positions[index]
            bulk_update_data.append(obj)

    if not testrun and bulk_update_data:
        TourneyRow.objects.bulk_update(bulk_update_data, ["position"])

    if changes:
        logging.info(f"Repositioned {changes} rows in tournament {tourney_result}")
    return changes


def get_summary(last_date: datetime.datetime) -> str:
    """Generate AI summary of tournament results.

    Args:
        last_date: Latest date to include in summary

    Returns:
        Generated summary text from AI model
    """
    logging.info("Collecting ai summary data...")

    qs = TourneyResult.objects.filter(league=legend, date__lte=last_date).order_by("-date")[:10]
    tourney_dates = list(qs.values_list("date", flat=True))
    logging.info(f"AI summary: querying {len(tourney_dates)} Legend tourneys: {tourney_dates}")

    df = get_tourneys(qs, offset=0, limit=50)
    logging.info(f"AI summary: got {len(df)} rows across {df['date'].nunique() if not df.empty else 0} dates")

    ranking = ""

    for date, sdf in df.groupby(["date"]):
        bcs = [(bc.name, bc.shortcut) for bc in sdf.iloc[0]["bcs"]]
        name_counts = sdf["real_name"].value_counts()
        dupes = name_counts[name_counts > 1]
        if not dupes.empty:
            logging.warning(f"AI summary: duplicate real_names on {date[0].isoformat()}: {dupes.to_dict()}")
        logging.info(f"AI summary: {date[0].isoformat()} — {len(sdf)} rows, top 3: {list(sdf.head(3)['real_name'])}")
        ranking += f"Tourney of {date[0].isoformat()}, battle conditions: {bcs}:\n"
        ranking += "\n".join(
            [
                f"{row.position}. {row.real_name} (tourney_name: {row.tourney_name}) - {row.wave}"
                for _, row in sdf[["position", "real_name", "tourney_name", "wave"]].iterrows()
            ]
        )
        ranking += "\n\n"

        # top1_message = Injection.objects.last().text

    logging.info(f"AI summary: full prompt ranking length = {len(ranking)} chars")
    logging.debug(f"AI summary: ranking text =\n{ranking}")

    prompt_template = PromptTemplate.objects.get(id=1).text
    text = prompt_template.format(
        ranking=ranking,
        last_date=last_date,
        top1_message="",  # deprecated, kept for template compatibility
    )

    logging.info("Starting to generate ai summary...")
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        temperature=1.0,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                    }
                ],
            }
        ],
    )

    response = message.content[0].text
    logging.info(f"AI summary done ({len(response)} chars): {response[:200]}...")

    return response


def get_time(file_path: Path) -> datetime.datetime:
    """Parse datetime from filename.

    Args:
        file_path: Path object containing timestamp in filename

    Returns:
        Parsed datetime object
    """
    stem = str(file_path.stem)
    # Handle both .csv and .csv.gz files
    if stem.endswith(".csv"):
        stem = stem[:-4]  # Remove .csv extension
    return datetime.datetime.strptime(stem, "%Y-%m-%d__%H_%M")


def get_full_brackets(df: pd.DataFrame, anti_snipe: bool = True) -> tuple[list[str], list[str]]:
    """Get bracket information from tournament data.

    Args:
        df: DataFrame containing tournament data
        anti_snipe: If True, only return brackets with >= 28 players (anti-snipe protection).
                    If False, return all brackets.

    Returns:
        Tuple containing:
        - bracket_order: List of brackets ordered by creation time
        - fullish_brackets: List of brackets (filtered by anti_snipe if enabled)
    """
    df["datetime"] = pd.to_datetime(df["datetime"])
    bracket_order = df.groupby("bracket")["datetime"].min().sort_values().index.tolist()

    if anti_snipe:
        bracket_counts = dict(df.groupby("bracket").player_id.unique().map(lambda player_ids: len(player_ids)))
        fullish_brackets = [bracket for bracket, count in bracket_counts.items() if count >= 28]
    else:
        fullish_brackets = bracket_order  # All brackets

    return bracket_order, fullish_brackets


def get_latest_live_df(league: str, shun: bool = False) -> pd.DataFrame:
    """Load only the latest non-empty live tournament CSV for a league.

    This is a slimmer alternative to `get_live_df` when callers only need the
    most recent snapshot (for example: a quick membership check).

    Args:
        league: League identifier
        shun: If True, only exclude suspicious IDs, otherwise exclude both suspicious and shunned

    Returns:
        DataFrame containing data from the latest non-empty CSV

    Raises:
        ValueError: If no current tournament data is available
    """
    t1_start = perf_counter()
    csv_data = get_csv_data()
    live_path = Path(csv_data) / "current_tourney" / league

    try:
        last_file = max((p for p in live_path.glob("*.csv.gz") if p.stat().st_size > 0), default=None)
        if last_file is None:
            raise ValueError
    except ValueError:
        # Staging is empty (post-tourney cleanup or between tourneys).
        # Fall back to the most recent archive so the cog can still serve the final state.
        archive_path = Path(csv_data) / f"{league}_live"
        archives = list_archives(archive_path)
        if not archives:
            raise ValueError("No current data, wait until the tourney day")
        archive_df = read_archive(archives[-1])
        if archive_df.empty:
            raise ValueError("No current data, wait until the tourney day")
        at = archive_df["snapshot_time"].max()
        df = reconstruct_at(archive_df, at)
        if df.empty:
            raise ValueError("No current data, wait until the tourney day")
        # Normalize all rows to the same datetime so global/bracket ranking works correctly.
        # reconstruct_at gives each player their last-updated snapshot_time; callers that
        # filter to latest_datetime == df.datetime.max() would miss players whose wave
        # didn't change in the final snapshot, producing empty global_row and no stats.
        df["datetime"] = at
        lookup = get_player_id_lookup()
        df["real_name"] = [lookup.get(pid, name) for pid, name in zip(df.player_id, df.name)]
        df["real_name"] = df["real_name"].astype(str)
        excluded_ids = get_sus_ids() | get_banned_ids()
        if not shun:
            excluded_ids = excluded_ids | get_shun_ids()
        df = df[~df.player_id.isin(excluded_ids)].reset_index(drop=True)
        logging.info(f"get_latest_live_df({league}): staging empty, fell back to archive ({archives[-1].name})")
        return df
    t_glob = perf_counter()

    last_date = get_time(last_file)

    try:
        df = pd.read_csv(last_file)
    except Exception as e:
        logging.warning(f"Failed to read latest live file {last_file}: {e}")
        raise ValueError("No current data, wait until the tourney day")
    t_read = perf_counter()

    if df.empty:
        raise ValueError("No current data, wait until the tourney day")

    df["datetime"] = last_date

    lookup = get_player_id_lookup()
    df["real_name"] = [lookup.get(id, name) for id, name in zip(df.player_id, df.name)]
    df["real_name"] = df["real_name"].astype(str)

    # Always exclude banned and suspicious IDs, optionally exclude shunned IDs
    excluded_ids = get_sus_ids() | get_banned_ids()
    if not shun:
        excluded_ids = excluded_ids | get_shun_ids()
    df = df[~df.player_id.isin(excluded_ids)]
    df = df.reset_index(drop=True)
    t1_stop = perf_counter()
    logging.info(
        f"get_latest_live_df({league}): glob={1000*(t_glob-t1_start):.0f}ms "
        f"read={1000*(t_read-t_glob):.0f}ms db={1000*(t1_stop-t_read):.0f}ms "
        f"total={1000*(t1_stop-t1_start):.0f}ms"
    )
    return df


def check_live_entry(league: str, player_id: str, fast: bool = False) -> bool:
    """Check if player has entered live tournament.

    Args:
        league: League identifier
        player_id: Player ID to check
        fast: If True, use only latest checkpoint (sufficient for participation checking since players persist in checkpoints).
              If False, use full recent data (for detailed bracket analysis).

    Returns:
        True if player has entered, False otherwise
    """
    t1_start = perf_counter()
    logging.info(f"Checking live entry for player {player_id} in {league} league (fast={fast})")

    try:
        if fast:
            # Fast path: read only the columns needed for bracket membership, and defer exclusion
            # DB queries until after confirming the player actually appears in the raw CSV.
            # If the player isn't in the file at all we return False with zero DB calls;
            # if they are, we run the sus/banned check exactly once.
            csv_data = get_csv_data()
            live_path = Path(csv_data) / "current_tourney" / league
            try:
                last_file = max((p for p in live_path.glob("*.csv.gz") if p.stat().st_size > 0), default=None)
                if last_file is None:
                    raise ValueError
            except ValueError:
                # No live checkpoints — fall back to the most recent archive file
                archive_dir = Path(csv_data) / f"{league}_live"
                archives = list_archives(archive_dir)
                if not archives:
                    raise ValueError("No current data, wait until the tourney day")
                df = read_archive(archives[-1])
                if df.empty or player_id not in df["player_id"].values:
                    return False
                excluded_ids = get_sus_ids() | get_banned_ids()
                return player_id not in excluded_ids
            t_glob = perf_counter()
            last_date = get_time(last_file)
            try:
                df = pd.read_csv(last_file, usecols=["player_id", "bracket"])
            except Exception as e:
                logging.warning(f"Failed to read latest live file {last_file}: {e}")
                raise ValueError("No current data, wait until the tourney day")
            t_read = perf_counter()
            if df.empty:
                raise ValueError("No current data, wait until the tourney day")
            df["datetime"] = last_date

            # Quick raw presence check — no DB calls if player isn't in the file at all
            if player_id not in df.player_id.values:
                logging.info(
                    f"check_live_entry({league}): not found — "
                    f"glob={1000*(t_glob-t1_start):.0f}ms read={1000*(t_read-t_glob):.0f}ms "
                    f"total={1000*(t_read-t1_start):.0f}ms"
                )
                return False

            # Player found in raw data; check sus/banned exclusion once (matches shun=True behaviour)
            excluded_ids = get_sus_ids() | get_banned_ids()
            if player_id in excluded_ids:
                return False
        else:
            t_glob = t_read = perf_counter()
            df = get_latest_live_df(league, True)

        # Use our local bracket filtering; only apply anti-snipe during ENTRY_OPEN
        anti_snipe = get_tourney_state() == TourneyState.ENTRY_OPEN
        _, fullish_brackets = get_full_brackets(df, anti_snipe=anti_snipe)

        # Check if player is in any full bracket
        filtered_df = df[df.bracket.isin(fullish_brackets)]
        player_found = player_id in filtered_df.player_id.values

        t1_stop = perf_counter()
        logging.info(
            f"check_live_entry({league}): {'found' if player_found else 'not found'} — "
            f"glob={1000*(t_glob-t1_start):.0f}ms read={1000*(t_read-t_glob):.0f}ms "
            f"total={1000*(t1_stop-t1_start):.0f}ms"
        )
        return player_found

    except (IndexError, ValueError):
        return False


def check_all_live_entry(player_id: str) -> bool:
    """Check if player has entered any live tournament.

    Args:
        player_id: Player ID to check

    Returns:
        True if player has entered any tournament, False otherwise
    """
    t1_start = perf_counter()
    for league in leagues:
        if check_live_entry(league, player_id, fast=True):
            t1_stop = perf_counter()
            logging.info(f"check_all_live_entry({player_id}): found in {league}, total={1000*(t1_stop-t1_start):.0f}ms")
            return True
    t1_stop = perf_counter()
    logging.info(f"check_all_live_entry({player_id}): not found, total={1000*(t1_stop-t1_start):.0f}ms")
    return False


def get_live_data_date() -> Optional[datetime.datetime]:
    """Return the most recent data timestamp available across all leagues.

    Checks live checkpoints first; falls back to the most recent archive file.
    Returns None if no data is found.
    """
    csv_data = get_csv_data()
    best: Optional[datetime.datetime] = None

    for league in leagues:
        live_path = Path(csv_data) / "current_tourney" / league
        try:
            last_file = max((p for p in live_path.glob("*.csv.gz") if p.stat().st_size > 0), default=None)
            if last_file is not None:
                candidate = get_time(last_file)
                if best is None or candidate > best:
                    best = candidate
                continue
        except Exception:
            pass

        # Fall back to archive
        archive_dir = Path(csv_data) / f"{league}_live"
        try:
            archives = list_archives(archive_dir)
            if archives:
                mtime = datetime.datetime.fromtimestamp(archives[-1].stat().st_mtime, tz=datetime.timezone.utc)
                if best is None or mtime > best:
                    best = mtime
        except Exception:
            pass

    return best


def load_battle_conditions() -> MappingProxyType:
    """
    Load battle conditions from the database into an immutable dictionary.
    Returns a read-only dictionary with condition shortcuts as keys and names as values.
    """
    BattleCondition = apps.get_model("tourney_results", "BattleCondition")
    conditions = {condition.shortcut: condition.name for condition in BattleCondition.objects.all()}
    return MappingProxyType(conditions)
