import streamlit as st
import json
import time
import datetime
from datetime import timedelta
import uuid
import os
st.write("CONTROL FILE LOADED FROM:", os.path.abspath("control.json"))


# >>> ADDED IMPORTS (EMAIL)
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
# <<< END ADDED IMPORTS

# ======================================================
# LOAD CONTROL FILE
# ======================================================

with open("control.json", "r") as f:
    CONTROL = json.load(f)

TITLE = CONTROL["title"]
TIME_STEPS = CONTROL["time_steps"]
TILES = CONTROL["tiles"]

SUBJECTIVE_VARS = [
    "Risk perception; 0 no risk, 100 very high risk",
    "Decision time pressure; 0 no time pressure, 100 extreme time pressure",
    "Trust in official alerts; 0 no trust, 100 very high trust",
    "Anxiety level; 0 no anxiety, 100 very high anxiety",
    "Social pressure; 0 no pressure, 100 extreme social pressure",
    "Evacuation feasibility; 0 no feasibility, 100 very high feasibility",
    "Decision leaning; 0-50 leaning towards stay 51-100 leaning towards evacuate)",
]

# ======================================================
# SESSION STATE
# ======================================================

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "consent_given" not in st.session_state:
    st.session_state.consent_given = False

if "contact_collected" not in st.session_state:
    st.session_state.contact_collected = False

if "participant_info" not in st.session_state:
    st.session_state.participant_info = {}

if "show_intro" not in st.session_state:
    st.session_state.show_intro = True

if "time_index" not in st.session_state:
    st.session_state.time_index = 0
    st.session_state.open_tile = None
    st.session_state.tile_open_time = None
    st.session_state.awaiting_assessment = False
    st.session_state.tiles_opened_this_step = set()
    st.session_state.logs = []
    st.session_state.slider_seed = 0
    st.session_state.scenario_ended = False
    st.session_state.family_evacuated = False

# >>> ADDED SESSION FLAG (EMAIL)
if "results_emailed" not in st.session_state:
    st.session_state.results_emailed = False
# <<< END ADDED FLAG

CURRENT_TIME = TIME_STEPS[st.session_state.time_index]

# ======================================================
# LOGGING (AUTO SAVE)
# ======================================================

LOG_DIR = "results"
os.makedirs(LOG_DIR, exist_ok=True)

def save_logs():
    path = os.path.join(LOG_DIR, f"{st.session_state.session_id}.json")
    with open(path, "w") as f:
        json.dump(st.session_state.logs, f, indent=2)

def log_event(event, payload):
    st.session_state.logs.append({
        "time_step": CURRENT_TIME,
        "event": event,
        **payload,
        "timestamp": datetime.datetime.now().isoformat()
    })
    save_logs()

# ======================================================
# EMAIL RESULTS (AUTO)
# ======================================================

def email_results_file(results_path):
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
# HELPERS
# ======================================================

def display_time_label(time_index):
    start_time_str = CONTROL.get("start_time_display", "14:00")
    start_time = datetime.datetime.strptime(start_time_str, "%H:%M")
    current_time = start_time + timedelta(hours=time_index)

    hour = current_time.strftime("%I").lstrip("0") or "12"
    minute = current_time.strftime("%M")
    ampm = current_time.strftime("%p")

    return f"{hour}:{minute} {ampm}"

def get_tile_content(tile_id):
    return TILES[tile_id]["content"].get(str(CURRENT_TIME))

def tile_has_update_or_content(tile_id):
    current = TILES[tile_id]["content"].get(str(CURRENT_TIME))

    if st.session_state.time_index == 0:
        return current is not None

    prev_time = TIME_STEPS[st.session_state.time_index - 1]
    previous = TILES[tile_id]["content"].get(str(prev_time))

    return current != previous

def get_social_response(policy_name):
    """
    Returns a deterministic social contact response
    based on the current time step and policy name.
    """
    policies = CONTROL.get("social_response_policies", {})
    policy = policies.get(policy_name, {})

    return policy.get(str(CURRENT_TIME), "No response received.")

# ======================================================
# PAGE SETUP + CSS
# ======================================================

st.set_page_config(layout="wide")

st.markdown(
    """
    <style>
    div[data-testid="column"] button {
        height: 110px !important;
        width: 100% !important;
        font-size: 15px !important;
        padding: 10px !important;
        margin: 0 !important;
        display: -webkit-box !important;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
        text-align: center;
    }
    button[aria-label="Close"] {
        display: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# ======================================================
# IRB CONSENT SCREEN
# ======================================================

if not st.session_state.consent_given:
    irb = CONTROL.get("irb_consent", {})

    st.markdown(f"## {irb.get('title', 'Consent')}")

    for p in irb.get("text", []):
        st.write(p)

    read_ok = st.checkbox("I have read and understood the information above.")
    consent_ok = st.checkbox("I consent to participate in this study.")

    if st.button("Continue"):
        if read_ok and consent_ok:
            st.session_state.consent_given = True
            log_event("consent", {"consent": True})
            st.rerun()
        else:
            st.warning("Consent is required to continue.")

    st.stop()

# ======================================================
# CONTACT INFORMATION SCREEN
# ======================================================

if st.session_state.consent_given and not st.session_state.contact_collected:
    contact_cfg = CONTROL.get("contact_screen", {})

    st.markdown(f"## {contact_cfg.get('title', 'Contact Information')}")

    for p in contact_cfg.get("text", []):
        st.write(p)

    email = st.text_input("Email (optional)")
    phone = st.text_input("Phone number (optional)")

    if st.button("Continue"):
        st.session_state.participant_info = {
            "email": email,
            "phone": phone
        }
        log_event("contact_info", st.session_state.participant_info)
        st.session_state.contact_collected = True
        st.rerun()

    st.stop()


# ======================================================
# SCENARIO INTRO SCREEN
# ======================================================


if st.session_state.show_intro:
    intro = CONTROL.get("scenario_description", {})

    st.markdown(f"## {intro.get('title', 'Scenario')}")
    spacer_left, col1, col2, spacer_right = st.columns([1, 2, 2, 1])
    # Show intro images side by side
    if "image_house" in intro and "image_map" in intro:

        with col1:
            _, img_col, _ = st.columns([1, 2, 1])
            with img_col:
                st.image(intro["image_house"], width=300)
                st.caption("Your home")
        with col2:
            _, img_col, _ = st.columns([1, 2, 1])
            with img_col:
              st.image(intro["image_map"], width=300)
              st.caption("Neighborhood map")
    # Show intro text
    for p in intro.get("text", []):
        st.write(p)

    if st.button("BEGIN"):
        st.session_state.show_intro = False
        st.rerun()

    st.stop()


# ======================================================
# SCENARIO END SCREEN (AUTO EMAIL)
# ======================================================

if st.session_state.scenario_ended:
    st.markdown("## Scenario Complete")
    st.write("You have chosen to evacuate. This ends the scenario.")

    results_path = os.path.join(
        LOG_DIR, f"{st.session_state.session_id}.json"
    )

    if not st.session_state.results_emailed:
        try:
            email_results_file(results_path)
            log_event("results_emailed", {"recipient": "fadrews@gmail.com"})
            st.session_state.results_emailed = True
        except Exception as e:
            log_event("results_email_failed", {"error": str(e)})

    st.stop()

# ======================================================
# MAIN HEADER
# ======================================================

st.title(TITLE)
st.subheader(f"Time: {display_time_label(st.session_state.time_index)}")
st.markdown("---")

# ======================================================
# TILE GRID
# ======================================================

if not st.session_state.awaiting_assessment:
    cols = st.columns(4)

    for i in range(1, 17):
        tile_id = str(i)
        label = TILES[tile_id]["label"] if tile_id in TILES else f"Tile {i}"

        with cols[(i - 1) % 4]:
            if st.button(f"{i}. {label}", key=f"tile_{i}_{CURRENT_TIME}"):
                st.session_state.open_tile = tile_id
                st.session_state.tile_open_time = time.time()
                st.session_state.tiles_opened_this_step.add(tile_id)

                log_event(
                    "tile_opened",
                    {
                        "tile_id": tile_id,
                        "tile_label": label
                    }
                )

# ======================================================
# TILE POPUP
# ======================================================

if st.session_state.open_tile:
    tile_id = st.session_state.open_tile
    tile = TILES[tile_id]
    content = get_tile_content(tile_id)

    @st.dialog(tile["label"])
    def tile_modal():

        # ---- SOCIAL CONTACTS TILE ----
        if tile.get("type") == "social_contacts":
            contacts = tile.get("contacts", [])
#            st.write("You can send a message to:")
#            st.write("DEBUG contacts =", contacts)
            for contact in contacts:
                name = contact["name"]
                policy = contact["response_policy"]
                contact_id = contact["id"]

                if st.button(f"Text {name}", key=f"contact_{contact_id}_{CURRENT_TIME}"):

                    log_event(
                        "social_message_sent",
                        {
                            "to": name,
                            "contact_id": contact_id,
                            "message_type": "evacuation_advice"
                        }
                    )

                    time.sleep(1.2)

                    response = get_social_response(policy)

                    st.info(f"{name} replies:\n\n{response}")

                    log_event(
                        "social_response_received",
                        {
                            "from": name,
                            "contact_id": contact_id,
                            "response_policy": policy,
                            "response": response
                        }
                    )

        # ---- STANDARD TILE ----
        else:
            if content:
                st.write(content.get("text", ""))
                if "image" in content:
                    st.image(content["image"], width=500)
            else:
                st.info("No information available.")

        if st.button("Close"):
            duration = round(time.time() - st.session_state.tile_open_time, 2)
            log_event("tile_closed", {"tile_id": tile_id, "duration_seconds": duration})
            st.session_state.open_tile = None
            st.session_state.tile_open_time = None
            st.rerun()

    tile_modal()

# ======================================================
# PROCEED BUTTON
# ======================================================

if not st.session_state.awaiting_assessment and st.session_state.open_tile is None:
    st.button(
        "Proceed",
        disabled=len(st.session_state.tiles_opened_this_step) == 0,
        on_click=lambda: setattr(st.session_state, "awaiting_assessment", True)
    )

# ======================================================
# ASSESSMENT + DECISIONS
# ======================================================

if st.session_state.awaiting_assessment:
    st.markdown("## Assessment")

    responses = {
        var: st.slider(var, 0, 100, 50, step=1, key=f"{var}_{st.session_state.slider_seed}")
        for var in SUBJECTIVE_VARS
    }

    st.markdown("### Decision")

    decision = None
    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Evacuate now, including family members if still present"):
            decision = "evacuate_everyone_now"

    with col2:
        if not st.session_state.family_evacuated:
            if st.button("Evacuate family only"):
                decision = "evacuate_family_only"

    with col3:
        if st.button("Stay for now"):
            decision = "stay_for_now"

    if decision:
        log_event("assessment", {"responses": responses, "decision": decision})

        if decision == "evacuate_everyone_now":
            st.session_state.scenario_ended = True
            st.session_state.awaiting_assessment = False
            st.rerun()
            st.stop()

        if decision == "evacuate_family_only":
            st.session_state.family_evacuated = True

        st.session_state.slider_seed += 1
        st.session_state.awaiting_assessment = False
        st.session_state.tiles_opened_this_step.clear()
        st.session_state.time_index += 1

        if st.session_state.time_index >= len(TIME_STEPS):
            st.session_state.scenario_ended = True

        st.rerun()
