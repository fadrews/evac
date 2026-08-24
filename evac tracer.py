# Wildfire Evacuation Decision Simulator
# Version 2.6.3 - interface readability and compact assessment revision
#
# Version 2.6 introduces a versioned result schema, control-file hashing,
# atomic result writes, strict preparation-time limits, stable assessment IDs,
# explicit simulated decision times, and improved interaction-state logging.
# Version 2.6.2 adds non-reactive opportunity-set and decision-state-change
# logging. These records are for later analysis only and do not warn, verify,
# constrain, or otherwise alter participant decisions.
# Version 2.6.3 retains the 2.6.2 scenario and schema while introducing a
# neutral, research-appropriate visual system and a compact 0-10 assessment
# scale. The scale specification is written into every result and assessment
# submission so that 2.6.2 and 2.6.3 scores cannot be confused in analysis.

import streamlit as st
import json
import datetime
from datetime import timedelta
import hashlib
import uuid
import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path

# Set wide layout for the fixed 4 x 4 information-source grid.
st.set_page_config(
    page_title="Wildfire Evacuation Scenario",
    layout="wide",
    initial_sidebar_state="collapsed"
)


# ======================================================
# 1. LOAD CONTROL FILE
# ======================================================
APP_VERSION = "2.6.3"
RESULT_SCHEMA_VERSION = "3.2"
CONTROL_PATH = Path("control.json")

ASSESSMENT_SCALE_MIN = 0
ASSESSMENT_SCALE_MAX = 10
ASSESSMENT_SCALE_DEFAULT = 5
ASSESSMENT_SCALE_STEP = 1


def load_control():
    """Load the current control file without a stale Streamlit data cache."""
    if not CONTROL_PATH.exists():
        st.error("Error: 'control.json' not found.")
        st.stop()
    with CONTROL_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


CONTROL = load_control()
CONTROL_SHA256 = hashlib.sha256(CONTROL_PATH.read_bytes()).hexdigest()
TITLE = CONTROL.get("title", "Research Scenario")
SCENARIO_ID = CONTROL.get("scenario_id")
SCENARIO_VERSION = str(CONTROL.get("scenario_version", "unspecified"))
TIME_STEPS = CONTROL.get("time_steps", [])
TIME_STEP_MINUTES = int(CONTROL.get("time_step_minutes", 60))
TILES = CONTROL.get("tiles", {})
PREP_ACTIONS = CONTROL.get("preparation_actions", [])

# Simulated preparation-time budget for each scenario step.
DECISION_TIME = CONTROL.get("decision_time", {})
NOMINAL_MINUTES = int(DECISION_TIME.get("nominal_minutes", 60))
MAX_MINUTES = int(DECISION_TIME.get("maximum_minutes", 70))

if MAX_MINUTES < NOMINAL_MINUTES:
    st.error("control.json error: maximum_minutes must be >= nominal_minutes.")
    st.stop()

if TIME_STEP_MINUTES <= 0:
    st.error("control.json error: time_step_minutes must be greater than zero.")
    st.stop()

if NOMINAL_MINUTES > TIME_STEP_MINUTES:
    st.error(
        "control.json error: nominal_minutes cannot exceed the allocated "
        "time_step_minutes."
    )
    st.stop()

if MAX_MINUTES > TIME_STEP_MINUTES:
    st.error(
        "control.json error: maximum_minutes cannot exceed the allocated "
        "time_step_minutes. Set maximum_minutes to 60 for the original "
        "60-minute scenario."
    )
    st.stop()

if not TIME_STEPS:
    st.error("control.json error: time_steps must contain at least one step.")
    st.stop()

if SCENARIO_ID != "original_wildfire":
    st.error(
        "control.json error: wildfire_simulator_v2_6.py is currently limited "
        "to scenario_id 'original_wildfire'. Use the matching revised original "
        "control file. The Iron Fire scenario is intentionally excluded."
    )
    st.stop()

ACTION_IDS = [action.get("id") for action in PREP_ACTIONS]
if len(ACTION_IDS) != len(set(ACTION_IDS)) or any(not action_id for action_id in ACTION_IDS):
    st.error("control.json error: preparation action IDs must be present and unique.")
    st.stop()

ACTION_ID_SET = set(ACTION_IDS)
for action in PREP_ACTIONS:
    prerequisites = action.get("prerequisites", [])
    invalid_prerequisites = [
        prerequisite_id
        for prerequisite_id in prerequisites
        if prerequisite_id not in ACTION_ID_SET or prerequisite_id == action["id"]
    ]
    if invalid_prerequisites:
        st.error(
            "control.json error: action "
            f"'{action['id']}' has invalid prerequisites: "
            f"{invalid_prerequisites}."
        )
        st.stop()

ASSESSMENT_VARIABLES = [
    {
        "id": "perceived_threat",
        "prompt": "How much threat does the wildfire currently pose to you and your family?",
        "low_anchor": "no threat",
        "high_anchor": "very high threat"
    },
    {
        "id": "decision_time_pressure",
        "prompt": "How much decision time pressure do you feel?",
        "low_anchor": "no time pressure",
        "high_anchor": "extreme time pressure"
    },
    {
        "id": "trust_official_alerts",
        "prompt": "What is your trust in official alerts?",
        "low_anchor": "no trust",
        "high_anchor": "very high trust"
    },
    {
        "id": "anxiety",
        "prompt": "How anxious are you in this situation?",
        "low_anchor": "no anxiety",
        "high_anchor": "very high anxiety"
    },
    {
        "id": "social_pressure",
        "prompt": "How much social pressure do you experience?",
        "low_anchor": "no pressure",
        "high_anchor": "extreme social pressure"
    },
    {
        "id": "household_readiness",
        "prompt": "How ready are you and your household to leave immediately?",
        "low_anchor": "not ready",
        "high_anchor": "completely ready"
    },
    {
        "id": "route_feasibility",
        "prompt": "How feasible and safe is evacuation using the currently available routes?",
        "low_anchor": "not feasible or safe",
        "high_anchor": "completely feasible and safe"
    },
    {
        "id": "evacuation_leaning",
        "prompt": "Which direction are you currently leaning?",
        "low_anchor": "strongly leaning to stay",
        "high_anchor": "strongly leaning to evacuate"
    }
]

DECISION_PROCESS_STATES = {
    "continue_normal": "Continue normal activities",
    "monitor": "Monitor conditions",
    "prepare": "Prepare or continue preparing to evacuate",
    "ready_waiting": "Ready to evacuate and waiting for a trigger",
    "evacuate_now": "Evacuate now"
}


# ======================================================
# 2. SESSION STATE INITIALIZATION
# ======================================================
def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def init_state():
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.session_schema_version = RESULT_SCHEMA_VERSION
        st.session_state.session_app_version = APP_VERSION
        st.session_state.session_started_at_utc = utc_now_iso()
        st.session_state.session_completed_at_utc = None
        st.session_state.completion_status = "active"
        st.session_state.event_sequence = 0
        st.session_state.logs = []
        st.session_state.results_delivery_status = "not_attempted"
        st.session_state.results_delivery_error = None

        # flow
        st.session_state.consent_given = False
        st.session_state.contact_collected = False
        st.session_state.show_intro = True
        st.session_state.scenario_ended = False
        st.session_state.final_choice = None
        st.session_state.scenario_end_reason = None

        # time
        st.session_state.time_index = 0

        # dashboard
        st.session_state.open_tile = None
        st.session_state.tiles_opened_this_step = set()
        st.session_state.viewed_updates = set()
        st.session_state.dashboard_start_perf = None
        st.session_state.current_phase = None

        # phase flags
        st.session_state.in_assessment = False
        st.session_state.in_decision = False

        # timing
        st.session_state.assessment_start_perf = None
        st.session_state.decision_start_perf = None
        st.session_state.tile_open_perf = None
        st.session_state.current_tile_id = None
        st.session_state.current_tile_instance_id = None
        st.session_state.current_tile_display_logged = False
        st.session_state.tile_view_sequence_this_step = 0

        # social timing
        st.session_state.current_social_contact = None
        st.session_state.social_open_perf = None
        st.session_state.current_social_instance_id = None

        # assessment cache
        st.session_state.cached_assessment = None
        st.session_state.cached_assessment_interactions = None
        st.session_state.assessment_interactions = {}
        st.session_state.decision_process_state = None
        st.session_state.decision_process_state_interactions = {}
        st.session_state.decision_screen_presented_steps = set()
        st.session_state.scenario_step_started_steps = set()

        # preparation
        # Non-repeatable actions that have been completed at any prior time.
        st.session_state.completed_prep_actions = set()

        # Ordered list of action IDs performed during the current scenario step.
        # Repeatable actions may occur again in a later step, but only once per step.
        st.session_state.prep_actions_this_step = []

        # Simulated minutes consumed by preparation actions in the current step.
        st.session_state.prep_minutes_this_step = 0

        # Total number of times each action has been performed across the scenario.
        st.session_state.prep_action_counts = {}


def ensure_new_state_defaults():
    """Support browser sessions that were already open when the app was updated."""
    if "prep_actions_this_step" not in st.session_state:
        st.session_state.prep_actions_this_step = []
    if "prep_minutes_this_step" not in st.session_state:
        st.session_state.prep_minutes_this_step = 0
    if "prep_action_counts" not in st.session_state:
        st.session_state.prep_action_counts = {}
    if "completed_prep_actions" not in st.session_state:
        st.session_state.completed_prep_actions = set()
    if "final_choice" not in st.session_state:
        st.session_state.final_choice = None
    if "scenario_end_reason" not in st.session_state:
        st.session_state.scenario_end_reason = None
    if "decision_process_state_interactions" not in st.session_state:
        st.session_state.decision_process_state_interactions = {}
    if "decision_screen_presented_steps" not in st.session_state:
        st.session_state.decision_screen_presented_steps = set()
    if "scenario_step_started_steps" not in st.session_state:
        st.session_state.scenario_step_started_steps = set()


def reset_incompatible_session_state():
    """Never mix browser state from incompatible schemas or app versions."""
    if (
        "session_id" in st.session_state
        and (
            st.session_state.get("session_schema_version") != RESULT_SCHEMA_VERSION
            or st.session_state.get("session_app_version") != APP_VERSION
        )
    ):
        st.session_state.clear()
        st.rerun()


reset_incompatible_session_state()
init_state()
ensure_new_state_defaults()

st.markdown("""
<style>
:root {
    --wf-ink: #233142;
    --wf-muted: #5f6f7f;
    --wf-primary: #315f7d;
    --wf-primary-dark: #244b65;
    --wf-surface: #ffffff;
    --wf-background: #f4f6f8;
    --wf-border: #d8e0e7;
}

[data-testid="stAppViewContainer"] {
    background: var(--wf-background);
}

.block-container {
    max-width: 1320px;
    padding-top: 1.4rem;
    padding-bottom: 3rem;
}

h1, h2, h3, h4 {
    color: var(--wf-ink);
    letter-spacing: -0.015em;
}

p, li, label {
    color: var(--wf-ink);
}

div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--wf-surface);
    border-color: var(--wf-border) !important;
    border-radius: 12px;
    box-shadow: 0 1px 3px rgba(35, 49, 66, 0.05);
}

div[data-testid="stMetric"] {
    background: var(--wf-surface);
    border: 1px solid var(--wf-border);
    border-radius: 10px;
    padding: 0.75rem 1rem;
}

div[data-testid="stAlert"] {
    border-radius: 10px;
}

.stButton > button {
    min-height: 2.75rem;
    border-radius: 8px;
    font-weight: 600;
    padding: 0.55rem 1rem;
}

.stButton > button[kind="primary"] {
    background: var(--wf-primary);
    border-color: var(--wf-primary);
}

.stButton > button[kind="primary"]:hover {
    background: var(--wf-primary-dark);
    border-color: var(--wf-primary-dark);
}

/* Scope fixed-height, left-aligned styling to information-source tiles. */
div[class*="st-key-tile_"] button {
    width: 100%;
    min-height: 4rem;
    padding: 0.7rem 0.85rem;
    text-align: left;
    justify-content: flex-start;
    white-space: normal;
    line-height: 1.25;
    background: var(--wf-surface);
    border-color: var(--wf-border);
}

div[class*="st-key-tile_"] button:hover:not(:disabled) {
    border-color: var(--wf-primary);
    color: var(--wf-primary-dark);
}

/* Close is a neutral navigation control, not a destructive action. */
div[class*="st-key-close_modal"] button {
    min-height: 2.5rem;
    border-color: var(--wf-primary);
    color: var(--wf-primary-dark);
}

div[class*="st-key-decision_choice_"] button {
    width: 100%;
    min-height: 3.5rem;
    white-space: normal;
    text-align: center;
    justify-content: center;
}

.wf-context-title {
    margin: 0;
    color: var(--wf-ink);
    font-size: 1.45rem;
    font-weight: 700;
}

.wf-context-label {
    margin: 0.15rem 0 0 0;
    color: var(--wf-muted);
    font-size: 0.95rem;
}

.wf-time-label {
    margin: 0;
    color: var(--wf-muted);
    font-size: 0.8rem;
    font-weight: 650;
    letter-spacing: 0.04em;
    text-transform: uppercase;
}

.wf-time-value {
    margin: 0.1rem 0 0 0;
    color: var(--wf-ink);
    font-size: 1.25rem;
    font-weight: 700;
}

.wf-information-copy {
    max-width: 880px;
    color: var(--wf-ink);
    font-size: 1.08rem;
    line-height: 1.6;
}

.wf-anchor-left, .wf-anchor-right {
    color: var(--wf-muted);
    font-size: 0.78rem;
    line-height: 1.2;
}

.wf-anchor-right {
    text-align: right;
}

@media (max-width: 800px) {
    .block-container {
        padding-left: 1rem;
        padding-right: 1rem;
    }
    div[class*="st-key-tile_"] button {
        min-height: 3.4rem;
    }
}
</style>
""", unsafe_allow_html=True)

CURRENT_TIME_VAL = (
    TIME_STEPS[st.session_state.time_index]
    if st.session_state.time_index < len(TIME_STEPS)
    else None
)


# ======================================================
# 3. LOGGING
# ======================================================
def atomic_write_json(path, data):
    """Write complete JSON through a temporary file and atomically publish it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def scheduled_time_label(index=None, offset_minutes=0):
    if index is None:
        index = st.session_state.time_index
    start = datetime.datetime.strptime(
        CONTROL.get("start_time_display", "14:00"), "%H:%M"
    )
    current = start + timedelta(
        minutes=(TIME_STEP_MINUTES * index) + offset_minutes
    )
    return current.strftime("%I:%M %p")


def result_document():
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "session": {
            "session_id": st.session_state.session_id,
            "app_version": APP_VERSION,
            "started_at_utc": st.session_state.session_started_at_utc,
            "completed_at_utc": st.session_state.session_completed_at_utc,
            "completion_status": st.session_state.completion_status
        },
        "scenario": {
            "scenario_id": SCENARIO_ID,
            "scenario_version": SCENARIO_VERSION,
            "title": TITLE,
            "control_filename": CONTROL_PATH.name,
            "control_sha256": CONTROL_SHA256,
            "start_time_display": CONTROL.get("start_time_display", "14:00"),
            "time_step_minutes": TIME_STEP_MINUTES,
            "nominal_minutes": NOMINAL_MINUTES,
            "maximum_minutes": MAX_MINUTES,
            "time_steps": TIME_STEPS,
            "assessment_scale": {
                "minimum": ASSESSMENT_SCALE_MIN,
                "maximum": ASSESSMENT_SCALE_MAX,
                "default": ASSESSMENT_SCALE_DEFAULT,
                "step": ASSESSMENT_SCALE_STEP
            },
            "assessment_variable_ids": [
                variable["id"] for variable in ASSESSMENT_VARIABLES
            ]
        },
        "events": st.session_state.logs,
        "completion": {
            "final_choice": st.session_state.final_choice,
            "end_reason": st.session_state.scenario_end_reason,
            "delivery_status": st.session_state.results_delivery_status,
            "delivery_error": st.session_state.results_delivery_error
        }
    }


def persist_results():
    atomic_write_json(
        Path("results") / f"{st.session_state.session_id}.json",
        result_document()
    )


def log_event(event, payload):
    st.session_state.event_sequence += 1
    event_step_value = (
        TIME_STEPS[st.session_state.time_index]
        if st.session_state.time_index < len(TIME_STEPS)
        else None
    )
    st.session_state.logs.append({
        "event_sequence": st.session_state.event_sequence,
        "event_id": str(uuid.uuid4()),
        "step_index": st.session_state.time_index,
        "scenario_step_value": event_step_value,
        "scheduled_step_time": scheduled_time_label(),
        "event": event,
        **payload,
        "server_timestamp_utc": utc_now_iso()
    })
    persist_results()


def save_contact_information(email, phone):
    """Keep identifiable contact information outside the behavioral event log."""
    email = email.strip()
    phone = phone.strip()
    if not email and not phone:
        return False

    contact_record = {
        "study_session_id": st.session_state.session_id,
        "collected_at_utc": utc_now_iso(),
        "email": email,
        "phone": phone
    }
    atomic_write_json(
        Path("contact_results") / f"{st.session_state.session_id}.json",
        contact_record
    )
    return True


def email_results_file(results_path=None):
    """Send the session results file using credentials stored in Streamlit secrets."""
    if results_path is None:
        results_path = Path(f"results/{st.session_state.session_id}.json")
    else:
        results_path = Path(results_path)

    if not results_path.exists():
        raise FileNotFoundError("Results file not found.")

    sender_email = st.secrets.get("SENDER_EMAIL")
    password = st.secrets.get("EMAIL_PASSWORD")
    recipient_email = st.secrets.get("RESULTS_EMAIL", sender_email)

    if not sender_email or not password:
        raise RuntimeError(
            "Email credentials are not configured. Set SENDER_EMAIL and "
            "EMAIL_PASSWORD in Streamlit secrets."
        )

    msg = EmailMessage()
    msg["Subject"] = f"Wildfire Scenario Results - {st.session_state.session_id}"
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg.set_content(f"Results attached for session: {st.session_state.session_id}")

    with open(results_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="json",
            filename=results_path.name
        )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(sender_email, password)
        server.send_message(msg)


# ======================================================
# 4. HELPERS
# ======================================================
def clear_open_information_state():
    """Clear all tile and social-message display and timing state."""
    st.session_state.open_tile = None
    st.session_state.current_tile_id = None
    st.session_state.tile_open_perf = None
    st.session_state.current_tile_instance_id = None
    st.session_state.current_tile_display_logged = False
    st.session_state.current_social_contact = None
    st.session_state.social_open_perf = None
    st.session_state.current_social_instance_id = None


def finalize_open_information(reason):
    """Log the current information exposure, then close and clear it."""
    if (
        st.session_state.current_tile_id is not None
        and st.session_state.tile_open_perf is not None
    ):
        log_event(
            "tile_closed",
            {
                "id": st.session_state.current_tile_id,
                "tile_instance_id": st.session_state.current_tile_instance_id,
                "server_elapsed_seconds": (
                    time.perf_counter() - st.session_state.tile_open_perf
                ),
                "timer_source": "server_monotonic_includes_render_latency",
                "close_reason": reason
            }
        )
    finalize_current_social(reason)

    clear_open_information_state()


def finalize_current_social(reason):
    if (
        st.session_state.current_social_contact is not None
        and st.session_state.social_open_perf is not None
    ):
        log_event(
            "social_contact_closed",
            {
                "contact": st.session_state.current_social_contact,
                "social_instance_id": st.session_state.current_social_instance_id,
                "server_elapsed_seconds": (
                    time.perf_counter() - st.session_state.social_open_perf
                ),
                "timer_source": "server_monotonic_includes_render_latency",
                "close_reason": reason
            }
        )
    st.session_state.current_social_contact = None
    st.session_state.social_open_perf = None
    st.session_state.current_social_instance_id = None


def has_new_update(tid):
    if st.session_state.time_index == 0:
        return True
    curr = TILES[tid]["content"].get(str(CURRENT_TIME_VAL))
    prev = TILES[tid]["content"].get(
        str(TIME_STEPS[st.session_state.time_index - 1])
    )
    return curr != prev


def is_end_of_time_window():
    """End after the final configured time step, independent of clock time."""
    return st.session_state.time_index >= len(TIME_STEPS)


def get_time_label():
    return scheduled_time_label()


def render_context_header(activity, *, show_scenario_time=True):
    """Render a consistent, neutral header without changing scenario flow."""
    with st.container(border=True):
        if show_scenario_time:
            title_col, time_col = st.columns([4, 1])
            with title_col:
                st.markdown(
                    '<p class="wf-context-title">Wildfire Evacuation Scenario</p>',
                    unsafe_allow_html=True
                )
                st.markdown(
                    f'<p class="wf-context-label">{activity}</p>',
                    unsafe_allow_html=True
                )
            with time_col:
                st.markdown(
                    '<p class="wf-time-label">Scenario time</p>',
                    unsafe_allow_html=True
                )
                st.markdown(
                    f'<p class="wf-time-value">{get_time_label()}</p>',
                    unsafe_allow_html=True
                )
        else:
            st.markdown(
                '<p class="wf-context-title">Wildfire Evacuation Scenario</p>',
                unsafe_allow_html=True
            )
            st.markdown(
                f'<p class="wf-context-label">{activity}</p>',
                unsafe_allow_html=True
            )


def scenario_briefing_sections(intro):
    """Group the unchanged original-scenario facts into readable sections."""
    lines = [str(line).strip() for line in intro.get("text", []) if str(line).strip()]
    if len(lines) >= 13:
        return [
            ("Your household", " ".join(lines[0:5])),
            ("Home and wildfire experience", " ".join(lines[5:8])),
            ("Your role", " ".join(lines[8:10])),
            ("What happens next", " ".join(lines[10:])),
        ]
    return [("Scenario information", " ".join(lines))]


def content_sha256(content):
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assessment_interaction_key(variable_id):
    return f"{st.session_state.time_index}:{variable_id}"


def mark_assessment_interaction(variable_id):
    key = assessment_interaction_key(variable_id)
    record = st.session_state.assessment_interactions.setdefault(
        key,
        {
            "was_interacted_with": False,
            "change_count": 0,
            "first_interaction_at_utc": None
        }
    )
    record["was_interacted_with"] = True
    record["change_count"] += 1
    if record["first_interaction_at_utc"] is None:
        record["first_interaction_at_utc"] = utc_now_iso()


def decision_process_widget_key():
    return f"decision_process_state_{st.session_state.time_index}"


def decision_process_interaction_record():
    """Return the non-reactive interaction record for the current step."""
    return st.session_state.decision_process_state_interactions.setdefault(
        st.session_state.time_index,
        {
            "was_interacted_with": False,
            "change_count": 0,
            "first_interaction_at_utc": None,
            "last_interaction_at_utc": None,
            "current_value": "__select__",
            "history": []
        }
    )


def mark_decision_process_state_interaction():
    """Log decision-stage changes without validating or reacting to them."""
    selected_value = st.session_state.get(
        decision_process_widget_key(),
        "__select__"
    )
    record = decision_process_interaction_record()
    previous_value = record["current_value"]
    if selected_value == previous_value:
        return

    changed_at = utc_now_iso()
    record["was_interacted_with"] = True
    record["change_count"] += 1
    if record["first_interaction_at_utc"] is None:
        record["first_interaction_at_utc"] = changed_at
    record["last_interaction_at_utc"] = changed_at
    record["current_value"] = selected_value
    record["history"].append({
        "change_sequence": record["change_count"],
        "from": previous_value,
        "to": selected_value,
        "changed_at_utc": changed_at
    })

    log_event(
        "decision_process_state_changed",
        {
            "change_sequence_in_step": record["change_count"],
            "from": previous_value,
            "to": selected_value,
            "to_label": DECISION_PROCESS_STATES.get(selected_value),
            "logging_is_non_reactive": True
        }
    )


def prep_available(action):
    return (
            CURRENT_TIME_VAL is not None and
            action["available_from"] <= CURRENT_TIME_VAL <= action["available_until"]
    )


def is_repeatable(action):
    """Return True when the JSON allows an action to reappear in later steps."""
    return bool(action.get("repeatable", False))


def performed_this_step(action):
    """Repeatable and non-repeatable actions can only be performed once per step."""
    return action["id"] in st.session_state.prep_actions_this_step


def permanently_completed(action):
    """Only non-repeatable actions are permanently completed."""
    return (
        not is_repeatable(action)
        and action["id"] in st.session_state.completed_prep_actions
    )


def minutes_remaining_in_period():
    return max(0, NOMINAL_MINUTES - st.session_state.prep_minutes_this_step)


def minutes_remaining_to_cap():
    return max(0, MAX_MINUTES - st.session_state.prep_minutes_this_step)


def can_perform_action(action):
    """An action is selectable only if it is available, unfinished, and fits the time cap."""
    if not prep_available(action):
        return False
    if permanently_completed(action):
        return False
    if performed_this_step(action):
        return False

    duration = int(action.get("estimated_time_minutes", 0))
    return duration <= minutes_remaining_to_cap()


def get_prep_action(action_id):
    return next((a for a in PREP_ACTIONS if a["id"] == action_id), None)


def preparation_opportunity_snapshot():
    """Describe the options shown now without changing their availability."""
    snapshot = []
    for action in PREP_ACTIONS:
        if not prep_available(action):
            continue

        prerequisites = list(action.get("prerequisites", []))
        missing_prerequisites = [
            prerequisite_id
            for prerequisite_id in prerequisites
            if prerequisite_id not in st.session_state.completed_prep_actions
        ]
        duration = int(action.get("estimated_time_minutes", 0))
        snapshot.append({
            "action_id": action["id"],
            "action_label": action["label"],
            "estimated_time_minutes": duration,
            "repeatable": is_repeatable(action),
            "completed_before_or_during_period": permanently_completed(action),
            "performed_this_step": performed_this_step(action),
            "fits_remaining_time": duration <= minutes_remaining_to_cap(),
            "selectable_at_snapshot": can_perform_action(action),
            "prerequisites": prerequisites,
            "prerequisites_met": not missing_prerequisites,
            "missing_prerequisites": missing_prerequisites,
            "prerequisite_check_is_logging_only": True
        })
    return snapshot


def log_scenario_step_started_once():
    """Create one opportunity-set record for each displayed scenario step."""
    step_index = st.session_state.time_index
    if step_index in st.session_state.scenario_step_started_steps:
        return

    st.session_state.scenario_step_started_steps.add(step_index)
    sorted_tile_ids = sorted(
        TILES.keys(),
        key=lambda value: (
            (0, int(value)) if str(value).isdigit() else (1, str(value))
        )
    )
    log_event(
        "scenario_step_started",
        {
            "scheduled_period_start": scheduled_time_label(),
            "time_step_minutes": TIME_STEP_MINUTES,
            "nominal_minutes": NOMINAL_MINUTES,
            "maximum_minutes": MAX_MINUTES,
            "available_information_source_ids": sorted_tile_ids,
            "available_preparation_action_ids": [
                action["action_id"]
                for action in preparation_opportunity_snapshot()
            ],
            "completed_nonrepeatable_actions_at_step_start": sorted(
                st.session_state.completed_prep_actions
            )
        }
    )


# ======================================================
# 5. CONSENT / CONTACT / INTRO
# ======================================================
if not st.session_state.consent_given:
    render_context_header("Research study information", show_scenario_time=False)
    with st.container(border=True):
        st.header(CONTROL["irb_consent"]["title"])
        for p in CONTROL["irb_consent"]["text"]:
            st.write(p)
        st.divider()
        read_information = st.checkbox("I have read the information.")
        consent_to_participate = st.checkbox("I consent to participate.")
        if st.button(
            "Proceed",
            type="primary",
            disabled=not (read_information and consent_to_participate)
        ):
            st.session_state.consent_given = True
            log_event("consent_accepted", {})
            st.rerun()
    st.stop()

if not st.session_state.contact_collected:
    render_context_header("Optional contact information", show_scenario_time=False)
    with st.container(border=True):
        st.header(CONTROL["contact_screen"]["title"])
        for p in CONTROL["contact_screen"]["text"]:
            st.write(p)
        st.caption("You may leave both fields blank and continue.")
        email_col, phone_col = st.columns(2)
        with email_col:
            email = st.text_input("Email (optional)")
        with phone_col:
            phone = st.text_input("Phone (optional)")
        if st.button("Continue", type="primary"):
            contact_record_created = save_contact_information(email, phone)
            st.session_state.contact_collected = True
            log_event(
                "contact_collected",
                {
                    "email_provided": bool(email.strip()),
                    "phone_provided": bool(phone.strip()),
                    "pii_record_created": contact_record_created,
                    "pii_stored_separately": contact_record_created
                }
            )
            st.rerun()
    st.stop()

if st.session_state.show_intro:
    intro = CONTROL["scenario_description"]
    render_context_header("Scenario briefing", show_scenario_time=False)
    st.header(intro["title"])
    c1, c2 = st.columns(2)
    with c1:
        with st.container(border=True):
            if "image_house" in intro:
                st.image(intro["image_house"], use_container_width=True)
                st.caption("Scenario household")
    with c2:
        with st.container(border=True):
            if "image_map" in intro:
                st.image(intro["image_map"], use_container_width=True)
                st.caption("Scenario area map")

    briefing_sections = scenario_briefing_sections(intro)
    for row_start in range(0, len(briefing_sections), 2):
        section_columns = st.columns(2)
        for column_index, section in enumerate(
            briefing_sections[row_start:row_start + 2]
        ):
            heading, paragraph = section
            with section_columns[column_index]:
                with st.container(border=True):
                    st.markdown(f"#### {heading}")
                    st.write(paragraph)

    if st.button("Start Scenario", type="primary", use_container_width=True):
        st.session_state.show_intro = False
        log_event("scenario_started", {})
        st.session_state.current_phase = "dashboard"
        st.session_state.dashboard_start_perf = time.perf_counter()
        log_event("phase_entered", {"phase": "dashboard"})
        st.rerun()
    st.stop()

# ======================================================
# 6. ASSESSMENT SCREEN
# ======================================================
if st.session_state.in_assessment:
    render_context_header("Situation assessment")
    st.subheader("Situation Assessment")
    st.info(
        "Rate each item from 0 to 10 based on the situation as you understand "
        "it now. Every slider begins at 5; leave it at 5 if that represents "
        "your response."
    )

    results = {}
    for row_start in range(0, len(ASSESSMENT_VARIABLES), 2):
        assessment_columns = st.columns(2)
        for column_index, variable in enumerate(
            ASSESSMENT_VARIABLES[row_start:row_start + 2]
        ):
            variable_id = variable["id"]
            question_number = row_start + column_index + 1
            with assessment_columns[column_index]:
                with st.container(border=True):
                    st.caption(
                        f"Question {question_number} of "
                        f"{len(ASSESSMENT_VARIABLES)}"
                    )
                    st.markdown(f"**{variable['prompt']}**")
                    results[variable_id] = st.slider(
                        variable["prompt"],
                        ASSESSMENT_SCALE_MIN,
                        ASSESSMENT_SCALE_MAX,
                        ASSESSMENT_SCALE_DEFAULT,
                        step=ASSESSMENT_SCALE_STEP,
                        key=(
                            f"assessment_{variable_id}_"
                            f"{st.session_state.time_index}"
                        ),
                        on_change=mark_assessment_interaction,
                        args=(variable_id,),
                        label_visibility="collapsed"
                    )
                    low_col, high_col = st.columns(2)
                    with low_col:
                        st.markdown(
                            f'<div class="wf-anchor-left">0 — '
                            f'{variable["low_anchor"]}</div>',
                            unsafe_allow_html=True
                        )
                    with high_col:
                        st.markdown(
                            f'<div class="wf-anchor-right">10 — '
                            f'{variable["high_anchor"]}</div>',
                            unsafe_allow_html=True
                        )

    if st.button(
        "Continue to Decisions",
        type="primary",
        use_container_width=True
    ):
        assessment_duration = (
            time.perf_counter() - st.session_state.assessment_start_perf
        )
        interaction_records = {}
        for variable in ASSESSMENT_VARIABLES:
            variable_id = variable["id"]
            record = st.session_state.assessment_interactions.get(
                assessment_interaction_key(variable_id),
                {
                    "was_interacted_with": False,
                    "change_count": 0,
                    "first_interaction_at_utc": None
                }
            )
            interaction_records[variable_id] = {
                "value": results[variable_id],
                "default_value": ASSESSMENT_SCALE_DEFAULT,
                **record
            }

        log_event(
            "assessment_submitted",
            {
                "scores": results,
                "interactions": interaction_records,
                "scale": {
                    "minimum": ASSESSMENT_SCALE_MIN,
                    "maximum": ASSESSMENT_SCALE_MAX,
                    "default": ASSESSMENT_SCALE_DEFAULT,
                    "step": ASSESSMENT_SCALE_STEP
                },
                "server_elapsed_seconds": assessment_duration
            }
        )
        log_event(
            "phase_exited",
            {
                "phase": "assessment",
                "server_elapsed_seconds": assessment_duration,
                "timer_source": "server_monotonic_includes_render_latency",
                "includes_nested_information_time": False
            }
        )
        st.session_state.cached_assessment = results
        st.session_state.cached_assessment_interactions = interaction_records
        st.session_state.in_assessment = False
        st.session_state.in_decision = True
        st.session_state.current_phase = "decision"
        st.session_state.decision_start_perf = time.perf_counter()
        log_event("phase_entered", {"phase": "decision"})
        st.rerun()
    st.stop()

# ======================================================
# 7. DECISION SCREEN (PREPARATION + EVACUATION)
# if there is no description in json it may show an empty line and an option
# ======================================================
if st.session_state.in_decision:
    render_context_header("Preparation and decision")
    st.subheader("Preparation and Decisions")

    st.markdown("### Preparation actions")

    # --------------------------------------------------
    # Simulated time budget for this scenario step
    # --------------------------------------------------
    used_minutes = st.session_state.prep_minutes_this_step
    remaining_hour = minutes_remaining_in_period()
    remaining_cap = minutes_remaining_to_cap()

    # Capture the initial opportunity set once per scenario step. This is an
    # analysis record only; it does not modify, hide, recommend, or validate
    # any preparation or evacuation option.
    if (
        st.session_state.time_index
        not in st.session_state.decision_screen_presented_steps
    ):
        st.session_state.decision_screen_presented_steps.add(
            st.session_state.time_index
        )
        log_event(
            "decision_options_presented",
            {
                "scheduled_period_start": scheduled_time_label(),
                "time_budget": {
                    "time_step_minutes": TIME_STEP_MINUTES,
                    "nominal_minutes": NOMINAL_MINUTES,
                    "maximum_minutes": MAX_MINUTES,
                    "minutes_used_at_snapshot": used_minutes,
                    "minutes_remaining_at_snapshot": remaining_cap
                },
                "preparation_options": preparation_opportunity_snapshot(),
                "decision_process_options": [
                    {"id": state_id, "label": state_label}
                    for state_id, state_label in DECISION_PROCESS_STATES.items()
                ],
                "evacuation_choice_options": [
                    "evacuate_all",
                    "evacuate_family",
                    "stay"
                ],
                "logging_is_non_reactive": True
            }
        )

    if MAX_MINUTES == NOMINAL_MINUTES:
        t1, t2 = st.columns(2)
        t1.metric("Time used", f"{used_minutes} min")
        t2.metric("Time remaining", f"{remaining_cap} min")
    else:
        t1, t2, t3 = st.columns(3)
        t1.metric("Time used", f"{used_minutes} min")
        t2.metric("Time left in period", f"{remaining_hour} min")
        t3.metric("Time left until maximum", f"{remaining_cap} min")

    st.progress(min(used_minutes / MAX_MINUTES, 1.0))

    if MAX_MINUTES == NOMINAL_MINUTES:
        st.caption(
            f"Preparation actions may use up to {MAX_MINUTES} minutes in "
            "this scenario period."
        )
    elif used_minutes > NOMINAL_MINUTES:
        st.warning(
            f"You are {used_minutes - NOMINAL_MINUTES} minutes beyond the "
            f"{NOMINAL_MINUTES}-minute period. No action may push the total "
            f"past {MAX_MINUTES} minutes."
        )
    else:
        st.caption(
            f"Preparation actions may use up to {MAX_MINUTES} minutes in this step "
            f"({NOMINAL_MINUTES} minutes plus a "
            f"{MAX_MINUTES - NOMINAL_MINUTES}-minute allowance)."
        )

    if st.session_state.prep_actions_this_step:
        with st.container(border=True):
            st.markdown("#### Actions selected this period")
            running_total = 0
            for seq_num, action_id in enumerate(
                st.session_state.prep_actions_this_step,
                start=1
            ):
                selected_action = get_prep_action(action_id)
                if selected_action is None:
                    continue
                duration = int(selected_action.get("estimated_time_minutes", 0))
                running_total += duration
                st.write(
                    f"{seq_num}. {selected_action['label']} — {duration} min "
                    f"(cumulative: {running_total} min)"
                )

    st.divider()

    for action in PREP_ACTIONS:
        if not prep_available(action):
            continue

        done_this_step = performed_this_step(action)
        done_permanently = permanently_completed(action)
        duration = int(action.get("estimated_time_minutes", 0))
        fits_time = duration <= minutes_remaining_to_cap()

        with st.container(border=True):
            col1, col2, col3 = st.columns([5, 1, 1.7])

            with col1:
                st.markdown(f"**{action['label']}**")
                if action.get("description"):
                    st.caption(action["description"])

            with col2:
                st.markdown(f"**{duration} min**")
                st.caption("Estimated time")

            with col3:
                if done_permanently:
                    st.markdown("**Completed**")
                elif done_this_step:
                    st.markdown("**Done this period**")
                else:
                    if st.button(
                        "Perform action",
                        key=f"prep_{action['id']}_{st.session_state.time_index}",
                        disabled=not fits_time,
                        use_container_width=True
                    ):
                        minutes_before = st.session_state.prep_minutes_this_step
                        prerequisites = list(action.get("prerequisites", []))
                        missing_prerequisites = [
                            prerequisite_id
                            for prerequisite_id in prerequisites
                            if prerequisite_id
                            not in st.session_state.completed_prep_actions
                        ]
                        st.session_state.prep_minutes_this_step += duration
                        minutes_after = st.session_state.prep_minutes_this_step
                        st.session_state.prep_actions_this_step.append(action["id"])

                        if not is_repeatable(action):
                            st.session_state.completed_prep_actions.add(action["id"])

                        st.session_state.prep_action_counts[action["id"]] = (
                            st.session_state.prep_action_counts.get(action["id"], 0) + 1
                        )

                        log_event(
                            "prep_action_completed",
                            {
                                "action_id": action["id"],
                                "action_label": action["label"],
                                "repeatable": is_repeatable(action),
                                "prerequisites": prerequisites,
                                "prerequisites_met": not missing_prerequisites,
                                "missing_prerequisites": missing_prerequisites,
                                "prerequisite_check_is_logging_only": True,
                                "estimated_time_minutes": duration,
                                "sequence_in_step": len(
                                    st.session_state.prep_actions_this_step
                                ),
                                "minutes_before_action": minutes_before,
                                "minutes_used_this_step": minutes_after,
                                "simulated_action_start_time": scheduled_time_label(
                                    offset_minutes=minutes_before
                                ),
                                "simulated_action_end_time": scheduled_time_label(
                                    offset_minutes=minutes_after
                                ),
                                "minutes_remaining_in_period": (
                                    minutes_remaining_in_period()
                                ),
                                "minutes_remaining_to_cap": (
                                    minutes_remaining_to_cap()
                                ),
                                "occurrence": st.session_state.prep_action_counts[
                                    action["id"]
                                ]
                            }
                        )
                        st.rerun()

                    if not fits_time:
                        st.caption(
                            f"Insufficient time: "
                            f"{minutes_remaining_to_cap()} min remain."
                        )

    st.divider()

    with st.container(border=True):
        st.markdown("### Record what you would do now")
        st.write(
            "First describe your current decision stage. Then select the "
            "action you would take at this moment."
        )

        process_state_options = ["__select__", *DECISION_PROCESS_STATES.keys()]
        selected_process_state = st.selectbox(
            "Which statement best describes what you have decided to do at this moment?",
            process_state_options,
            format_func=lambda value: (
                "Select your current decision stage"
                if value == "__select__"
                else DECISION_PROCESS_STATES[value]
            ),
            key=decision_process_widget_key(),
            on_change=mark_decision_process_state_interaction
        )
        process_state_selected = selected_process_state != "__select__"

        st.markdown("#### Evacuation decision")

        is_final_step = st.session_state.time_index == len(TIME_STEPS) - 1

        if not process_state_selected:
            st.caption(
                "Select your current decision stage before making the final choice."
            )

        decision_columns = st.columns(3)
        with decision_columns[0]:
            evac_all = st.button(
                "Evacuate now",
                key=f"decision_choice_all_{st.session_state.time_index}",
                disabled=not process_state_selected,
                use_container_width=True
            )
        with decision_columns[1]:
            evac_fam = st.button(
                "Ask a neighbor to evacuate kids and dog",
                key=f"decision_choice_family_{st.session_state.time_index}",
                disabled=not process_state_selected,
                use_container_width=True
            )
        stay_label = "Stay despite GO order" if is_final_step else "Stay and continue"
        with decision_columns[2]:
            stay = st.button(
                stay_label,
                key=f"decision_choice_stay_{st.session_state.time_index}",
                disabled=not process_state_selected,
                use_container_width=True
            )

    if evac_all or evac_fam or stay:
        decision_duration = (
            time.perf_counter() - st.session_state.decision_start_perf
        )
        choice = "stay"
        if evac_all:
            choice = "evacuate_all"
        if evac_fam:
            choice = "evacuate_family"
        st.session_state.final_choice = choice
        st.session_state.decision_process_state = selected_process_state

        # Preserve action order and cumulative simulated time in the hourly log.
        action_sequence = []
        running_total = 0
        for action_id in st.session_state.prep_actions_this_step:
            selected_action = get_prep_action(action_id)
            if selected_action is None:
                continue
            duration = int(selected_action.get("estimated_time_minutes", 0))
            running_total += duration
            action_sequence.append({
                "action_id": action_id,
                "action_label": selected_action["label"],
                "estimated_time_minutes": duration,
                "cumulative_minutes": running_total,
                "simulated_action_end_time": scheduled_time_label(
                    offset_minutes=running_total
                )
            })

        simulated_offset = st.session_state.prep_minutes_this_step
        decision_simulated_time = scheduled_time_label(
            offset_minutes=simulated_offset
        )
        choice_is_evacuation = choice in {"evacuate_all", "evacuate_family"}
        state_is_evacuation = selected_process_state == "evacuate_now"
        decision_state_choice_consistent = (
            choice_is_evacuation == state_is_evacuation
        )
        process_interaction_record = decision_process_interaction_record()

        decision_payload = {
            "time_step_minutes": TIME_STEP_MINUTES,
            "scheduled_period_start": scheduled_time_label(),
            "simulated_offset_minutes": simulated_offset,
            "decision_simulated_time": decision_simulated_time,
            "scores": st.session_state.cached_assessment,
            "assessment_interactions": (
                st.session_state.cached_assessment_interactions
            ),
            "decision_process_state": selected_process_state,
            "decision_process_label": DECISION_PROCESS_STATES[
                selected_process_state
            ],
            "choice": choice,
            "decision_state_choice_consistent": (
                decision_state_choice_consistent
            ),
            "decision_process_state_interactions": {
                "was_interacted_with": process_interaction_record[
                    "was_interacted_with"
                ],
                "change_count": process_interaction_record["change_count"],
                "first_interaction_at_utc": process_interaction_record[
                    "first_interaction_at_utc"
                ],
                "last_interaction_at_utc": process_interaction_record[
                    "last_interaction_at_utc"
                ],
                "history": list(process_interaction_record["history"])
            },
            "information_acquisition_summary": {
                "unique_information_source_ids_opened": sorted(
                    st.session_state.tiles_opened_this_step,
                    key=lambda value: (
                        (0, int(value))
                        if str(value).isdigit()
                        else (1, str(value))
                    )
                ),
                "information_source_view_count": (
                    st.session_state.tile_view_sequence_this_step
                )
            },
            "prep_action_sequence": action_sequence,
            "prep_minutes_this_step": simulated_offset,
            "minutes_beyond_nominal_period": max(
                0,
                simulated_offset - NOMINAL_MINUTES
            ),
            "completed_nonrepeatable_actions": sorted(
                st.session_state.completed_prep_actions
            ),
            "prep_action_counts": dict(st.session_state.prep_action_counts),
            "server_elapsed_seconds": decision_duration
        }

        log_event(
            "decision_submitted",
            decision_payload
        )
        log_event(
            "phase_exited",
            {
                "phase": "decision",
                "server_elapsed_seconds": decision_duration,
                "timer_source": "server_monotonic_includes_render_latency",
                "includes_nested_information_time": False
            }
        )

        st.session_state.in_decision = False
        st.session_state.cached_assessment = None
        st.session_state.cached_assessment_interactions = None
        st.session_state.current_phase = None

        # Information panels must never carry over into a new scenario step.
        # Exposure should already have been finalized before assessment; this
        # is a defensive state reset and intentionally does not log again.
        clear_open_information_state()

        if choice in ["evacuate_all", "evacuate_family"]:
            st.session_state.scenario_ended = True
            st.session_state.scenario_end_reason = "evacuated"
        elif is_final_step:
            st.session_state.scenario_ended = True
            st.session_state.scenario_end_reason = "stayed_after_final_go"

        if st.session_state.scenario_ended:
            st.session_state.completion_status = "completed"
            st.session_state.session_completed_at_utc = utc_now_iso()
            log_event(
                "scenario_outcome",
                {
                    "final_choice": st.session_state.final_choice,
                    "end_reason": st.session_state.scenario_end_reason,
                    "final_scheduled_period_start": scheduled_time_label(),
                    "final_simulated_offset_minutes": simulated_offset,
                    "final_simulated_time": decision_simulated_time
                }
            )
        else:
            st.session_state.time_index += 1

            # A new scenario step receives a fresh action budget. Repeatable
            # actions can reappear if their availability window includes it.
            st.session_state.prep_actions_this_step = []
            st.session_state.prep_minutes_this_step = 0
            st.session_state.decision_process_state = None
            st.session_state.tiles_opened_this_step.clear()
            st.session_state.viewed_updates.clear()
            st.session_state.tile_view_sequence_this_step = 0

            st.session_state.current_phase = "dashboard"
            st.session_state.dashboard_start_perf = time.perf_counter()
            log_event("phase_entered", {"phase": "dashboard"})

        st.rerun()
    st.stop()

# ======================================================
# SCENARIO END HANDLING
# ======================================================
if st.session_state.scenario_ended:
    render_context_header("Scenario complete")
    st.header("Scenario Complete")
    if st.session_state.scenario_end_reason == "evacuated":
        st.success("Your decision to evacuate has been recorded.")
    elif st.session_state.scenario_end_reason == "stayed_after_final_go":
        st.success("Your decision to stay after the final GO alert has been recorded.")
    else:
        st.success("Thank you for participating in this evacuation scenario!")

    # Attempt delivery only once per session. The local atomic result file is
    # preserved whether delivery succeeds or fails.
    results_file = f"results/{st.session_state.session_id}.json"
    if st.session_state.results_delivery_status == "not_attempted":
        st.session_state.results_delivery_status = "attempting"
        log_event(
            "results_delivery_attempted",
            {"method": "email", "delivery_status": "attempting"}
        )
        try:
            email_results_file(results_file)
            st.session_state.results_delivery_status = "succeeded"
            st.session_state.results_delivery_error = None
            log_event(
                "results_delivery_completed",
                {"method": "email", "delivery_status": "succeeded"}
            )
        except Exception as e:
            st.session_state.results_delivery_status = "failed"
            st.session_state.results_delivery_error = str(e)
            log_event(
                "results_delivery_completed",
                {
                    "method": "email",
                    "delivery_status": "failed",
                    "error_type": type(e).__name__
                }
            )

    if st.session_state.results_delivery_status == "succeeded":
        st.info("✅ Your decisions have been automatically recorded.")
    else:
        st.error(
            "Your result was saved locally, but email delivery failed. "
            "Please notify the research team before closing this window."
        )

    st.write("You may now close this window.")
    st.stop()




# ======================================================
# 8. MAIN DASHBOARD
# ======================================================
render_context_header("Review information")
log_scenario_step_started_once()

# Display information panel at TOP if tile is open
if st.session_state.open_tile:
    tile = TILES[st.session_state.open_tile]
    content = tile["content"].get(str(CURRENT_TIME_VAL))

    if not st.session_state.current_tile_display_logged:
        log_event(
            "tile_displayed_server",
            {
                "id": st.session_state.current_tile_id,
                "label": tile["label"],
                "tile_instance_id": st.session_state.current_tile_instance_id,
                "content_sha256": content_sha256(content),
                "measurement_semantics": (
                    "server_render_started; browser visibility is not yet measured"
                )
            }
        )
        st.session_state.current_tile_display_logged = True

    with st.container(border=True):
        panel_title_col, panel_close_col = st.columns([5, 1])
        with panel_title_col:
            st.markdown(f"### {tile['label']}")
            st.caption("Open information source")
        with panel_close_col:
            if st.button(
                "Close",
                key="close_modal",
                use_container_width=True
            ):
                finalize_open_information("user_closed")
                st.rerun()

        st.divider()

        if tile.get("type") == "social_contacts":
            for c in tile["contacts"]:
                if st.button(
                    f"Message {c['name']}",
                    key=f"soc_{c['id']}",
                    use_container_width=True
                ):
                    if st.session_state.current_social_contact:
                        finalize_current_social("contact_switched")
                    reply = CONTROL["social_response_policies"][
                        c["response_policy"]
                    ][str(CURRENT_TIME_VAL)]
                    st.session_state.current_social_contact = c["name"]
                    st.session_state.social_open_perf = time.perf_counter()
                    st.session_state.current_social_instance_id = str(uuid.uuid4())
                    log_event(
                        "social_contact_requested",
                        {
                            "contact": c["name"],
                            "contact_id": c["id"],
                            "social_instance_id": (
                                st.session_state.current_social_instance_id
                            ),
                            "response_policy": c["response_policy"]
                        }
                    )
                    log_event(
                        "social_reply_displayed_server",
                        {
                            "contact": c["name"],
                            "contact_id": c["id"],
                            "social_instance_id": (
                                st.session_state.current_social_instance_id
                            ),
                            "reply": reply,
                            "reply_sha256": content_sha256(reply)
                        }
                    )
                    st.info(reply)
        else:
            if content is not None:
                if "text" in content:
                    st.markdown(
                        f'<div class="wf-information-copy">'
                        f'{content["text"]}</div>',
                        unsafe_allow_html=True
                    )
                if "image" in content:
                    img = content.get("image")

                    # Only attempt to render a non-empty image path.
                    if isinstance(img, str) and img.strip():
                        img = img.strip().replace("\\", "/")
                        st.image(img)
            else:
                st.caption("No information is available at this time.")

# Display tile grid
st.subheader("Information Sources")
# Lock tile selection while a tile is open (forces user to press Close)
tile_lock = st.session_state.open_tile is not None
if tile_lock:
    st.info("Close the open information source before selecting another one.")
elif len(st.session_state.tiles_opened_this_step) == 0:
    st.info("Open at least one information source before continuing.")
else:
    st.caption(
        "You may review additional information sources or continue to the assessment."
    )
# Create 4 rows of 4 tiles each
for row in range(4):
    cols = st.columns(4)
    for col_idx in range(4):
        tile_num = row * 4 + col_idx + 1
        if tile_num > 16:
            break

        tid = str(tile_num)
        label = TILES[tid]["label"]

        text = label

        with cols[col_idx]:

            if st.button(
                text,
                key=f"tile_{tid}_{st.session_state.time_index}",
                use_container_width=True,
                disabled=tile_lock
            ):

                # Open new tile
                was_revisit = tid in st.session_state.tiles_opened_this_step
                content_changed_from_previous_period = has_new_update(tid)
                new_to_participant_this_period = (
                    content_changed_from_previous_period
                    and tid not in st.session_state.viewed_updates
                )
                st.session_state.tile_view_sequence_this_step += 1
                st.session_state.open_tile = tid
                st.session_state.current_tile_id = tid
                st.session_state.tile_open_perf = time.perf_counter()
                st.session_state.current_tile_instance_id = str(uuid.uuid4())
                st.session_state.current_tile_display_logged = False
                st.session_state.tiles_opened_this_step.add(tid)
                st.session_state.viewed_updates.add(tid)

                # Reset social contact tracking
                st.session_state.current_social_contact = None
                st.session_state.social_open_perf = None
                st.session_state.current_social_instance_id = None

                tile_content = TILES[tid]["content"].get(str(CURRENT_TIME_VAL))
                log_event(
                    "tile_requested",
                    {
                        "id": tid,
                        "label": label,
                        "tile_instance_id": (
                            st.session_state.current_tile_instance_id
                        ),
                        "view_sequence_in_step": (
                            st.session_state.tile_view_sequence_this_step
                        ),
                        "was_revisit": was_revisit,
                        "content_changed_from_previous_period": (
                            content_changed_from_previous_period
                        ),
                        "new_to_participant_this_period": (
                            new_to_participant_this_period
                        ),
                        "content_sha256": content_sha256(tile_content)
                    }
                )
                st.rerun()

# ======================================================
# 9. TRANSITION TO ASSESSMENT
# ======================================================
st.divider()

assessment_disabled = (
    len(st.session_state.tiles_opened_this_step) == 0
    or st.session_state.open_tile is not None
)

if st.session_state.open_tile is not None:
    st.caption(
        "Close the current information source before proceeding "
        "to the assessment."
    )
elif len(st.session_state.tiles_opened_this_step) == 0:
    st.caption("The assessment becomes available after you open one information source.")

if st.button(
    "Go to Assessment",
    disabled=assessment_disabled,
    use_container_width=True,
    type="primary"
):
    # Defensive finalization: normally the tile was already closed explicitly.
    finalize_open_information("assessment_started")

    dashboard_duration = (
        time.perf_counter() - st.session_state.dashboard_start_perf
    )
    log_event(
        "phase_exited",
        {
            "phase": "dashboard",
            "server_elapsed_seconds": dashboard_duration,
            "timer_source": "server_monotonic_includes_render_latency",
            "includes_nested_information_time": True
        }
    )
    st.session_state.in_assessment = True
    st.session_state.current_phase = "assessment"
    st.session_state.assessment_start_perf = time.perf_counter()
    log_event("phase_entered", {"phase": "assessment"})

    st.rerun()
