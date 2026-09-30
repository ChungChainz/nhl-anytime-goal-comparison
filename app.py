from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import unicodedata

import pandas as pd
import requests
import streamlit as st

API_BASE_URL = "https://api.the-odds-api.com/v4"
NHL_API_BASE_URL = "https://api-web.nhle.com/v1"
PACIFIC_TIME = ZoneInfo("America/Los_Angeles")

TARGET_BOOKS = {"draftkings", "fanduel"}

BOOK_LABELS = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
}

TEAM_CODES = {
    "Anaheim Ducks": "ANA",
    "Boston Bruins": "BOS",
    "Buffalo Sabres": "BUF",
    "Calgary Flames": "CGY",
    "Carolina Hurricanes": "CAR",
    "Chicago Blackhawks": "CHI",
    "Colorado Avalanche": "COL",
    "Columbus Blue Jackets": "CBJ",
    "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET",
    "Edmonton Oilers": "EDM",
    "Florida Panthers": "FLA",
    "Los Angeles Kings": "LAK",
    "Minnesota Wild": "MIN",
    "Montreal Canadiens": "MTL",
    "Montréal Canadiens": "MTL",
    "Nashville Predators": "NSH",
    "New Jersey Devils": "NJD",
    "New York Islanders": "NYI",
    "New York Rangers": "NYR",
    "Ottawa Senators": "OTT",
    "Philadelphia Flyers": "PHI",
    "Pittsburgh Penguins": "PIT",
    "San Jose Sharks": "SJS",
    "Seattle Kraken": "SEA",
    "St. Louis Blues": "STL",
    "Tampa Bay Lightning": "TBL",
    "Toronto Maple Leafs": "TOR",
    "Utah Mammoth": "UTA",
    "Utah Hockey Club": "UTA",
    "Vancouver Canucks": "VAN",
    "Vegas Golden Knights": "VGK",
    "Washington Capitals": "WSH",
    "Winnipeg Jets": "WPG",
}

PLAYER_NAME_ALIASES = {
    "joseph veleno": "joe veleno",
    "zachary bolduc": "zack bolduc",
    "christopher tanev": "chris tanev",
    "elias pettersson 2004": "elias pettersson",
    "egor chinakhov": "yegor chinakhov",
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


def event_is_today(event: dict) -> bool:
    game_time = datetime.fromisoformat(
        event["commence_time"].replace("Z", "+00:00")
    )
    return game_time.astimezone(PACIFIC_TIME).date() == datetime.now(
        PACIFIC_TIME
    ).date()


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


@st.cache_data(ttl=30, show_spinner=False)
def get_anytime_goal_odds(api_key: str, event_id: str):
    response = requests.get(
        f"{API_BASE_URL}/sports/icehockey_nhl/events/{event_id}/odds",
        params={
            "apiKey": api_key,
            "regions": "us",
            "bookmakers": "draftkings,fanduel",
            "markets": "player_goal_scorer_anytime",
            "oddsFormat": "american",
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json(), response.headers


@st.cache_data(ttl=3600, show_spinner=False)
def get_team_roster(team_code: str):
    response = requests.get(
        f"{NHL_API_BASE_URL}/roster/{team_code}/current",
        timeout=20,
    )
    response.raise_for_status()
    roster = response.json()

    players = []

    for group in ["forwards", "defensemen", "goalies"]:
        for player in roster.get(group, []):
            first_name = player.get("firstName", {}).get("default", "")
            last_name = player.get("lastName", {}).get("default", "")
            full_name = f"{first_name} {last_name}".strip()

            if full_name:
                players.append(normalize_name(full_name))

    return players


def build_player_team_map(home_team: str, away_team: str):
    home_players = get_team_roster(TEAM_CODES[home_team])
    away_players = get_team_roster(TEAM_CODES[away_team])

    team_map = {}

    for player in home_players:
        team_map[player] = home_team

    for player in away_players:
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
                outcome
                for outcome in market.get("outcomes", [])
                if outcome.get("name") == "Yes"
                and outcome.get("price") is not None
            ]

            pettersson_outcomes = [
                outcome
                for outcome in yes_outcomes
                if normalize_name(
                    outcome.get("description", "")
                ) == "elias pettersson"
            ]

            pettersson_rank = {
                id(outcome): rank
                for rank, outcome in enumerate(
                    sorted(
                        pettersson_outcomes,
                        key=lambda item: american_to_implied_probability(
                            item["price"]
                        ),
                        reverse=True,
                    )
                )
            }

            for outcome in yes_outcomes:
                raw_player_name = outcome.get(
                    "description",
                    "Unknown player",
                )

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
                        "Team": player_team_map.get(
                            normalize_name(raw_player_name),
                            "Unmatched",
                        ),
                        "Book Key": book_key,
                        "American Odds": odds,
                        "Implied Probability": american_to_implied_probability(
                            odds
                        ),
                    }
                )

    return pd.DataFrame(rows)


def build_player_comparison(raw_odds: pd.DataFrame) -> pd.DataFrame:
    pivot = raw_odds.pivot_table(
        index="Player",
        columns="Book Key",
        values=["American Odds", "Implied Probability"],
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
        }
    ).reset_index(drop=True)

    comparison["Difference (FD - DK)"] = (
            comparison["FD Implied %"] - comparison["DK Implied %"]
    )

    def best_book(row):
        prices = []

        if pd.notna(row["DK Implied %"]):
            prices.append(("DraftKings", row["DK Implied %"]))

        if pd.notna(row["FD Implied %"]):
            prices.append(("FanDuel", row["FD Implied %"]))

        return min(prices, key=lambda item: item[1])[0] if prices else "—"

    comparison["Best Price"] = comparison.apply(best_book, axis=1)
    comparison["Best Implied %"] = comparison[
        ["DK Implied %", "FD Implied %"]
    ].min(axis=1)

    comparison = comparison.sort_values(
        ["Best Implied %", "Player"],
        ascending=[False, True],
    )

    comparison["DK Odds"] = comparison["DK Odds"].apply(format_american_odds)
    comparison["FD Odds"] = comparison["FD Odds"].apply(format_american_odds)

    return comparison.drop(columns="Best Implied %")


def build_team_summary(
    raw_odds: pd.DataFrame,
    home_team: str,
    away_team: str,
) -> pd.DataFrame:
    summary_rows = []

    for team, side in [(home_team, "H"), (away_team, "A")]:
        team_odds = raw_odds.loc[raw_odds["Team"] == team]

        dk_odds = team_odds.loc[team_odds["Book Key"] == "draftkings"]
        fd_odds = team_odds.loc[team_odds["Book Key"] == "fanduel"]

        dk_total = dk_odds["Implied Probability"].sum()
        fd_total = fd_odds["Implied Probability"].sum()

        dk_average = dk_odds["Implied Probability"].mean()
        fd_average = fd_odds["Implied Probability"].mean()

        summary_rows.append(
            {
                "Team": f"{team} ({side})",
                "DK": dk_odds["Player"].nunique(),
                "FD": fd_odds["Player"].nunique(),
                "DK Total": dk_total,
                "FD Total": fd_total,
                "Diff": fd_total - dk_total,
                "DK Avg": dk_average,
                "FD Avg": fd_average,
                "Diff / Player": fd_average - dk_average,
            }
        )

    return pd.DataFrame(summary_rows)


def display_player_section(team_name: str, side: str, team_odds: pd.DataFrame):
    st.markdown(f"#### {team_name} ({side})")

    if team_odds.empty:
        st.info("No matched anytime-goal prices were returned for this team.")
        return

    comparison = build_player_comparison(team_odds)

    styled_comparison = (
        comparison.style
        .map(sign_color, subset=["Difference (FD - DK)"])
        .format(
            {
                "DK Implied %": "{:.2f}%",
                "FD Implied %": "{:.2f}%",
                "Difference (FD - DK)": "{:+.2f}%",
            }
        )
    )

    st.dataframe(
        styled_comparison,
        use_container_width=True,
        hide_index=True,
    )


st.set_page_config(page_title="NHL Anytime Goal Comparison", layout="wide")

st.title("NHL Anytime Goal Comparison")
st.caption("DraftKings vs FanDuel • Anytime Goal Scorer")

try:
    api_key = st.secrets["odds_api"]["api_key"].strip()
except KeyError:
    st.error("API key not found in `.streamlit/secrets.toml`.")
    st.stop()

if "last_auto_refresh" not in st.session_state:
    st.session_state.last_auto_refresh = datetime.now(PACIFIC_TIME)


@st.fragment(run_every=60)
def automatic_refresh():
    elapsed = datetime.now(PACIFIC_TIME) - st.session_state.last_auto_refresh

    if elapsed >= timedelta(seconds=60):
        st.session_state.last_auto_refresh = datetime.now(PACIFIC_TIME)
        st.rerun()


automatic_refresh()

try:
    all_events = get_nhl_events(api_key)
except requests.exceptions.RequestException as error:
    st.error(f"Could not load NHL events: {error}")
    st.stop()

today_events = [event for event in all_events if event_is_today(event)]

if not today_events:
    st.info("There are no NHL games on today’s slate.")
    st.stop()

today_events.sort(key=lambda event: (event["commence_time"], event["id"]))

event_lookup = {event["id"]: event for event in today_events}
event_ids = list(event_lookup.keys())

ALL_GAMES = "__all_games__"
event_options = [ALL_GAMES] + event_ids

remembered_event_id = st.session_state.get("selected_event_id")

selected_event_id = st.selectbox(
    "Today's NHL Games",
    options=event_options,
    index=(
        event_options.index(remembered_event_id)
        if remembered_event_id in event_options
        else 1
    ),
    key="event_selector",
    format_func=lambda event_id: (
        "All Games"
        if event_id == ALL_GAMES
        else (
            f"{event_lookup[event_id]['away_team']} @ "
            f"{event_lookup[event_id]['home_team']} — "
            f"{datetime.fromisoformat(event_lookup[event_id]['commence_time'].replace('Z', '+00:00')).astimezone(PACIFIC_TIME).strftime('%I:%M %p PT')}"
        )
    ),
)

st.session_state["selected_event_id"] = selected_event_id

player_search = st.text_input(
    "Search player",
    placeholder="Type a player name...",
).strip()

selected_event_ids = (
    event_ids
    if selected_event_id == ALL_GAMES
    else [selected_event_id]
)


def display_game(event_id: str, search_text: str) -> bool:
    try:
        odds_payload, odds_headers = get_anytime_goal_odds(
            api_key,
            event_id,
        )

        home_team = odds_payload["home_team"]
        away_team = odds_payload["away_team"]
        player_team_map = build_player_team_map(home_team, away_team)

    except KeyError:
        st.error("Could not find an NHL team code for this matchup.")
        return False

    except requests.exceptions.RequestException as error:
        st.error(f"Could not load the odds or NHL roster data: {error}")
        return False

    raw_odds = normalize_odds(odds_payload, player_team_map)

    if raw_odds.empty:
        st.warning(
            f"No DraftKings or FanDuel Anytime Goal Scorer odds were returned "
            f"for {away_team} @ {home_team}."
        )
        return False

    matched_odds = raw_odds.loc[
        raw_odds["Team"] != "Unmatched"
    ].copy()

    if search_text:
        normalized_search = normalize_name(search_text)

        matched_odds = matched_odds.loc[
            matched_odds["Player"]
            .map(normalize_name)
            .str.contains(normalized_search, case=False, na=False, regex=False)
        ].copy()

        # In All Games mode, skip games where the searched player is absent.
        if matched_odds.empty:
            return False

    st.subheader(f"{away_team} @ {home_team}")
    st.caption(
        f"Retrieved at {datetime.now(PACIFIC_TIME).strftime('%I:%M:%S %p PT')}"
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("Players", matched_odds["Player"].nunique())
    col2.metric(
        "DraftKings Prices",
        matched_odds.loc[
            matched_odds["Book Key"] == "draftkings",
            "Player",
        ].nunique(),
    )
    col3.metric(
        "FanDuel Prices",
        matched_odds.loc[
            matched_odds["Book Key"] == "fanduel",
            "Player",
        ].nunique(),
    )

    # Keep the normal team-level summary when not searching.
    if not search_text:
        st.markdown("#### TEAM SUMMARY")

        team_summary = build_team_summary(
            matched_odds,
            home_team,
            away_team,
        )

        styled_summary = (
            team_summary.style
            .map(sign_color, subset=["Diff", "Diff / Player"])
            .format(
                {
                    "DK Total": "{:.2f}%",
                    "FD Total": "{:.2f}%",
                    "Diff": "{:+.2f}%",
                    "DK Avg": "{:.2f}%",
                    "FD Avg": "{:.2f}%",
                    "Diff / Player": "{:+.2f}%",
                }
            )
        )

        st.dataframe(
            styled_summary,
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            "Positive differences are green; negative differences are red. "
            "A positive FD - DK difference means FanDuel has the better listed price."
        )
    else:
        st.caption(f"Showing results matching: {search_text}")

    home_odds = matched_odds.loc[
        matched_odds["Team"] == home_team
    ]
    away_odds = matched_odds.loc[
        matched_odds["Team"] == away_team
    ]

    if not home_odds.empty:
        display_player_section(
            home_team,
            "Home",
            home_odds,
        )

    if not away_odds.empty:
        display_player_section(
            away_team,
            "Away",
            away_odds,
        )

    if not search_text:
        unmatched_players = raw_odds.loc[
            raw_odds["Team"] == "Unmatched",
            "Player",
        ].unique()

        if len(unmatched_players) > 0:
            st.warning(
                f"{len(unmatched_players)} player(s) could not be roster-matched: "
                f"{', '.join(unmatched_players)}"
            )

    return True


games_displayed = 0

for event_id in selected_event_ids:
    if games_displayed > 0:
        st.divider()

    game_was_displayed = display_game(event_id, player_search)

    if game_was_displayed:
        games_displayed += 1

if player_search and games_displayed == 0:
    st.info(
        f'No matched anytime-goal prices found for "{player_search}" '
        "on today’s slate."
    )