import streamlit as st
import pandas as pd
import os
import bcrypt
import requests
from datetime import datetime, timedelta
import plotly.express as px
from io import BytesIO
from PIL import Image, ImageDraw, ImageFilter
import math

# ----------------------------
# App Setup
# ----------------------------
st.set_page_config(page_title="SmartHarvest Portal", page_icon="🌾", layout="wide")

# Directories
USER_DIR = "users"
os.makedirs(USER_DIR, exist_ok=True)

# ----------------------------
# Helper Functions
# ----------------------------
def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def check_password(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed.encode())

def get_user_path(username):
    path = os.path.join(USER_DIR, username)
    os.makedirs(path, exist_ok=True)
    return path

def load_df(username, key, columns):
    path = os.path.join(get_user_path(username), f"{key}.csv")
    if os.path.exists(path):
        return pd.read_csv(path)
    return pd.DataFrame(columns=columns)

def save_df(username, key, df):
    path = os.path.join(get_user_path(username), f"{key}.csv")
    df.to_csv(path, index=False)

def load_users():
    users_file = os.path.join(USER_DIR, "users.csv")
    if os.path.exists(users_file):
        df = pd.read_csv(users_file)
        # Migrate plaintext passwords to bcrypt hashes if needed
        changed = False
        for idx, row in df.iterrows():
            pwd = str(row.get("password", ""))
            # bcrypt hashes start with $2b$ or $2a$ or $2y$
            if not pwd.startswith("$2"):
                try:
                    # treat stored value as plaintext and hash it
                    hashed = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode()
                    df.at[idx, "password"] = hashed
                    changed = True
                except Exception:
                    # if something goes wrong, leave as-is
                    pass
        if changed:
            df.to_csv(users_file, index=False)
        return df
    return pd.DataFrame(columns=["username", "password"])

def save_users(df):
    df.to_csv(os.path.join(USER_DIR, "users.csv"), index=False)

# ----------------------------
# Authentication
# ----------------------------
def register_user(username, password):
    users = load_users()
    if username in users["username"].values:
        st.warning("Username already exists.")
        return False
    hashed = hash_password(password)
    new_user = pd.DataFrame([[username, hashed]], columns=["username", "password"])
    users = pd.concat([users, new_user], ignore_index=True)
    save_users(users)
    get_user_path(username)
    st.success("Account created! Please log in.")
    return True

def login_user(username, password):
    users = load_users()
    user_row = users[users["username"] == username]
    if not user_row.empty:
        hashed = user_row.iloc[0]["password"]
        if check_password(password, hashed):
            return True
    return False

# ----------------------------
# Weather utilities
# ----------------------------
def fetch_weather(lat, lon, hours=48):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,precipitation,cloudcover,weathercode",
        "timezone": "UTC",
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def geocode_place(place_name):
    """Use Open-Meteo geocoding to resolve a city/place name to lat/lon."""
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": place_name, "count": 1}
    r = requests.get(url, params=params, timeout=8)
    r.raise_for_status()
    j = r.json()
    results = j.get("results")
    if not results:
        return None
    return results[0]["latitude"], results[0]["longitude"], results[0].get("name"), results[0].get("country")

def will_rain_in_next_hours(weather_json, hours=6):
    hourly = weather_json.get("hourly", {})
    precipitation = hourly.get("precipitation", [])
    times = hourly.get("time", [])
    for i in range(min(hours, len(precipitation))):
        if float(precipitation[i]) > 0:
            return True, times[i]
    return False, None

def cloud_visual(cloudcover):
    if cloudcover >= 75:
        return "🌧️ Dark Clouds"
    elif cloudcover >= 30:
        return "🌥️ Partly Cloudy"
    else:
        return "☀️ Clear Skies"


def generate_cloud_image(cloudcover: float, width=640, height=240) -> BytesIO:
    """Generate a simple cloud illustration whose appearance depends on cloudcover (0-100).

    Returns a BytesIO PNG image.
    """
    # Normalize
    c = max(0, min(100, float(cloudcover)))
    # background color shifts darker with more clouds
    sky_clear = (135, 206, 235)
    sky_overcast = (70, 80, 90)
    mix = lambda a, b, t: tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))
    t = c / 100.0
    bg = mix(sky_clear, sky_overcast, t)

    img = Image.new("RGBA", (width, height), bg + (255,))
    draw = ImageDraw.Draw(img, "RGBA")

    # Draw layered clouds: number and darkness depend on c
    cloud_color_light = (255, 255, 255, 230)
    cloud_color_dark = (180, 180, 190, 255)

    # determine number of cloud clusters
    clusters = 1 + int((c / 100.0) * 3)
    for cl in range(clusters):
        base_x = int((cl + 0.5) * width / (clusters + 0.5))
        base_y = int(height * (0.35 + 0.25 * (cl % 2)))
        size = int(width * (0.25 + 0.12 * cl))
        layers = 6
        for i in range(layers):
            radius = size * (0.6 + 0.1 * (i / layers))
            offset_x = int((i - layers/2) * (size * 0.12))
            offset_y = int(((i % 2) - 0.5) * (size * 0.08))
            bbox = [
                base_x - radius + offset_x,
                base_y - radius * 0.6 + offset_y,
                base_x + radius + offset_x,
                base_y + radius * 0.6 + offset_y,
            ]
            # choose color interpolated between light and dark depending on cloudcover
            dark_t = 0.3 + 0.7 * t
            color = mix(cloud_color_light[:3], cloud_color_dark[:3], dark_t)
            draw.ellipse(bbox, fill=tuple(list(color) + [230]))

    # subtle blur to smooth edges
    img = img.filter(ImageFilter.GaussianBlur(radius=4))

    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio

# ----------------------------
# App Logic
# ----------------------------
st.markdown("<h1 style='text-align:center;'>🌾 SmartHarvest Portal</h1>", unsafe_allow_html=True)

# Premium-ish small CSS
st.markdown(
    """
    <style>
    .stApp { background: linear-gradient(180deg, #f7fbff 0%, #ffffff 100%); }
    .header { text-align: center; }
    .reportview-container .main { color: #111827; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "username" not in st.session_state:
    st.session_state.username = ""

# ----------------------------
# LOGIN & SIGNUP SCREENS
# ----------------------------
if not st.session_state.authenticated:
    tabs = st.tabs(["🔑 Login", "🧑‍🌾 Create Account"])

    # --- Login Tab ---
    with tabs[0]:
        st.subheader("Login to your account")
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")

        if st.button("Login"):
            if login_user(username, password):
                st.session_state.authenticated = True
                st.session_state.username = username
                st.success(f"Welcome {username}! Redirecting...")
                st.rerun()
            else:
                st.error("Invalid username or password.")

    # --- Create Account Tab ---
    with tabs[1]:
        st.subheader("Create a new account")
        new_user = st.text_input("New Username")
        new_pass = st.text_input("New Password", type="password")
        confirm_pass = st.text_input("Confirm Password", type="password")

        if st.button("Create Account"):
            if not new_user or not new_pass:
                st.warning("Please enter all fields.")
            elif new_pass != confirm_pass:
                st.warning("Passwords do not match.")
            else:
                # ensure username safe
                ok = register_user(new_user, new_pass)
                if ok:
                    st.balloons()

else:
    username = st.session_state.username
    st.sidebar.success(f"Logged in as {username}")
    if st.sidebar.button("Logout"):
        st.session_state.authenticated = False
        st.session_state.username = ""
        st.rerun()

    # ----------------------------
    # App Pages
    # ----------------------------
    page = st.sidebar.radio("Navigation", ["Dashboard", "Records", "Weather", "About"])

    # Common Data
    DEFAULT_COLUMNS = {
        "expenses": ["Date", "Category", "Amount", "Notes"],
        "fertilizer": ["Date", "Crop", "Type", "Quantity_kg", "Notes"],
        "livestock": ["Date", "Animal_Type", "Count", "Health_Notes"],
        "yield": ["Date", "Crop", "Area_ha", "Yield_kg", "Notes"],
    }

    # ----------------------------
    # RECORDS PAGE
    # ----------------------------
    if page == "Records":
        st.header("📒 Farm Records")

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Expenses")
            df_exp = load_df(username, "expenses", DEFAULT_COLUMNS["expenses"])
            edited_exp = st.data_editor(df_exp, num_rows="dynamic", key="exp")
            if st.button("Save Expenses"):
                save_df(username, "expenses", edited_exp)
                st.success("Expenses saved.")

            st.subheader("Fertilizer")
            df_fert = load_df(username, "fertilizer", DEFAULT_COLUMNS["fertilizer"])
            edited_fert = st.data_editor(df_fert, num_rows="dynamic", key="fert")
            if st.button("Save Fertilizer"):
                save_df(username, "fertilizer", edited_fert)
                st.success("Fertilizer saved.")

        with col2:
            st.subheader("Livestock")
            df_liv = load_df(username, "livestock", DEFAULT_COLUMNS["livestock"])
            edited_liv = st.data_editor(df_liv, num_rows="dynamic", key="liv")
            if st.button("Save Livestock"):
                save_df(username, "livestock", edited_liv)
                st.success("Livestock saved.")

            st.subheader("Yield")
            df_yield = load_df(username, "yield", DEFAULT_COLUMNS["yield"])
            edited_yield = st.data_editor(df_yield, num_rows="dynamic", key="yield")
            if st.button("Save Yield"):
                save_df(username, "yield", edited_yield)
                st.success("Yield saved.")

    # ----------------------------
    # DASHBOARD PAGE
    # ----------------------------
    elif page == "Dashboard":
        st.header("📊 Dashboard Overview")
        df_exp = load_df(username, "expenses", DEFAULT_COLUMNS["expenses"])
        df_yield = load_df(username, "yield", DEFAULT_COLUMNS["yield"])
        df_fert = load_df(username, "fertilizer", DEFAULT_COLUMNS["fertilizer"])
        df_liv = load_df(username, "livestock", DEFAULT_COLUMNS["livestock"])

        total_exp = df_exp["Amount"].astype(float).sum() if not df_exp.empty else 0
        total_yield = df_yield["Yield_kg"].astype(float).sum() if not df_yield.empty else 0
        total_fert = df_fert["Quantity_kg"].astype(float).sum() if not df_fert.empty else 0
        total_animals = df_liv["Count"].astype(float).sum() if not df_liv.empty else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Expenses", f"₹{total_exp:,.2f}")
        c2.metric("Total Yield (kg)", f"{total_yield}")
        c3.metric("Fertilizer Used (kg)", f"{total_fert}")
        c4.metric("Livestock Count", f"{total_animals}")

        st.markdown("---")
        if not df_exp.empty:
            st.subheader("Expenses by Category")
            fig = px.pie(df_exp, names="Category", values="Amount", title="Expense Distribution")
            st.plotly_chart(fig, use_container_width=True)

        if not df_yield.empty:
            st.subheader("Yield Over Time")
            df_yield["Date"] = pd.to_datetime(df_yield["Date"])
            fig2 = px.line(df_yield, x="Date", y="Yield_kg", color="Crop", title="Yield Trend")
            st.plotly_chart(fig2, use_container_width=True)

    # ----------------------------
    # WEATHER PAGE
    # ----------------------------
    elif page == "Weather":
        st.header("🌦️ Weather Forecast")
        lat = st.text_input("Latitude", "12.9716")
        lon = st.text_input("Longitude", "77.5946")

        if st.button("Get Weather"):
            try:
                weather = fetch_weather(float(lat), float(lon))
                rain, time_ = will_rain_in_next_hours(weather, hours=6)
                if rain:
                    st.error(f"🌧️ Rain predicted within next 6 hours at {time_}")
                else:
                    st.success("☀️ No rain expected in next 6 hours.")

                hourly = weather["hourly"]
                df = pd.DataFrame({
                    "Time": hourly["time"],
                    "Precipitation(mm)": hourly["precipitation"],
                    "Cloudcover(%)": hourly["cloudcover"]
                })
                st.dataframe(df.head(24))

                fig = px.line(df.head(24), x="Time", y="Precipitation(mm)", title="Precipitation Next 24h")
                st.plotly_chart(fig, use_container_width=True)

                cloud_now = cloud_visual(df["Cloudcover(%)"][0])
                st.info(f"Current Cloud Condition: {cloud_now}")

            except Exception as e:
                st.error(f"Error fetching weather: {e}")

    # ----------------------------
    # ABOUT PAGE
    # ----------------------------
    elif page == "About":
        st.header("ℹ️ About SmartHarvest")
        st.write("""
        SmartHarvest is a digital farm management portal that helps farmers:
        - Record daily farm data (expenses, fertilizer, yield, livestock)
        - Visualize farm performance using interactive charts
        - Get live weather forecasts & rain alerts
        - Manage personalized data via login system
        """)

