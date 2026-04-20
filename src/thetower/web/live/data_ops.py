import datetime
import json
import logging
from functools import wraps
from pathlib import Path
from time import perf_counter

import pandas as pd
import streamlit as st

from thetower.backend.env_config import get_csv_data
from thetower.backend.tourney_results.archive_utils import (
    build_tourney_archive,
    group_snapshots_by_tourney,
    list_archives,
    list_snapshots,
    read_archive,
    reconstruct_all_snapshots,
)
from thetower.backend.tourney_results.data import get_banned_ids, get_player_id_lookup, get_shun_ids, get_sus_ids
from thetower.backend.tourney_results.shun_config import include_shun_enabled_for
from thetower.backend.tourney_results.tourney_utils import (
    get_full_brackets,
    get_latest_live_df,
    get_time,
    get_tourney_state,
)

logger = logging.getLogger(__name__)

# Cache configuration
CACHE_TTL_SECONDS = 300  # 5 minutes cache duration


def is_caching_disabled():
    """Check if caching should be disabled by looking for the control file."""
    root_dir = Path(__file__).parent.parent.parent
    return (root_dir / "live_cache_disabled").exists()


def cache_data_if_enabled(**cache_args):
    """Decorator that only applies st.cache_data if caching is enabled."""

    def decorator(func):
        cached_func = st.cache_data(**cache_args)(func)

        @wraps(func)
        def wrapper(*args, **kwargs):
            if is_caching_disabled():
                logging.info(f"Cache disabled for {func.__name__}")
                return func(*args, **kwargs)
            logging.info(f"Cache enabled for {func.__name__}")
            return cached_func(*args, **kwargs)

        return wrapper

    return decorator


@cache_data_if_enabled(ttl=CACHE_TTL_SECONDS)
def get_live_data(league: str, shun: bool = False) -> pd.DataFrame:
    """
    Get and cache live tournament data.

    Args:
        league: League identifier
        shun: Whether to include shunned players

    Returns:
        DataFrame containing live tournament data
    """
    t0 = perf_counter()
    archive, expected_timestamps = _load_archive_df(league)
    df = reconstruct_all_snapshots(archive, extra_timestamps=expected_timestamps)

    excluded_ids = get_sus_ids() | get_banned_ids()
    if not shun:
        excluded_ids = excluded_ids | get_shun_ids()
    df = df[~df["player_id"].isin(excluded_ids)]

    lookup = get_player_id_lookup()
    df["real_name"] = [lookup.get(pid, name) for pid, name in zip(df["player_id"], df["name"])]
    df["real_name"] = df["real_name"].astype(str)

    df = df.sort_values(["datetime", "wave"], ascending=False).reset_index(drop=True)
    logger.info(f"get_live_data({league}) took {perf_counter() - t0:.3f}s — {len(df):,} rows")
    return df


@cache_data_if_enabled(ttl=CACHE_TTL_SECONDS)
def get_processed_data(league: str, shun: bool = False):
    """
    Get processed tournament data with common transformations.

    Args:
        league: League identifier
        shun: Whether to include shunned players

    Returns:
        Tuple containing:
        - Complete DataFrame
        - Top 25 players DataFrame
        - Latest data DataFrame
        - First moment datetime
        - Last moment datetime
    """
    df = get_live_data(league, shun)

    # Common data processing
    group_by_id = df.groupby("player_id")
    top_25 = group_by_id.wave.max().sort_values(ascending=False).index[:25]
    tdf = df[df.player_id.isin(top_25)]

    first_moment = tdf.datetime.iloc[-1]
    last_moment = tdf.datetime.iloc[0]
    ldf = df[df.datetime == last_moment].copy()

    # Sort by wave descending to ensure proper position calculation
    ldf = ldf.sort_values("wave", ascending=False).reset_index(drop=True)

    # Calculate positions accounting for ties (same wave = same position)
    positions = []
    current = 0
    borrow = 1
    last_wave = None

    for wave in ldf["wave"]:
        if last_wave is not None and wave == last_wave:
            borrow += 1
        else:
            current += borrow
            borrow = 1
        positions.append(current)
        last_wave = wave

    ldf.index = positions

    return df, tdf, ldf, first_moment, last_moment


@cache_data_if_enabled(ttl=CACHE_TTL_SECONDS)
def get_bracket_data(df: pd.DataFrame):
    """
    Process bracket-specific data.

    Args:
        df: DataFrame containing tournament data

    Returns:
        Tuple containing:
        - bracket_order: List of brackets ordered by creation time
        - fullish_brackets: List of brackets (filtered based on tournament state)
    """
    # Check tournament state - only apply anti-snipe protection during ENTRY_OPEN
    tourney_state = get_tourney_state()
    anti_snipe = tourney_state.name == "ENTRY_OPEN"

    return get_full_brackets(df, anti_snipe=anti_snipe)


def process_display_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    Process display names by adding player_id to duplicated names.

    Args:
        df: DataFrame containing player data

    Returns:
        DataFrame with processed display names
    """
    # Create a copy to avoid warnings
    result = df.copy()

    # Calculate name duplicates
    name_counts = result.groupby("real_name")["player_id"].nunique()
    duplicate_names = name_counts[name_counts > 1].index

    # Create display_name column using loc
    result.loc[:, "display_name"] = result["real_name"]

    if len(duplicate_names) > 0:
        # Add player_id to duplicated names
        mask = result["real_name"].isin(duplicate_names)
        result.loc[mask, "display_name"] = result.loc[mask, "real_name"] + " (" + result.loc[mask, "player_id"].astype(str) + ")"

    return result


@cache_data_if_enabled(ttl=CACHE_TTL_SECONDS)
def get_placement_analysis_data(league: str):
    """
    Get processed data specifically for placement analysis.

    Args:
        league: League identifier

    Returns:
        Tuple containing:
        - DataFrame
        - Latest time
        - Bracket creation times dict
    """
    # Respect filesystem/JSON flag: include shunned players when configured
    # for live_placement_cache (shared by live_placement_analysis and live_quantile_analysis)
    include_shun = include_shun_enabled_for("live_placement_cache")

    # Try to use per-tourney cache: snapshots live in current_tourney/{league}/
    # and placement cache files live in {league}_live/ (permanent location).
    try:
        csv_data = get_csv_data()
        logging.info(f"get_placement_analysis_data: CSV_DATA={csv_data!s}")
        staging_path = Path(csv_data) / "current_tourney" / league
        cache_dir = Path(csv_data) / f"{league}_live"
        logging.info(f"get_placement_analysis_data: staging_path={staging_path}")

        all_files = sorted([p for p in staging_path.glob("*.csv.gz") if p.stat().st_size > 0], key=get_time)
        logging.info(f"get_placement_analysis_data: found {len(all_files)} non-empty CSV snapshots in {staging_path}")

        if all_files:
            last_file = all_files[-1]
            logging.info(f"get_placement_analysis_data: last_file={last_file}")
            last_time = get_time(last_file)
            tourney_date = last_time.date().isoformat()
            logging.info(f"get_placement_analysis_data: last_time={last_time}, initial tourney_date={tourney_date}")

            # Placement cache files are written using the tourney start date.
            # Pick the most recent cache file in the cache dir; the start date
            # can be several days before the latest snapshot (tournaments run 4-5 days).
            sorted_caches = sorted(cache_dir.glob("*_placement_cache.json"), key=lambda p: p.name, reverse=True)
            cache_file = sorted_caches[0] if sorted_caches else None
            found_date = cache_file.name.replace("_placement_cache.json", "") if cache_file else None
            logging.info(f"get_placement_analysis_data: selected cache_file={cache_file} (found_date={found_date})")

            if cache_file:
                logging.info(f"get_placement_analysis_data: cache_file exists: {cache_file}")
                try:
                    payload_text = cache_file.read_text(encoding="utf8")
                    logging.info(f"get_placement_analysis_data: cache_file size={len(payload_text)} bytes")
                    payload = json.loads(payload_text)

                    payload_include_shun = payload.get("include_shun")
                    logging.info(f"get_placement_analysis_data: payload include_shun={payload_include_shun}, desired include_shun={include_shun}")

                    # only accept cache if include_shun matches what we're using
                    if payload_include_shun == include_shun:
                        # Ensure the cache was generated against the latest snapshot.
                        # If the cache refers to a different snapshot than the
                        # latest available CSV, refuse to use it and surface a
                        # friendly message so the UI doesn't mix stale cache
                        # metadata with newer CSVs.
                        payload_snapshot = payload.get("snapshot_iso")
                        if payload_snapshot:
                            try:
                                payload_snapshot_name = Path(payload_snapshot).name
                            except Exception:
                                payload_snapshot_name = str(payload_snapshot)
                        else:
                            payload_snapshot_name = None

                        last_snapshot_name = last_file.name
                        logging.info(
                            f"get_placement_analysis_data: payload snapshot name={payload_snapshot_name}, latest snapshot name={last_snapshot_name}"
                        )

                        if payload_snapshot_name != last_snapshot_name:
                            logging.warning("get_placement_analysis_data: cache snapshot does not match latest CSV; refusing to use stale cache")
                            # Surface an actionable message to the UI via ValueError
                            raise ValueError("Live Placement Analysis is lagging behind live data.  Please wait while we catch up.")
                        # Parse bracket_creation_times (stored as ISO strings) back to datetimes
                        raw_times = payload.get("bracket_creation_times", {}) or {}
                        bracket_creation_times = {
                            br: (datetime.datetime.fromisoformat(ts) if isinstance(ts, str) else ts) for br, ts in raw_times.items()
                        }
                        logging.info(f"get_placement_analysis_data: parsed {len(bracket_creation_times)} bracket_creation_times from cache")

                        # Load only latest snapshot to build the live DataFrame for analysis
                        df_latest = get_latest_live_df(league, include_shun)
                        logging.info(f"get_placement_analysis_data: df_latest.shape={getattr(df_latest, 'shape', None)}")

                        # compute fullish brackets from latest snapshot
                        bracket_counts = dict(df_latest.groupby("bracket").player_id.unique().map(lambda player_ids: len(player_ids)))
                        fullish_brackets = [bracket for bracket, count in bracket_counts.items() if count >= 28]
                        logging.info(f"get_placement_analysis_data: found {len(fullish_brackets)} fullish_brackets (>=28 players)")

                        df = df_latest[df_latest.bracket.isin(fullish_brackets)].copy()
                        df["real_name"] = df["real_name"].astype("str")
                        latest_time = df["datetime"].max()
                        logging.info(f"get_placement_analysis_data: filtered df.shape={getattr(df, 'shape', None)}, latest_time={latest_time}")

                        logging.info(f"Using placement cache for {league} {tourney_date}")
                        # Return the tourney start date (the cache is keyed by start date)
                        tourney_start_date = found_date or tourney_date
                        return df, latest_time, bracket_creation_times, tourney_start_date
                    else:
                        logging.info("get_placement_analysis_data: cache include_shun mismatch; rejecting cache")
                except Exception:
                    logging.exception(f"Failed to read/parse placement cache {cache_file}; will fall back to raising ValueError")
            else:
                logging.info(f"get_placement_analysis_data: cache_file does not exist: {cache_file}")

        else:
            # No staging snapshots — fall back to the most recent placement cache in cache_dir.
            # get_latest_live_df itself falls back to the archive when staging is empty.
            logging.info(f"get_placement_analysis_data: no staging snapshots; searching for most recent cache in {cache_dir}")
            cache_files = sorted(cache_dir.glob("*_placement_cache.json"), key=lambda p: p.name, reverse=True)
            if cache_files:
                fallback_cache = cache_files[0]
                found_date = fallback_cache.name.replace("_placement_cache.json", "")
                logging.info(f"get_placement_analysis_data: archive fallback using cache {fallback_cache.name}")
                try:
                    payload = json.loads(fallback_cache.read_text(encoding="utf8"))
                    payload_include_shun = payload.get("include_shun")
                    if payload_include_shun == include_shun:
                        raw_times = payload.get("bracket_creation_times", {}) or {}
                        bracket_creation_times = {
                            br: (datetime.datetime.fromisoformat(ts) if isinstance(ts, str) else ts) for br, ts in raw_times.items()
                        }
                        df_latest = get_latest_live_df(league, include_shun)
                        bracket_counts = dict(df_latest.groupby("bracket").player_id.unique().map(lambda player_ids: len(player_ids)))
                        fullish_brackets = [bracket for bracket, count in bracket_counts.items() if count >= 28]
                        df = df_latest[df_latest.bracket.isin(fullish_brackets)].copy()
                        df["real_name"] = df["real_name"].astype("str")
                        latest_time = df["datetime"].max()
                        logging.info(f"get_placement_analysis_data: archive fallback succeeded, df.shape={df.shape}")
                        return df, latest_time, bracket_creation_times, found_date
                    else:
                        logging.info("get_placement_analysis_data: archive fallback cache include_shun mismatch")
                except Exception:
                    logging.exception("get_placement_analysis_data: archive fallback cache read failed")
            else:
                logging.info(f"get_placement_analysis_data: no placement cache files found in {cache_dir}")

    except Exception:
        logging.exception("Failed to check placement cache path or list snapshots; will fall back to raising ValueError")

    # If we reach here, no placement cache was available or it was invalid.
    # Per product decision: do not fall back to on-the-fly aggregation. Let the
    # caller handle a missing cache (the Streamlit page will show a friendly
    # message via the require_tournament_data decorator).
    raise ValueError("Placement cache not available yet; try again later")


def analyze_wave_placement(df, wave_to_analyze, latest_time):
    """
    Analyze placement of a specific wave across all brackets.

    Args:
        df: DataFrame containing tournament data
        wave_to_analyze: Wave number to analyze
        latest_time: Latest time point in the data

    Returns:
        List of dictionaries containing placement analysis results
    """
    results = []
    for bracket in sorted(df["bracket"].unique()):
        bracket_df = df[df["bracket"] == bracket]
        last_bracket_df = bracket_df[bracket_df["datetime"] == latest_time].sort_values("wave", ascending=False)

        # Tie rule: tied players all receive the LAST (worst) position in the tied group.
        # above_count = players strictly better; equal_count = players tied at the same wave.
        # The analyzed wave is treated as a new entrant joining any existing tie, so
        # rank = above_count + equal_count + 1 (last slot in the combined tied group).
        above_count = last_bracket_df[last_bracket_df["wave"] > wave_to_analyze].shape[0]
        equal_count = last_bracket_df[last_bracket_df["wave"] == wave_to_analyze].shape[0]
        total = last_bracket_df.shape[0]
        rank = above_count + equal_count + 1

        results.append(
            {
                "Bracket": bracket,
                "Would Place": f"{rank}/{total}",
                "Top Wave": last_bracket_df["wave"].max(),
                "Median Wave": int(last_bracket_df["wave"].median()),
                "Players Above": above_count,
            }
        )

    return results


def process_bracket_selection(df, selected_real_name, selected_player_id, selected_bracket, bracket_order):
    """
    Process bracket selection logic from different input methods.

    Args:
        df: DataFrame containing tournament data
        selected_real_name: Selected player name
        selected_player_id: Selected player ID
        selected_bracket: Directly selected bracket
        bracket_order: List of brackets in order

    Returns:
        Tuple containing:
        - bracket_id: Selected bracket ID
        - tdf: DataFrame filtered for selected bracket
        - selected_real_name: Resolved player name
        - bracket_index: Index of bracket in bracket_order
    """
    try:
        if selected_bracket:
            bracket_id = selected_bracket
            tdf = df[df.bracket == bracket_id]
            if tdf.empty:
                raise ValueError(f"Bracket {bracket_id} not found")
            selected_real_name = tdf.real_name.iloc[0]
            bracket_index = bracket_order.index(bracket_id)
        elif selected_player_id:
            player_match = df[df.player_id == selected_player_id]
            if player_match.empty:
                raise ValueError(f"Player ID {selected_player_id} not found")
            selected_real_name = player_match.real_name.iloc[0]
            sdf = df[df.real_name == selected_real_name]
            bracket_id = sdf.bracket.iloc[0]
            tdf = df[df.bracket == bracket_id]
            bracket_index = bracket_order.index(bracket_id)
        elif selected_real_name:
            # Support partial matching for player names
            name_lower = selected_real_name.lower()
            # Try matching on real_name and name (tourney name) columns
            sdf = df[df.real_name.str.lower().str.contains(name_lower, na=False, regex=False)]
            # Also check tourney name if the column exists
            if "name" in df.columns:
                sdf_alt = df[df["name"].str.lower().str.contains(name_lower, na=False, regex=False)]
                sdf = pd.concat([sdf, sdf_alt]).drop_duplicates()
            if sdf.empty:
                raise ValueError(f"Player '{selected_real_name}' not found")
            # Check for multiple unique players
            unique_players = sdf.real_name.unique()
            if len(unique_players) > 1:
                # Multiple matches found - create a special exception with the matches
                # Note: This doesn't include league info since it's within a single league's data
                matches_str = ", ".join(sorted(unique_players))
                raise ValueError(f"MULTIPLE_MATCHES:{matches_str}")
            bracket_id = sdf.bracket.iloc[0]
            selected_real_name = sdf.real_name.iloc[0]
            tdf = df[df.bracket == bracket_id]
            bracket_index = bracket_order.index(bracket_id)
        return bracket_id, tdf, selected_real_name, bracket_index
    except Exception as e:
        raise ValueError(f"Selection not found: {str(e)}")


def get_bracket_stats(df):
    """
    Calculate bracket statistics.

    Args:
        df: DataFrame containing tournament data

    Returns:
        Dictionary containing bracket statistics, including salt/spoon ranking brackets
        (hardest/easiest promotion and relegation by 5th/25th place wave).
    """

    def _promotion_cutoff_wave(group, n):
        """Wave of the last player to actually promote under the last-in-tie rank rule.

        Tied players all share the worst rank in their group. This returns the minimum
        wave whose entire tied group still fits within the top n slots. Returns None if
        nobody in the bracket promotes (e.g. > n players all tied at the top wave).
        """
        sorted_counts = group.value_counts().sort_index(ascending=False)
        cumulative = 0
        result = None
        for wave, cnt in sorted_counts.items():
            if cumulative + cnt <= n:
                cumulative += cnt
                result = wave
            else:
                break
        return result

    def _relegation_cutoff_wave(group, n):
        """Wave of the highest-ranked player who demotes under the last-in-tie rank rule.

        Returns the first wave (going highest to lowest) where the cumulative player
        count reaches n — those players' last-in-tie rank >= n, so they demote.
        Returns None if fewer than n players in the bracket.
        """
        sorted_counts = group.value_counts().sort_index(ascending=False)
        cumulative = 0
        for wave, cnt in sorted_counts.items():
            cumulative += cnt
            if cumulative >= n:
                return wave
        return None

    group_by_bracket = df.groupby("bracket").wave

    fourth_place = group_by_bracket.apply(lambda x: _promotion_cutoff_wave(x, 4)).dropna()
    twenty_fifth_place = group_by_bracket.apply(lambda x: _relegation_cutoff_wave(x, 25)).dropna()

    stats = {
        "total_brackets": df.groupby("bracket").ngroups,
        "highest_total": group_by_bracket.sum().sort_values(ascending=False).index[0],
        "highest_median": group_by_bracket.median().sort_values(ascending=False).index[0],
        "lowest_total": group_by_bracket.sum().sort_values(ascending=True).index[0],
        "lowest_median": group_by_bracket.median().sort_values(ascending=True).index[0],
    }

    if len(fourth_place) > 0:
        stats["hardest_promotion"] = fourth_place.idxmax()
        stats["hardest_promotion_wave"] = int(fourth_place.max())
        stats["easiest_promotion"] = fourth_place.idxmin()
        stats["easiest_promotion_wave"] = int(fourth_place.min())
    else:
        stats["hardest_promotion"] = stats["easiest_promotion"] = None
        stats["hardest_promotion_wave"] = stats["easiest_promotion_wave"] = None

    if len(twenty_fifth_place) > 0:
        stats["hardest_relegation"] = twenty_fifth_place.idxmax()
        stats["hardest_relegation_wave"] = int(twenty_fifth_place.max())
        stats["easiest_relegation"] = twenty_fifth_place.idxmin()
        stats["easiest_relegation_wave"] = int(twenty_fifth_place.min())
    else:
        stats["hardest_relegation"] = stats["easiest_relegation"] = None
        stats["hardest_relegation_wave"] = stats["easiest_relegation_wave"] = None

    return stats


@cache_data_if_enabled(ttl=CACHE_TTL_SECONDS)
def get_quantile_analysis_data(league: str):
    """
    Get pre-computed quantile data for placement analysis.

    Args:
        league: League identifier

    Returns:
        Tuple containing:
        - quantile_df: DataFrame with columns: rank, quantile, waves
        - tourney_start_date: Tournament start date string
        - latest_time: Latest timestamp from the data
    """
    # Respect filesystem/JSON flag: shared setting for all placement cache pages
    include_shun = include_shun_enabled_for("live_placement_cache")

    # Try to use per-tourney cache: snapshots live in current_tourney/{league}/
    # and placement cache files live in {league}_live/ (permanent location).
    try:
        csv_data = get_csv_data()
        logging.info(f"get_quantile_analysis_data: CSV_DATA={csv_data!s}")
        staging_path = Path(csv_data) / "current_tourney" / league
        cache_dir = Path(csv_data) / f"{league}_live"
        logging.info(f"get_quantile_analysis_data: staging_path={staging_path}")

        all_files = sorted([p for p in staging_path.glob("*.csv.gz") if p.stat().st_size > 0], key=get_time)
        logging.info(f"get_quantile_analysis_data: found {len(all_files)} non-empty CSV snapshots in {staging_path}")

        if all_files:
            last_file = all_files[-1]
            logging.info(f"get_quantile_analysis_data: last_file={last_file}")
            last_time = get_time(last_file)
            tourney_date = last_time.date().isoformat()
            logging.info(f"get_quantile_analysis_data: last_time={last_time}, initial tourney_date={tourney_date}")

            # Pick the most recent cache file in the cache dir; the start date
            # can be several days before the latest snapshot (tournaments run 4-5 days).
            sorted_caches = sorted(cache_dir.glob("*_placement_cache.json"), key=lambda p: p.name, reverse=True)
            cache_file = sorted_caches[0] if sorted_caches else None
            found_date = cache_file.name.replace("_placement_cache.json", "") if cache_file else None
            logging.info(f"get_quantile_analysis_data: selected cache_file={cache_file} (found_date={found_date})")

            if cache_file:
                logging.info(f"get_quantile_analysis_data: cache_file exists: {cache_file}")
                try:
                    payload_text = cache_file.read_text(encoding="utf8")
                    logging.info(f"get_quantile_analysis_data: cache_file size={len(payload_text)} bytes")
                    payload = json.loads(payload_text)

                    payload_include_shun = payload.get("include_shun")
                    logging.info(f"get_quantile_analysis_data: payload include_shun={payload_include_shun}, desired include_shun={include_shun}")

                    # Only accept cache if include_shun matches
                    if payload_include_shun == include_shun:
                        # Check if cache has quantile data
                        quantile_data = payload.get("quantile_data")
                        if not quantile_data or not quantile_data.get("data"):
                            logging.warning("get_quantile_analysis_data: cache exists but has no quantile_data")
                            raise ValueError("Quantile analysis cache is being generated. Please wait a moment and refresh.")

                        # Verify cache snapshot matches latest
                        payload_snapshot = payload.get("snapshot_iso")
                        if payload_snapshot:
                            try:
                                payload_snapshot_name = Path(payload_snapshot).name
                            except Exception:
                                payload_snapshot_name = str(payload_snapshot)
                        else:
                            payload_snapshot_name = None

                        last_snapshot_name = last_file.name
                        logging.info(
                            f"get_quantile_analysis_data: payload snapshot name={payload_snapshot_name}, latest snapshot name={last_snapshot_name}"
                        )

                        if payload_snapshot_name != last_snapshot_name:
                            logging.warning("get_quantile_analysis_data: cache snapshot does not match latest CSV")
                            raise ValueError("Quantile analysis is catching up with live data. Please wait a moment and refresh.")

                        # Convert quantile data to DataFrame
                        data = quantile_data.get("data", {})

                        results = []
                        for rank_str, rank_quantiles in data.items():
                            rank = int(rank_str)
                            for q_str, wave_value in rank_quantiles.items():
                                q = float(q_str)
                                if wave_value is not None:
                                    results.append({"rank": rank, "quantile": q, "waves": wave_value})

                        if not results:
                            logging.warning("get_quantile_analysis_data: quantile_data exists but no valid results")
                            raise ValueError("Quantile analysis cache is empty. Please wait for data generation.")

                        quantile_df = pd.DataFrame(results)

                        # Get latest timestamp from a quick CSV read
                        df_latest = get_latest_live_df(league, include_shun)
                        latest_time = df_latest["datetime"].max()

                        tourney_start_date = found_date or tourney_date
                        logging.info(f"Using quantile cache for {league} {tourney_start_date}")
                        return quantile_df, tourney_start_date, latest_time
                    else:
                        logging.info("get_quantile_analysis_data: cache include_shun mismatch; rejecting cache")
                except Exception:
                    logging.exception(f"Failed to read/parse quantile cache {cache_file}")
            else:
                logging.info("get_quantile_analysis_data: cache_file does not exist")

        else:
            # No staging snapshots — fall back to the most recent placement cache in cache_dir.
            logging.info(f"get_quantile_analysis_data: no staging snapshots; searching for most recent cache in {cache_dir}")
            cache_files = sorted(cache_dir.glob("*_placement_cache.json"), key=lambda p: p.name, reverse=True)
            if cache_files:
                fallback_cache = cache_files[0]
                found_date = fallback_cache.name.replace("_placement_cache.json", "")
                logging.info(f"get_quantile_analysis_data: archive fallback using cache {fallback_cache.name}")
                try:
                    payload = json.loads(fallback_cache.read_text(encoding="utf8"))
                    payload_include_shun = payload.get("include_shun")
                    if payload_include_shun == include_shun:
                        quantile_data = payload.get("quantile_data")
                        if not quantile_data or not quantile_data.get("data"):
                            logging.warning("get_quantile_analysis_data: archive fallback cache has no quantile_data")
                        else:
                            data = quantile_data.get("data", {})
                            results = []
                            for rank_str, rank_quantiles in data.items():
                                rank = int(rank_str)
                                for q_str, wave_value in rank_quantiles.items():
                                    q = float(q_str)
                                    if wave_value is not None:
                                        results.append({"rank": rank, "quantile": q, "waves": wave_value})
                            if results:
                                quantile_df = pd.DataFrame(results)
                                df_latest = get_latest_live_df(league, include_shun)
                                latest_time = df_latest["datetime"].max()
                                logging.info(f"get_quantile_analysis_data: archive fallback succeeded for {found_date}")
                                return quantile_df, found_date, latest_time
                            else:
                                logging.warning("get_quantile_analysis_data: archive fallback cache has no valid results")
                    else:
                        logging.info("get_quantile_analysis_data: archive fallback cache include_shun mismatch")
                except Exception:
                    logging.exception("get_quantile_analysis_data: archive fallback cache read failed")
            else:
                logging.info(f"get_quantile_analysis_data: no placement cache files found in {cache_dir}")

    except Exception:
        logging.exception("Failed to check quantile cache path or list snapshots")

    # No cache available - show friendly message
    raise ValueError("Quantile analysis cache not available yet. Please wait for cache generation and try again later.")


def handle_no_data():
    """Standard handler for when no tournament data is available"""
    st.info("No current data, wait until the tourney day")
    return None


def require_tournament_data(func):
    """Decorator to handle cases where tournament data is not available"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (IndexError, ValueError) as e:
            # Show a helpful message from the exception (e.g., cache missing)
            try:
                st.info(str(e))
            except Exception:
                # Fallback to generic handler if Streamlit is not available
                return handle_no_data()
            return None

    return wrapper


@cache_data_if_enabled(ttl=CACHE_TTL_SECONDS)
def get_cached_plot_data(df):
    """Process dataframe for plotting, with better error handling."""
    plot_data = df.copy()

    # Only convert datetime if the column exists
    if "datetime" in plot_data.columns:
        plot_data["datetime"] = pd.to_datetime(plot_data["datetime"])

    return plot_data


def initialize_bracket_state(bracket_order, league):
    """Initialize or update bracket navigation state"""
    bracket_key = f"current_bracket_idx_{league}"
    if bracket_key not in st.session_state:
        st.session_state[bracket_key] = 0

    return st.session_state[bracket_key]


def update_bracket_index(new_index, max_index, league):
    """Update bracket navigation index with bounds checking"""
    bracket_key = f"current_bracket_idx_{league}"
    st.session_state[bracket_key] = max(0, min(new_index, max_index))


def clear_cache():
    """Clear all cached data in Streamlit"""
    st.cache_data.clear()


@cache_data_if_enabled(ttl=CACHE_TTL_SECONDS)
def get_data_refresh_timestamp(league: str) -> datetime.datetime | None:
    """
    Get the timestamp of the most recent data refresh for the given league.

    Args:
        league: League identifier

    Returns:
        datetime object representing when data was last refreshed, or None if no data
    """
    try:
        snaps = list_snapshots(_get_snapshot_path(league))
        if snaps:
            return get_time(snaps[-1])
        # Fall back to the latest timestamp in the most recent archive file.
        archives = list_archives(_get_archive_path(league))
        if archives:
            df = read_archive(archives[-1])
            if not df.empty:
                ts = df["snapshot_time"].max()
                return ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        return None
    except Exception as exc:
        logging.warning(f"Failed to get data refresh timestamp for {league}: {exc}")
        return None


def format_time_ago(timestamp: datetime.datetime) -> str:
    """
    Format a timestamp as a human-readable "time ago" string.

    Args:
        timestamp: datetime object to format (assumed to be UTC if timezone-naive)

    Returns:
        Human-readable string like "5 minutes ago", "2 hours ago", etc.
    """
    if not timestamp:
        return "Unknown"

    # Get current time in UTC
    now = datetime.datetime.now(datetime.timezone.utc)

    # If timestamp is timezone-naive, assume it's UTC
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=datetime.timezone.utc)

    # Calculate time difference
    time_diff = now - timestamp

    if time_diff.days > 0:
        if time_diff.days == 1:
            return "1 day ago"
        return f"{time_diff.days} days ago"
    elif time_diff.seconds >= 3600:
        hours = time_diff.seconds // 3600
        if hours == 1:
            return "1 hour ago"
        return f"{hours} hours ago"
    elif time_diff.seconds >= 60:
        minutes = time_diff.seconds // 60
        if minutes == 1:
            return "1 minute ago"
        return f"{minutes} minutes ago"
    else:
        return "Just now"


# ── archive helpers ───────────────────────────────────────────────────────────


def _get_snapshot_path(league: str) -> Path:
    """Current-tourney staging directory: where live snapshots are written."""
    return Path(get_csv_data()) / "current_tourney" / league


def _get_archive_path(league: str) -> Path:
    """Permanent archive directory: where ``*_archive.csv.gz`` files are stored."""
    return Path(get_csv_data()) / f"{league}_live"


def _load_archive_df(league: str) -> tuple[pd.DataFrame, list]:
    """Return (archive_df, expected_timestamps) for the current/most-recent tourney.

    Strategy:
    1. Determine the current tourney group from staging snapshots.
    2. Look for ``{date}_archive.csv.gz`` in the permanent archive directory.
    3. If found and newer than the last snapshot, read it directly.
    4. Otherwise, build in memory on the fly.
    """
    from thetower.backend.tourney_results.archive_utils import _archive_name_for_group, _parse_snapshot_time  # noqa: PLC0415

    snap_path = _get_snapshot_path(league)
    archive_dir = _get_archive_path(league)

    snaps = list_snapshots(snap_path)
    if not snaps:
        # No staging snapshots present (operator may have cleaned them up).
        # Attempt to fall back to the most recent pre-built archive file.
        try:
            archives = list_archives(archive_dir)
            if archives:
                arch_file = archives[-1]
                logger.info(f"No staging snapshots; falling back to archive {arch_file.name}")
                df = read_archive(arch_file)
                expected_timestamps = list(df["snapshot_time"].unique()) if not df.empty else []
                return df, expected_timestamps
        except Exception:
            logger.exception("Failed to read fallback archive; no current data available")
        raise ValueError("No current data, wait until the tourney day")

    groups = group_snapshots_by_tourney(snaps)
    current_group = groups[-1] if groups else snaps
    expected_timestamps = [_parse_snapshot_time(p) for p in current_group]

    arch_file = archive_dir / _archive_name_for_group(current_group)
    if arch_file.exists() and arch_file.stat().st_size > 0:
        if arch_file.stat().st_mtime >= current_group[-1].stat().st_mtime:
            logger.info(f"Reading pre-built archive {arch_file.name}")
            return read_archive(arch_file), expected_timestamps
        logger.info(f"Archive {arch_file.name} is stale; rebuilding from {len(current_group)} snapshots")

    logger.info(f"No archive file found for {league}; building in memory from {len(current_group)} snapshots")
    archive = build_tourney_archive(current_group)
    if archive.empty:
        raise ValueError("No current data, wait until the tourney day")
    return archive, expected_timestamps
