from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import math
import unicodedata

import pandas as pd
import requests
import streamlit as st

API_BASE_URL = "https://api.the-odds-api.com/v4"
NHL_API_BASE_URL = "https://api-web.nhle.com/v1"
PACIFIC_TIME = ZoneInfo("America/Los_Angeles")

TARGET_BOOKS = {"draftkings", "fanduel"}
BOOK_LABELS = {"draftkings": "DraftKings", "fanduel": "FanDuel"}

TEAM_CODES = {
    "Anaheim Ducks": "ANA", "Boston Bruins": "BOS", "Buffalo Sabres": "BUF",
    "Calgary Flames": "CGY", "Carolina Hurricanes": "CAR", "Chicago Blackhawks": "CHI",
    "Colorado Avalanche": "COL", "Columbus Blue Jackets": "CBJ", "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET", "Edmonton Oilers": "EDM", "Florida Panthers": "FLA",
    "Los Angeles Kings": "LAK", "Minnesota Wild": "MIN", "Montreal Canadiens": "MTL",
    "Montréal Canadiens": "MTL", "Nashville Predators": "NSH", "New Jersey Devils": "NJD",
    "New York Islanders": "NYI", "New York Rangers": "NYR", "Ottawa Senators": "OTT",
    "Philadelphia Flyers": "PHI", "Pittsburgh Penguins": "PIT", "San Jose Sharks": "SJS",
    "Seattle Kraken": "SEA", "St Louis Blues": "STL", "Tampa Bay Lightning": "TBL",
    "Toronto Maple Leafs": "TOR", "Utah Mammoth": "UTA", "Utah Hockey Club": "UTA",
    "Vancouver Canucks": "VAN", "Vegas Golden Knights": "VGK",
    "Washington Capitals": "WSH", "Winnipeg Jets": "WPG",
}

PLAYER_NAME_ALIASES = {
    "joseph veleno": "joe veleno",
    "zachary bolduc": "zack bolduc",
    "christopher tanev": "chris tanev",
    "elias pettersson 2004": "elias pettersson",
    "egor chinakhov": "yegor chinakhov",
    "dmitry simashev": "dmitri simashev",
    "charles-alexis legault": "charles alexis legault",
    "charlesalexis legault": "charles alexis legault",
    "alex wennberg": "alexander wennberg",
}


def american_to_implied_probability(odds: int) -> float:
    if odds > 0:
        return 100 / (odds + 100) * 100
    return abs(odds) / (abs(odds) + 100) * 100


def format_american_odds(odds) -> str:
    if pd.isna(odds):
        return "—"
    return f"{int(odds):+d}"


def normalize_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = "".join(
        character for character in normalized.lower()
        if character.isalnum() or character.isspace()
    )
    normalized = " ".join(normalized.split())
    return PLAYER_NAME_ALIASES.get(normalized, normalized)


def event_start_time(event: dict) -> datetime:
    game_time = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
    return game_time.astimezone(PACIFIC_TIME)


def event_is_on_date(event: dict, date) -> bool:
    return event_start_time(event).date() == date


def event_is_pregame(event: dict) -> bool:
    return event_start_time(event) > datetime.now(PACIFIC_TIME)


def sign_color(value):
    if pd.isna(value):
        return ""
    if value > 0:
        return "color: #16c784; font-weight: 700;"
    if value < 0:
        return "color: #ff4b4b; font-weight: 700;"
    return ""


@st.cache_data(ttl=60, show_spinner=False)
def get_nhl_events(api_key: str):
    response = requests.get(
        f"{API_BASE_URL}/sports/icehockey_nhl/events",
        params={"apiKey": api_key},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=300, show_spinner=False)
def get_anytime_goal_odds(api_key: str, event_id: str):
    response = requests.get(
        f"{API_BASE_URL}/sports/icehockey_nhl/events/{event_id}/odds",
        params={
            "apiKey": api_key,
            "regions": "us",
            "bookmakers": "draftkings,fanduel",
            "markets": "player_goal_scorer_anytime,alternate_team_totals",
            "oddsFormat": "american",
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json(), response.headers


@st.cache_data(ttl=3600, show_spinner=False)
def get_team_roster(team_code: str):
    response = requests.get(f"{NHL_API_BASE_URL}/roster/{team_code}/current", timeout=20)
    response.raise_for_status()
    players = []
    for group in ["forwards", "defensemen", "goalies"]:
        for player in response.json().get(group, []):
            first_name = player.get("firstName", {}).get("default", "")
            last_name = player.get("lastName", {}).get("default", "")
            full_name = f"{first_name} {last_name}".strip()
            if full_name:
                players.append(normalize_name(full_name))
    return players


def build_player_team_map(home_team: str, away_team: str):
    team_map = {}
    for player in get_team_roster(TEAM_CODES[home_team]):
        team_map[player] = home_team
    for player in get_team_roster(TEAM_CODES[away_team]):
        team_map[player] = away_team
    return team_map


def normalize_odds(odds_payload: dict, player_team_map: dict) -> pd.DataFrame:
    rows = []
    for bookmaker in odds_payload.get("bookmakers", []):
        book_key = bookmaker.get("key")
        if book_key not in TARGET_BOOKS:
            continue
        for market in bookmaker.get("markets", []):
            if market.get("key") != "player_goal_scorer_anytime":
                continue
            yes_outcomes = [
                outcome for outcome in market.get("outcomes", [])
                if outcome.get("name") == "Yes" and outcome.get("price") is not None
            ]
            pettersson_outcomes = [
                outcome for outcome in yes_outcomes
                if normalize_name(outcome.get("description", "")) == "elias pettersson"
            ]
            pettersson_rank = {
                id(outcome): rank
                for rank, outcome in enumerate(
                    sorted(
                        pettersson_outcomes,
                        key=lambda item: american_to_implied_probability(item["price"]),
                        reverse=True,
                    )
                )
            }
            for outcome in yes_outcomes:
                raw_player_name = outcome.get("description", "Unknown player")
                player_name = raw_player_name
                if (
                    normalize_name(raw_player_name) == "elias pettersson"
                    and len(pettersson_outcomes) > 1
                ):
                    player_name = (
                        "Elias Pettersson (F)"
                        if pettersson_rank[id(outcome)] == 0
                        else "Elias Pettersson (D)"
                    )
                odds = outcome["price"]
                rows.append(
                    {
                        "Player": player_name,
                        "Team": player_team_map.get(normalize_name(raw_player_name), "Unmatched"),
                        "Book Key": book_key,
                        "American Odds": odds,
                        "Implied Probability": american_to_implied_probability(odds),
                    }
                )
    return pd.DataFrame(rows)


def outcome_team(outcome: dict, home_team: str, away_team: str):
    description = outcome.get("description", "")
    normalized_description = normalize_name(description)
    for team in [home_team, away_team]:
        normalized_team = normalize_name(team)
        if normalized_description == normalized_team or normalized_team in normalized_description:
            return team
    return None


def extract_team_totals(odds_payload: dict, home_team: str, away_team: str) -> pd.DataFrame:
    rows = []

    for bookmaker in odds_payload.get("bookmakers", []):
        book_key = bookmaker.get("key")
        if book_key not in TARGET_BOOKS:
            continue

        for market in bookmaker.get("markets", []):
            market_key = market.get("key")

            if market_key not in {"team_totals", "alternate_team_totals"}:
                continue

            for outcome in market.get("outcomes", []):
                side = outcome.get("name")
                price = outcome.get("price")
                point = outcome.get("point")
                team = outcome_team(outcome, home_team, away_team)

                if side not in {"Over", "Under"} or price is None or point is None or team is None:
                    continue

                rows.append(
                    {
                        "Team": team,
                        "Book Key": book_key,
                        "Market Key": market_key,
                        "Side": side,
                        "Line": float(point),
                        "American Odds": price,
                        "Raw Probability": american_to_implied_probability(price) / 100,
                    }
                )

    return pd.DataFrame(rows)


def poisson_probability_at_least(goal_rate: float, goals: int) -> float:
    probability = math.exp(-goal_rate)
    cumulative_probability = probability
    for goal in range(1, goals):
        probability *= goal_rate / goal
        cumulative_probability += probability
    return 1 - cumulative_probability


def implied_goals_from_team_total(line: float, fair_over_probability: float):
    # Half-goal lines avoid a push and can be directly inverted into a Poisson rate.
    if abs(line - round(line)) < 0.001:
        return float("nan")
    threshold = math.floor(line) + 1
    low, high = 0.0, 10.0
    for _ in range(80):
        midpoint = (low + high) / 2
        if poisson_probability_at_least(midpoint, threshold) < fair_over_probability:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2


def interpolate_missing_side_probability(
    market_rows: pd.DataFrame,
    target_line: float,
    missing_side: str,
) -> float:
    """Estimate a missing Over/Under probability from adjacent offered lines."""
    side_rows = (
        market_rows.loc[
            market_rows["Side"] == missing_side,
            ["Line", "Raw Probability"],
        ]
        .drop_duplicates(subset=["Line"])
        .sort_values("Line")
    )

    lower = side_rows.loc[side_rows["Line"] < target_line].tail(1)
    upper = side_rows.loc[side_rows["Line"] > target_line].head(1)

    # Do not extrapolate: only fill a gap with a line on each side.
    if lower.empty or upper.empty:
        return float("nan")

    lower_line = float(lower["Line"].iloc[0])
    lower_probability = float(lower["Raw Probability"].iloc[0])
    upper_line = float(upper["Line"].iloc[0])
    upper_probability = float(upper["Raw Probability"].iloc[0])

    weight = (target_line - lower_line) / (upper_line - lower_line)

    return lower_probability + weight * (
        upper_probability - lower_probability
    )


def build_team_total_implied_goals(team_totals: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Team",
        "Book Key",
        "Team Total",
        "Fair Over %",
        "Market Implied Goals",
    ]

    if team_totals.empty:
        return pd.DataFrame(columns=columns)

    rows = []

    # Keep each sportsbook and market separate while completing missing sides.
    for (team, book_key, market_key), market_rows in team_totals.groupby(
        ["Team", "Book Key", "Market Key"]
    ):
        for line, line_rows in market_rows.groupby("Line"):
            over = line_rows.loc[
                line_rows["Side"] == "Over", "Raw Probability"
            ]
            under = line_rows.loc[
                line_rows["Side"] == "Under", "Raw Probability"
            ]

            over_probability = (
                float(over.iloc[0]) if not over.empty else float("nan")
            )
            under_probability = (
                float(under.iloc[0]) if not under.empty else float("nan")
            )

            # If one side is missing, estimate it from the nearest same-side
            # total below and above this line.
            if pd.isna(over_probability):
                over_probability = interpolate_missing_side_probability(
                    market_rows,
                    float(line),
                    "Over",
                )

            if pd.isna(under_probability):
                under_probability = interpolate_missing_side_probability(
                    market_rows,
                    float(line),
                    "Under",
                )

            # Skip a line if it still cannot be completed safely.
            if pd.isna(over_probability) or pd.isna(under_probability):
                continue

            fair_over_probability = over_probability / (
                over_probability + under_probability
            )

            rows.append(
                {
                    "Team": team,
                    "Book Key": book_key,
                    "Market Key": market_key,
                    "Team Total": float(line),
                    "Fair Over %": fair_over_probability * 100,
                    "Market Implied Goals": implied_goals_from_team_total(
                        float(line),
                        fair_over_probability,
                    ),
                }
            )

    if not rows:
        return pd.DataFrame(columns=columns)

    rates = pd.DataFrame(rows)

    # Prefer alternate team totals: they are the detailed board that gives us
    # the closest balanced line, with team_totals as a fallback.
    rates["source_priority"] = rates["Market Key"].ne(
        "alternate_team_totals"
    ).astype(int)

    rates["balance_distance"] = (
        rates["Fair Over %"] / 100 - 0.5
    ).abs()

    rates = (
        rates.sort_values(
            ["Team", "Book Key", "source_priority", "balance_distance"]
        )
        .drop_duplicates(["Team", "Book Key"], keep="first")
        .reset_index(drop=True)
    )
    return rates[columns]

def add_player_implied_goals(raw_odds: pd.DataFrame, team_goal_rates: pd.DataFrame) -> pd.DataFrame:
    odds = raw_odds.copy()
    probabilities = (odds["Implied Probability"] / 100).clip(upper=0.999999)
    odds["Scorer Derived xG"] = probabilities.map(
        lambda probability: -math.log1p(-probability)
    )

    scorer_totals = (
        odds.loc[odds["Team"] != "Unmatched"]
        .groupby(["Team", "Book Key"])["Scorer Derived xG"]
        .sum()
        .to_dict()
    )
    market_rates = (
        team_goal_rates.set_index(["Team", "Book Key"])["Market Implied Goals"].to_dict()
        if not team_goal_rates.empty
        else {}
    )

    def calibrated_xg(row):
        key = (row["Team"], row["Book Key"])
        scorer_total = scorer_totals.get(key)
        market_rate = market_rates.get(key)
        if scorer_total is None or pd.isna(market_rate) or scorer_total <= 0:
            return row["Scorer Derived xG"]
        return row["Scorer Derived xG"] / scorer_total * market_rate

    odds["Player Implied Goals"] = odds.apply(calibrated_xg, axis=1)
    return odds


def build_player_comparison(raw_odds: pd.DataFrame) -> pd.DataFrame:
    pivot = raw_odds.pivot_table(
        index="Player",
        columns="Book Key",
        values=["American Odds", "Implied Probability", "Player Implied Goals"],
        aggfunc="first",
    )

    def get_column(metric: str, book: str):
        if (metric, book) in pivot.columns:
            return pivot[(metric, book)]
        return pd.Series(index=pivot.index, dtype="float64")

    comparison = pd.DataFrame(
        {
            "Player": pivot.index,
            "DK Odds": get_column("American Odds", "draftkings"),
            "FD Odds": get_column("American Odds", "fanduel"),
            "DK Implied %": get_column("Implied Probability", "draftkings"),
            "FD Implied %": get_column("Implied Probability", "fanduel"),
            "DK xG": get_column("Player Implied Goals", "draftkings"),
            "FD xG": get_column("Player Implied Goals", "fanduel"),
        }
    ).reset_index(drop=True)
    comparison["Difference (FD - DK)"] = comparison["FD Implied %"] - comparison["DK Implied %"]
    comparison["xG Diff (FD - DK)"] = comparison["FD xG"] - comparison["DK xG"]

    def best_book(row):
        prices = []
        if pd.notna(row["DK Implied %"]):
            prices.append(("DraftKings", row["DK Implied %"]))
        if pd.notna(row["FD Implied %"]):
            prices.append(("FanDuel", row["FD Implied %"]))
        return min(prices, key=lambda item: item[1])[0] if prices else "—"

    comparison["Best Price"] = comparison.apply(best_book, axis=1)
    comparison["Best Implied %"] = comparison[["DK Implied %", "FD Implied %"]].min(axis=1)
    comparison = comparison.sort_values(["Best Implied %", "Player"], ascending=[False, True])
    comparison["DK Odds"] = comparison["DK Odds"].apply(format_american_odds)
    comparison["FD Odds"] = comparison["FD Odds"].apply(format_american_odds)
    return comparison.drop(columns="Best Implied %")


def build_team_summary(raw_odds: pd.DataFrame, team_goal_rates: pd.DataFrame, home_team: str, away_team: str):
    rate_lookup = (
        team_goal_rates.set_index(["Team", "Book Key"]).to_dict("index")
        if not team_goal_rates.empty
        else {}
    )
    summary_rows = []
    for team, side in [(home_team, "H"), (away_team, "A")]:
        row = {"Team": f"{team} ({side})"}
        for book_key, prefix in [("draftkings", "DK"), ("fanduel", "FD")]:
            book_odds = raw_odds.loc[(raw_odds["Team"] == team) & (raw_odds["Book Key"] == book_key)]
            rate_data = rate_lookup.get((team, book_key), {})
            team_xg = rate_data.get("Market Implied Goals", float("nan"))
            scorer_xg = book_odds["Scorer Derived xG"].sum() if not book_odds.empty else float("nan")
            row[f"{prefix} Players"] = book_odds["Player"].nunique()
            row[f"{prefix} Team xG"] = team_xg
            row[f"{prefix} Scorer xG"] = scorer_xg
            row[f"{prefix} Gap"] = scorer_xg - team_xg if pd.notna(team_xg) else float("nan")
        row["Team xG Diff (FD - DK)"] = row["FD Team xG"] - row["DK Team xG"]
        summary_rows.append(row)
    return pd.DataFrame(summary_rows)


def display_player_section(team_name: str, side: str, team_odds: pd.DataFrame):
    st.markdown(f"#### {team_name} ({side})")
    if team_odds.empty:
        st.info("No matched anytime-goal prices were returned for this team.")
        return
    comparison = build_player_comparison(team_odds)
    styled_comparison = (
        comparison.style
        .map(sign_color, subset=["Difference (FD - DK)", "xG Diff (FD - DK)"])
        .format(
            {
                "DK Implied %": "{:.2f}%",
                "FD Implied %": "{:.2f}%",
                "Difference (FD - DK)": "{:+.2f}%",
                "DK xG": "{:.3f}",
                "FD xG": "{:.3f}",
                "xG Diff (FD - DK)": "{:+.3f}",
            }
        )
    )
    st.dataframe(styled_comparison, use_container_width=True, hide_index=True)


st.set_page_config(page_title="NHL Anytime Goal Comparison", layout="wide")
st.title("NHL Anytime Goal Comparison")
st.caption("DraftKings vs FanDuel • Anytime Goal Scorer • Market-Implied Goals")

try:
    api_key = st.secrets["odds_api"]["api_key"].strip()
except KeyError:
    st.error("API key not found in `.streamlit/secrets.toml`.")
    st.stop()

if "last_auto_refresh" not in st.session_state:
    st.session_state.last_auto_refresh = datetime.now(PACIFIC_TIME)


@st.fragment(run_every=300)
def automatic_refresh():
    elapsed = datetime.now(PACIFIC_TIME) - st.session_state.last_auto_refresh
    if elapsed >= timedelta(seconds=300):
        st.session_state.last_auto_refresh = datetime.now(PACIFIC_TIME)
        st.rerun()


automatic_refresh()


ALL_GAMES = "__all_games__"
PRICE_GAP_LIMIT = 20
PRICE_GAP_COLUMNS = [
    "Player", "Team", "Game", "DK Odds", "FD Odds",
    "DK Implied %", "FD Implied %", "Difference (FD - DK)",
]


def format_event_label(event: dict) -> str:
    return (
        f"{event['away_team']} @ {event['home_team']} — "
        f"{event_start_time(event).strftime('%I:%M %p PT')}"
    )


def load_game_odds(event_id: str):
    odds_payload, _ = get_anytime_goal_odds(api_key, event_id)
    home_team = odds_payload["home_team"]
    away_team = odds_payload["away_team"]
    player_team_map = build_player_team_map(home_team, away_team)

    raw_odds = normalize_odds(odds_payload, player_team_map)
    if raw_odds.empty:
        return home_team, away_team, raw_odds, pd.DataFrame()

    team_totals = extract_team_totals(odds_payload, home_team, away_team)
    team_goal_rates = build_team_total_implied_goals(team_totals)
    raw_odds = add_player_implied_goals(raw_odds, team_goal_rates)
    return home_team, away_team, raw_odds, team_goal_rates


def display_game(event_id: str, search_text: str) -> bool:
    try:
        home_team, away_team, raw_odds, team_goal_rates = load_game_odds(event_id)
    except KeyError as error:
        st.error(f"Could not find an NHL team code for this matchup. Missing key: {error}")
        return False
    except requests.exceptions.RequestException as error:
        st.error(f"Could not load the odds or NHL roster data: {error}")
        return False

    if raw_odds.empty:
        st.warning(f"No DraftKings or FanDuel Anytime Goal Scorer odds were returned for {away_team} @ {home_team}.")
        return False

    matched_odds = raw_odds.loc[raw_odds["Team"] != "Unmatched"].copy()

    if search_text:
        matched_odds = matched_odds.loc[
            matched_odds["Player"].map(normalize_name).str.contains(
                normalize_name(search_text), case=False, na=False, regex=False
            )
        ].copy()
        if matched_odds.empty:
            return False

    st.subheader(f"{away_team} @ {home_team}")
    st.caption(f"Retrieved at {datetime.now(PACIFIC_TIME).strftime('%I:%M:%S %p PT')}")
    col1, col2, col3 = st.columns(3)
    col1.metric("Players", matched_odds["Player"].nunique())
    col2.metric("DraftKings Prices", matched_odds.loc[matched_odds["Book Key"] == "draftkings", "Player"].nunique())
    col3.metric("FanDuel Prices", matched_odds.loc[matched_odds["Book Key"] == "fanduel", "Player"].nunique())

    if not search_text:
        st.markdown("#### TEAM IMPLIED GOALS")
        team_summary = build_team_summary(matched_odds, team_goal_rates, home_team, away_team)
        styled_summary = (
            team_summary.style
            .map(sign_color, subset=["DK Gap", "FD Gap", "Team xG Diff (FD - DK)"])
            .format(
                {
                    "DK Team xG": "{:.2f}", "FD Team xG": "{:.2f}",
                    "DK Scorer xG": "{:.2f}", "FD Scorer xG": "{:.2f}",
                    "DK Gap": "{:+.2f}", "FD Gap": "{:+.2f}",
                    "Team xG Diff (FD - DK)": "{:+.2f}",
                }
            )
        )
        st.dataframe(styled_summary, use_container_width=True, hide_index=True)
        if team_goal_rates.empty:
            st.info("DK and FD did not return full-game team totals for this matchup. Player xG is shown from anytime scorer prices only.")
        else:
            st.caption("Team xG is no-vig implied from the half-goal team total. Scorer xG comes from the anytime scorer board. Player xG is calibrated to Team xG when a team total is available.")
    else:
        st.caption(f"Showing results matching: {search_text}")

    home_odds = matched_odds.loc[matched_odds["Team"] == home_team]
    away_odds = matched_odds.loc[matched_odds["Team"] == away_team]
    if not home_odds.empty:
        display_player_section(home_team, "Home", home_odds)
    if not away_odds.empty:
        display_player_section(away_team, "Away", away_odds)

    if not search_text:
        unmatched_players = raw_odds.loc[raw_odds["Team"] == "Unmatched", "Player"].unique()
        if len(unmatched_players) > 0:
            st.warning(f"{len(unmatched_players)} player(s) could not be roster-matched: {', '.join(unmatched_players)}")
    return True


def render_slate(events: list, selector_label: str, key_prefix: str, slate_name: str):
    if not events:
        st.info(f"There are no upcoming NHL games on {slate_name}.")
        return

    event_lookup = {event["id"]: event for event in events}
    event_ids = list(event_lookup.keys())
    event_options = event_ids
    remembered_key = f"{key_prefix}_selected_event_id"
    remembered_event_id = st.session_state.get(remembered_key)

    selected_event_id = st.selectbox(
        selector_label,
        options=event_options,
        index=event_options.index(remembered_event_id) if remembered_event_id in event_options else 0,
        key=f"{key_prefix}_event_selector",
        format_func=lambda event_id: (
            "All Games" if event_id == ALL_GAMES else format_event_label(event_lookup[event_id])
        ),
    )
    st.session_state[remembered_key] = selected_event_id

    search_key = f"{key_prefix}_player_search"
    player_search = st.text_input(
        "Search player",
        value=st.session_state.get(f"{search_key}_value", ""),
        placeholder="Type a player name...",
        key=search_key,
    ).strip()
    st.session_state[f"{search_key}_value"] = player_search
    selected_event_ids = [selected_event_id]

    games_displayed = 0
    for event_id in selected_event_ids:
        if games_displayed > 0:
            st.divider()
        if display_game(event_id, player_search):
            games_displayed += 1

    if player_search and games_displayed == 0:
        st.info(f'No matched anytime-goal prices found for "{player_search}" on {slate_name}.')

def render_overnight_slate(events: list, selector_label: str):
    if not events:
        st.info("There are no upcoming NHL games on tomorrow’s slate.")
        return

    event_lookup = {event["id"]: event for event in events}
    event_ids = list(event_lookup.keys())

    selected_event_id = st.selectbox(
        selector_label,
        options=event_ids,
        index=0,
        key="overnight_event_selector",
        format_func=lambda event_id: format_event_label(event_lookup[event_id]),
    )

    st.caption("Select one game, then load its odds. This makes one event request.")

    if st.button("Load Overnight Odds", type="primary", key="load_overnight_odds"):
        display_game(selected_event_id, "")

def build_price_gaps(events: list):
    frames = []
    failed_games = []
    for event in events:
        try:
            home_team, away_team, raw_odds, _ = load_game_odds(event["id"])
        except (KeyError, requests.exceptions.RequestException):
            failed_games.append(f"{event['away_team']} @ {event['home_team']}")
            continue
        if raw_odds.empty:
            continue

        comparison = build_player_comparison(raw_odds)
        player_teams = raw_odds.drop_duplicates("Player").set_index("Player")["Team"]
        comparison["Team"] = comparison["Player"].map(player_teams).map(lambda team: TEAM_CODES.get(team, team))
        comparison["Game"] = f"{TEAM_CODES.get(away_team, away_team)} @ {TEAM_CODES.get(home_team, home_team)}"
        frames.append(comparison)

    if not frames:
        return pd.DataFrame(columns=PRICE_GAP_COLUMNS), failed_games

    # Only players priced at both books can have a DK-vs-FD gap.
    gaps = pd.concat(frames, ignore_index=True).dropna(subset=["DK Implied %", "FD Implied %"])
    return gaps[PRICE_GAP_COLUMNS], failed_games


def display_price_gap_table(gaps: pd.DataFrame):
    styled_gaps = (
        gaps.style
        .map(sign_color, subset=["Difference (FD - DK)"])
        .format(
            {
                "DK Implied %": "{:.2f}%",
                "FD Implied %": "{:.2f}%",
                "Difference (FD - DK)": "{:+.2f}%",
            }
        )
    )
    st.dataframe(styled_gaps, use_container_width=True, hide_index=True)

#Game Check for NHL Games
def render_price_gaps(slates: dict):
    slate_name = st.radio("Slate", options=list(slates.keys()), horizontal=True, key="price_gap_slate")
    events = slates[slate_name]
    if not events:
        st.info(f"There are no upcoming NHL games on {slate_name.lower()}’s slate.")
        return

    gaps, failed_games = build_price_gaps(events)
    if failed_games:
        st.warning(f"Could not load odds for: {', '.join(failed_games)}")
    if gaps.empty:
        st.info("No players currently have both a DraftKings and a FanDuel anytime-goal price.")
        return

    st.caption(
        f"{len(events)} game(s) • {len(gaps)} players priced at both books • "
        f"Retrieved at {datetime.now(PACIFIC_TIME).strftime('%I:%M:%S %p PT')}"
    )

    # FD - DK > 0 means FanDuel implies a higher probability, i.e. DK is the longer price.
    dk_longer = gaps.loc[gaps["Difference (FD - DK)"] > 0].nlargest(PRICE_GAP_LIMIT, "Difference (FD - DK)")
    dk_shorter = gaps.loc[gaps["Difference (FD - DK)"] < 0].nsmallest(PRICE_GAP_LIMIT, "Difference (FD - DK)")

    st.markdown(f"#### DK Priced Longer Than FD (Top {PRICE_GAP_LIMIT})")
    if dk_longer.empty:
        st.info("No players where DraftKings is longer than FanDuel.")
    else:
        display_price_gap_table(dk_longer)

    st.markdown(f"#### DK Priced Shorter Than FD (Top {PRICE_GAP_LIMIT})")
    if dk_shorter.empty:
        st.info("No players where DraftKings is shorter than FanDuel.")
    else:
        display_price_gap_table(dk_shorter)


try:
    all_events = get_nhl_events(api_key)
except requests.exceptions.RequestException as error:
    st.error(f"Could not load NHL events: {error}")
    st.stop()

today = datetime.now(PACIFIC_TIME).date()
tomorrow = today + timedelta(days=1)
all_events = sorted(all_events, key=lambda event: (event["commence_time"], event["id"]))
# Games drop off Today once their listed start time passes, so only pregame matchups remain.
today_events = [event for event in all_events if event_is_on_date(event, today) and event_is_pregame(event)]
tomorrow_events = [event for event in all_events if event_is_on_date(event, tomorrow)]

# on_change="rerun" makes tabs lazy, so only the open tab spends Odds API requests.
today_tab, price_gaps_tab, overnight_tab = st.tabs(
    ["Today", "Price Gaps", "Overnight"],
    key="main_tabs",
    on_change="rerun",
)

if today_tab.open:
    with today_tab:
        render_slate(today_events, "Today's NHL Games", "today", "today’s slate")

if price_gaps_tab.open:
    with price_gaps_tab:
        render_price_gaps({"Today": today_events, "Tomorrow": tomorrow_events})

if overnight_tab.open:
    with overnight_tab:
        st.markdown(f"### Tomorrow's Slate — {tomorrow.strftime('%a %m/%d')}")
        render_overnight_slate(
            tomorrow_events,
            f"Tomorrow's NHL Games ({tomorrow.strftime('%m/%d')})",
        )
