# v2.5 Iron Fire adaptation - configurable time-step duration
#lasted version of the code updated on github as evactracer.py
#backwards compatible
import streamlit as st
import json
import datetime
from datetime import timedelta
import uuid
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

# Set wide layout for more display space
st.set_page_config(layout="wide")


# ======================================================
# 1. LOAD CONTROL FILE
# ======================================================
@st.cache_data
def load_control():
    if not os.path.exists("control.json"):
        st.error("Error: 'control.json' not found.")
        st.stop()
    with open("control.json", "r") as f:
        return json.load(f)


CONTROL = load_control()
TITLE = CONTROL.get("title", "Research Scenario")
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

SUBJECTIVE_VARS = [
    "Do you believe the wildfire currently poses a threat to you and your family; 0 no threat, 100 very high threat",
    "How much decision time pressure do you feel; 0 no time pressure, 100 extreme time pressure",
    "What is your trust in official alerts; 0 no trust, 100 very high trust",
    "How anxious are you in this situation; 0 no anxiety, 100 very high anxiety",
    "How much social pressure do you experience; 0 no pressure, 100 extreme social pressure",
    "How feasable is an evacuation at this moment; 0 no feasibility, 100 very high feasibility",
    "Decision leaning; 0–50 leaning stay, 51–100 leaning evacuate"
]


# ======================================================
# 2. SESSION STATE INITIALIZATION
# ======================================================
def init_state():
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.logs = []

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
        st.session_state.dashboard_start_time = datetime.datetime.now()

        # phase flags
        st.session_state.in_assessment = False
        st.session_state.in_decision = False

        # timing
        st.session_state.assessment_start_time = None
        st.session_state.decision_start_time = None
        st.session_state.tile_open_time = None
        st.session_state.current_tile_id = None

        # social timing
        st.session_state.current_social_contact = None
        st.session_state.social_open_time = None

        # assessment cache
        st.session_state.cached_assessment = None

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


init_state()
ensure_new_state_defaults()

st.markdown("""
<style>
/* Uniform tile styling */
.stButton > button {
    width: 100% !important;
    height: 60px !important;
    padding: 12px !important;
    text-align: left !important;
    white-space: normal !important;
    word-wrap: break-word !important;
    font-size: 14px !important;
    line-height: 1.3 !important;
    display: flex !important;
    align-items: center !important;
}

/* Make columns equal width */
div[data-testid="column"] {
    flex: 1 !important;
    min-width: 0 !important;
}

/* Style close button */
button[kind="secondary"]:has-text("Close") {
    background-color: #ff4b4b !important;
    color: white !important;
    border: none !important;
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
def log_event(event, payload):
    st.session_state.logs.append({
        "time_step": CURRENT_TIME_VAL,
        "event": event,
        **payload,
        "timestamp": datetime.datetime.now().isoformat()
    })
    os.makedirs("results", exist_ok=True)
    with open(f"results/{st.session_state.session_id}.json", "w") as f:
        json.dump(st.session_state.logs, f, indent=2)


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
    st.session_state.tile_open_time = None
    st.session_state.current_social_contact = None
    st.session_state.social_open_time = None


def finalize_open_information(reason):
    """Log the current information exposure, then close and clear it."""
    if (
        st.session_state.current_tile_id is not None
        and st.session_state.tile_open_time is not None
    ):
        log_event(
            "tile_time_spent",
            {
                "id": st.session_state.current_tile_id,
                "duration_seconds": (
                        datetime.datetime.now() - st.session_state.tile_open_time
                ).total_seconds(),
                "close_reason": reason
            }
        )
    if (
        st.session_state.current_social_contact is not None
        and st.session_state.social_open_time is not None
    ):
        log_event(
            "social_message_time_spent",
            {
                "contact": st.session_state.current_social_contact,
                "duration_seconds": (
                        datetime.datetime.now() - st.session_state.social_open_time
                ).total_seconds(),
                "close_reason": reason
            }
        )

    clear_open_information_state()


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
    start = datetime.datetime.strptime(
        CONTROL.get("start_time_display", "14:00"), "%H:%M"
    )
    current = start + timedelta(
        minutes=TIME_STEP_MINUTES * st.session_state.time_index
    )
    return current.strftime("%I:%M %p")


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


def minutes_remaining_in_hour():
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


# ======================================================
# 5. CONSENT / CONTACT / INTRO
# ======================================================
if not st.session_state.consent_given:
    st.header(CONTROL["irb_consent"]["title"])
    for p in CONTROL["irb_consent"]["text"]:
        st.write(p)
    if st.checkbox("I have read the information.") and st.checkbox("I consent to participate."):
        if st.button("Proceed"):
            st.session_state.consent_given = True
            log_event("consent_accepted", {})
            st.rerun()
    st.stop()

if not st.session_state.contact_collected:
    st.header(CONTROL["contact_screen"]["title"])
    for p in CONTROL["contact_screen"]["text"]:
        st.write(p)
    email = st.text_input("Email (optional)")
    phone = st.text_input("Phone (optional)")
    if st.button("Continue"):
        st.session_state.contact_collected = True
        log_event("contact_collected", {"email": email, "phone": phone})
        st.rerun()
    st.stop()

if st.session_state.show_intro:
    intro = CONTROL["scenario_description"]
    st.header(intro["title"])
    c1, c2 = st.columns(2)
    if "image_house" in intro:
        c1.image(intro["image_house"])
    if "image_map" in intro:
        c2.image(intro["image_map"])
    for p in intro["text"]:
        st.write(p)
    if st.button("Start Scenario"):
        st.session_state.show_intro = False
        log_event("scenario_started", {})
        st.rerun()
    st.stop()

# ======================================================
# 6. ASSESSMENT SCREEN
# ======================================================
if st.session_state.in_assessment:
    st.subheader("Situation Assessment")

    results = {
        v: st.slider(v, 0, 100, 50)
        for v in SUBJECTIVE_VARS
    }

    if st.button("Continue to Decisions"):
        log_event(
            "assessment_time_spent",
            {"duration_seconds": (datetime.datetime.now() - st.session_state.assessment_start_time).total_seconds()}
        )
        st.session_state.cached_assessment = results
        st.session_state.in_assessment = False
        st.session_state.in_decision = True
        st.session_state.decision_start_time = datetime.datetime.now()
        st.rerun()
    st.stop()

# ======================================================
# 7. DECISION SCREEN (PREPARATION + EVACUATION)
# if there is no description in json it may show an empty line and an option
# ======================================================
if st.session_state.in_decision:
    st.subheader(f"Decisions — {get_time_label()}")

    st.markdown("### Preparation actions")

    # --------------------------------------------------
    # Simulated time budget for this scenario step
    # --------------------------------------------------
    used_minutes = st.session_state.prep_minutes_this_step
    remaining_hour = minutes_remaining_in_hour()
    remaining_cap = minutes_remaining_to_cap()

    t1, t2, t3 = st.columns(3)
    t1.metric("Minutes used", f"{used_minutes} min")
    t2.metric("Minutes left in hour", f"{remaining_hour} min")
    t3.metric("Minutes left until maximum", f"{remaining_cap} min")

    st.progress(min(used_minutes / MAX_MINUTES, 1.0))

    if used_minutes > NOMINAL_MINUTES:
        st.warning(
            f"You are {used_minutes - NOMINAL_MINUTES} minutes beyond the "
            f"{NOMINAL_MINUTES}-minute hour. No action may push the total "
            f"past {MAX_MINUTES} minutes."
        )
    else:
        st.caption(
            f"Preparation actions may use up to {MAX_MINUTES} minutes in this step "
            f"({NOMINAL_MINUTES} minutes plus a "
            f"{MAX_MINUTES - NOMINAL_MINUTES}-minute allowance)."
        )

    if st.session_state.prep_actions_this_step:
        st.markdown("#### Actions selected this hour")
        running_total = 0
        for seq_num, action_id in enumerate(st.session_state.prep_actions_this_step, start=1):
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

        col1, col2, col3 = st.columns([4, 1, 1])

        with col1:
            st.write(f"**{action['label']}**")
            if action.get("description"):
                st.caption(action["description"])

        with col2:
            st.write(f"{duration} min")

        with col3:
            if done_permanently:
                st.write("Completed")
            elif done_this_step:
                st.write("Done this hour")
            else:
                if st.button(
                    "Perform action",
                    key=f"prep_{action['id']}_{st.session_state.time_index}",
                    disabled=not fits_time
                ):
                    minutes_before = st.session_state.prep_minutes_this_step
                    st.session_state.prep_minutes_this_step += duration
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
                            "estimated_time_minutes": duration,
                            "sequence_in_step": len(st.session_state.prep_actions_this_step),
                            "minutes_before_action": minutes_before,
                            "minutes_used_this_step": st.session_state.prep_minutes_this_step,
                            "minutes_remaining_in_hour": minutes_remaining_in_hour(),
                            "minutes_remaining_to_cap": minutes_remaining_to_cap(),
                            "occurrence": st.session_state.prep_action_counts[action["id"]]
                        }
                    )
                    st.rerun()

                if not fits_time:
                    st.caption(
                        f"Not enough time: {minutes_remaining_to_cap()} min remain."
                    )

    st.divider()

    st.markdown("### Evacuation decision")

    is_final_step = st.session_state.time_index == len(TIME_STEPS) - 1

    evac_all = st.button("Evacuate now")
    evac_fam = st.button("Ask a neighbor to evacuate kids and dog")
    stay_label = "Stay despite GO order" if is_final_step else "Stay and continue"
    stay = st.button(stay_label)

    if evac_all or evac_fam or stay:
        log_event(
            "decision_time_spent",
            {"duration_seconds": (datetime.datetime.now() - st.session_state.decision_start_time).total_seconds()}
        )

        choice = "stay"
        if evac_all:
            choice = "evacuate_all"
        if evac_fam:
            choice = "evacuate_family"
        st.session_state.final_choice = choice

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
                "cumulative_minutes": running_total
            })

        log_event(
            "hourly_decision",
            {
                "time_step_minutes": TIME_STEP_MINUTES,
                "simulated_time": get_time_label(),
                "scores": st.session_state.cached_assessment,
                "choice": choice,
                "prep_action_sequence": action_sequence,
                "prep_minutes_this_step": st.session_state.prep_minutes_this_step,
                "minutes_beyond_nominal_hour": max(
                    0,
                    st.session_state.prep_minutes_this_step - NOMINAL_MINUTES
                ),
                "completed_nonrepeatable_actions": sorted(
                    st.session_state.completed_prep_actions
                ),
                "prep_action_counts": dict(st.session_state.prep_action_counts)
            }
        )

        st.session_state.in_decision = False
        st.session_state.cached_assessment = None

        # Information panels must never carry over into a new scenario step.
        # Exposure should already have been finalized before assessment; this
        # is a defensive state reset and intentionally does not log again.
        clear_open_information_state()

        st.session_state.time_index += 1

        # A new scenario step receives a fresh action budget. Repeatable actions
        # can reappear if their JSON availability window includes the new step.
        st.session_state.prep_actions_this_step = []
        st.session_state.prep_minutes_this_step = 0

        if is_end_of_time_window():
            st.session_state.scenario_ended = True
        st.session_state.dashboard_start_time = datetime.datetime.now()
        st.session_state.tiles_opened_this_step.clear()
        st.session_state.viewed_updates.clear()

        if choice in ["evacuate_all", "evacuate_family"]:
            st.session_state.scenario_ended = True
            st.session_state.scenario_end_reason = "evacuated"
        elif is_final_step:
            st.session_state.scenario_end_reason = "stayed_after_final_go"

        if st.session_state.scenario_ended:
            log_event(
                "scenario_outcome",
                {
                    "final_choice": st.session_state.final_choice,
                    "end_reason": st.session_state.scenario_end_reason,
                    "final_simulated_time": "08:00 PM"
                }
            )

        st.rerun()
    st.stop()

# ======================================================
# SCENARIO END HANDLING
# ======================================================
if st.session_state.scenario_ended:
    st.header("Scenario Complete")
    if st.session_state.scenario_end_reason == "evacuated":
        st.success("Your decision to evacuate has been recorded.")
    elif st.session_state.scenario_end_reason == "stayed_after_final_go":
        st.success("Your decision to stay after the final GO alert has been recorded.")
    else:
        st.success("Thank you for participating in this evacuation scenario!")

    # Email results
    results_file = f"results/{st.session_state.session_id}.json"
    try:
        email_results_file(results_file)
        st.info("✅ Your decisions have been automatically recorded.")
    except Exception as e:
        st.error(f"Note: Could not send results email. Error: {e}")

    st.write("You may now close this window.")
    st.stop()




# ======================================================
# 8. MAIN DASHBOARD
# ======================================================
st.header(f"{TITLE} — {get_time_label()}")

# Display information panel at TOP if tile is open
if st.session_state.open_tile:
    tile = TILES[st.session_state.open_tile]
    content = tile["content"].get(str(CURRENT_TIME_VAL))

    # Information panel with prominent styling header
    st.markdown(
        f"<h4 style='margin: 0 0 10px 0; font-size: 20px; color: #333;'>{tile['label']}</h4>",
        unsafe_allow_html=True
    )

    # Display content with larger font
    if tile.get("type") == "social_contacts":
        for c in tile["contacts"]:
            if st.button(f"Message {c['name']}", key=f"soc_{c['id']}", use_container_width=True):
                if st.session_state.current_social_contact:
                    log_event(
                        "social_message_time_spent",
                        {
                            "contact": st.session_state.current_social_contact,
                            "duration_seconds": (
                                    datetime.datetime.now() - st.session_state.social_open_time
                            ).total_seconds()
                        }
                    )
                reply = CONTROL["social_response_policies"][c["response_policy"]][str(CURRENT_TIME_VAL)]
                st.session_state.current_social_contact = c["name"]
                st.session_state.social_open_time = datetime.datetime.now()
                log_event("social_message_opened", {"contact": c["name"]})
                log_event("social_interaction", {"to": c["name"], "reply": reply})
                st.info(reply)
    else:
        if content is not None:
            if "text" in content:
                st.markdown(f'<div style="font-size: 20px; line-height: 1.6; color: #333;">{content["text"]}</div>',
                            unsafe_allow_html=True)
            if "image" in content:
                img = content.get("image")

                # Only attempt to render if it's a non-empty string
                if isinstance(img, str) and img.strip():
                    img = img.strip().replace("\\", "/")  # normalize Windows paths
                    st.image(img)
                # else: skip silently (or show a placeholder)
        else:
            st.markdown('<div style="font-size: 16px; color: #666;">No information available at this time.</div>',
                        unsafe_allow_html=True)

    # Close button - prominent red styling
    if st.button("Close", key="close_modal", use_container_width=False):
        finalize_open_information("user_closed")
        st.rerun()

    st.divider()

# Display tile grid
st.subheader("Information Sources")
# Lock tile selection while a tile is open (forces user to press Close)
tile_lock = st.session_state.open_tile is not None
if tile_lock:
    st.info("Close the current window to open another information source.")
# Create 4 rows of 4 tiles each
for row in range(4):
    cols = st.columns(4)
    for col_idx in range(4):
        tile_num = row * 4 + col_idx + 1
        if tile_num > 16:
            break

        tid = str(tile_num)
        label = TILES[tid]["label"]

        is_new = has_new_update(tid) and tid not in st.session_state.viewed_updates
        text = label

        with cols[col_idx]:

            if st.button(
                text,
                key=f"tile_{tid}_{st.session_state.time_index}",
                use_container_width=True,
                disabled=tile_lock
            ):

                # Open new tile
                st.session_state.open_tile = tid
                st.session_state.current_tile_id = tid
                st.session_state.tile_open_time = datetime.datetime.now()
                st.session_state.tiles_opened_this_step.add(tid)
                st.session_state.viewed_updates.add(tid)

                # Reset social contact tracking
                st.session_state.current_social_contact = None
                st.session_state.social_open_time = None

                log_event("tile_viewed", {"id": tid, "label": label})
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

if st.button(
    "Go to Assessment",
    disabled=assessment_disabled,
    use_container_width=True
):
    # Defensive finalization: normally the tile was already closed explicitly.
    finalize_open_information("assessment_started")

    log_event(
        "dashboard_time_spent",
        {
            "duration_seconds": (
                    datetime.datetime.now()
                    - st.session_state.dashboard_start_time
            ).total_seconds()
        }
    )
    st.session_state.in_assessment = True
    st.session_state.assessment_start_time = datetime.datetime.now()

    st.rerun()

