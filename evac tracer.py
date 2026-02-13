# v2.4 - Optimized UI latest version

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
TILES = CONTROL.get("tiles", {})
PREP_ACTIONS = CONTROL.get("preparation_actions", [])

SUBJECTIVE_VARS = [
    "Risk perception; 0 no risk, 100 very high risk",
    "Decision time pressure; 0 no time pressure, 100 extreme time pressure",
    "Trust in official alerts; 0 no trust, 100 very high trust",
    "Anxiety level; 0 no anxiety, 100 very high anxiety",
    "Social pressure; 0 no pressure, 100 extreme social pressure",
    "Evacuation feasibility; 0 no feasibility, 100 very high feasibility",
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
        st.session_state.completed_prep_actions = set()


init_state()

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


def email_results_file():
    results_path = Path(f"results/{st.session_state.session_id}.json")
    if not results_path.exists(): return

    # Access secrets
    sender_email = st.secrets.get("SENDER_EMAIL", "fadrews@gmail.com")
    password = st.secrets.get("EMAIL_PASSWORD", "spsu jamp ozlb pjue")  # Fallback for dev

    msg = EmailMessage()
    msg["Subject"] = f"Wildfire Scenario Results - {st.session_state.session_id}"
    msg["From"] = sender_email
    msg["To"] = sender_email
    msg.set_content(f"Results attached for session: {st.session_state.session_id}")

    with open(results_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="json", filename=results_path.name)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(sender_email, password)
        server.send_message(msg)


# ======================================================
# 4. HELPERS
# ======================================================
def close_current_tile():
    """Helper function to log timing when switching tiles"""
    if st.session_state.current_tile_id:
        log_event(
            "tile_time_spent",
            {
                "id": st.session_state.current_tile_id,
                "duration_seconds": (
                        datetime.datetime.now() - st.session_state.tile_open_time
                ).total_seconds()
            }
        )
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


def has_new_update(tid):
    if st.session_state.time_index == 0:
        return True
    curr = TILES[tid]["content"].get(str(CURRENT_TIME_VAL))
    prev = TILES[tid]["content"].get(
        str(TIME_STEPS[st.session_state.time_index - 1])
    )
    return curr != prev


def is_end_of_time_window():
    start = datetime.datetime.strptime(
        CONTROL.get("start_time_display", "14:00"), "%H:%M"
    )
    current = start + timedelta(hours=st.session_state.time_index)
    return current.hour >= 20


def get_time_label():
    start = datetime.datetime.strptime(
        CONTROL.get("start_time_display", "14:00"), "%H:%M"
    )
    return (start + timedelta(hours=st.session_state.time_index)).strftime("%I:%M %p")


def prep_available(action):
    return (
            CURRENT_TIME_VAL is not None and
            action["available_from"] <= CURRENT_TIME_VAL <= action["available_until"]
    )


def email_results_file(results_path):
    """Send results file via email"""
    sender_email = "fadrews@gmail.com"
    sender_app_password = "spsu jamp ozlb pjue"
    recipient_email = "fadrews@gmail.com"

    results_path = Path(results_path)
    if not results_path.exists():
        raise FileNotFoundError("Results file not found.")

    msg = EmailMessage()
    msg["Subject"] = "Wildfire Evacuation Scenario – Results"
    msg["From"] = sender_email
    msg["To"] = recipient_email

    msg.set_content(
        f"Attached is the results file for session {st.session_state.session_id}."
    )

    with open(results_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="json",
            filename=results_path.name
        )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(sender_email, sender_app_password)
        server.send_message(msg)


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

    for action in PREP_ACTIONS:
        if not prep_available(action):
            continue

        completed = action["id"] in st.session_state.completed_prep_actions
        col1, col2, col3 = st.columns([4, 1, 1])

        with col1:
            st.write(f"**{action['label']}**")
            st.caption(action["description"])

        with col2:
            st.write(f"{action['estimated_time_minutes']} min")

        with col3:
            if completed:
                st.write("Completed")
            else:
                if st.button("Perform action", key=f"prep_{action['id']}"):
                    st.session_state.completed_prep_actions.add(action["id"])
                    log_event(
                        "prep_action_completed",
                        {
                            "action_id": action["id"],
                            "estimated_time_minutes": action["estimated_time_minutes"]
                        }
                    )
                    st.rerun()

    st.divider()

    st.markdown("### Evacuation decision")

    evac_all = st.button("Evacuate all")
    evac_fam = st.button("Ask a neighbor to evacuate kids and dog")
    stay = st.button("Stay for now")

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

        log_event(
            "hourly_decision",
            {
                "scores": st.session_state.cached_assessment,
                "choice": choice,
                "completed_prep_actions": list(st.session_state.completed_prep_actions)
            }
        )

        st.session_state.in_decision = False
        st.session_state.cached_assessment = None
        st.session_state.time_index += 1
        if is_end_of_time_window():
            st.session_state.scenario_ended = True
        st.session_state.dashboard_start_time = datetime.datetime.now()
        st.session_state.tiles_opened_this_step.clear()
        st.session_state.viewed_updates.clear()

        if choice in ["evacuate_all", "evacuate_family"]:
            st.session_state.scenario_ended = True

        st.rerun()
    st.stop()

# ======================================================
# SCENARIO END HANDLING
# ======================================================
if st.session_state.scenario_ended:
    st.header("Scenario Complete")
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
                st.image(content["image"])
        else:
            st.markdown('<div style="font-size: 16px; color: #666;">No information available at this time.</div>',
                        unsafe_allow_html=True)

    # Close button - prominent red styling
    if st.button("Close", key="close_modal", use_container_width=False):
        # Log time for currently open tile/contact before closing
        close_current_tile()

        st.session_state.open_tile = None
        st.session_state.current_tile_id = None
        st.session_state.tile_open_time = None
        st.session_state.current_social_contact = None
        st.session_state.social_open_time = None
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

if st.button("Go to Assessment", disabled=len(st.session_state.tiles_opened_this_step) == 0, use_container_width=True):
    # Log time for currently open tile/contact before transitioning
    close_current_tile()

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

    # Reset tile/contact tracking
    st.session_state.current_tile_id = None
    st.session_state.tile_open_time = None
    st.session_state.current_social_contact = None
    st.session_state.social_open_time = None

    st.rerun()