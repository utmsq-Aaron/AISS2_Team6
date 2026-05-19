import os
import streamlit as st
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
from tools import TOOLS


load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY") or "",
    base_url=os.getenv("OPENAI_BASE_URL"),
)
MODEL: str = os.getenv("AGENT_MODEL") or ""

LOCATION = "Karlsruhe"


def extract_purpose(content):
    in_purpose = False
    for line in content.splitlines():
        if line.strip() == "## Purpose":
            in_purpose = True
            continue
        if in_purpose and line.strip():
            return line.strip()
    return ""

skills = {}
for skill_file in sorted(Path("skills").glob("*.md")):
    content = skill_file.read_text(encoding="utf-8")
    skills[skill_file.name] = {
        "description": extract_purpose(content),
        "content": content,
    }


def choose_skill(user_message):
    skill_menu = "\n".join(
        f"- {name}: {details['description']}"
        for name, details in skills.items()
    )
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You choose which skill file an agent should use. "
                    "Return exactly one skill file name from the list and nothing else."
                ),
            },
            {
                "role": "user",
                "content": f"User message: {user_message}\n\nAvailable skills:\n{skill_menu}",
            },
        ],
    )
    chosen = (response.choices[0].message.content or "").strip()
    if chosen not in skills:
        chosen = "general_wisdom.md"
    return chosen


def run_agent(user_message):
    skill_name = choose_skill(user_message)
    skill_content = skills[skill_name]["content"]

    tool_fn = TOOLS.get(skill_name, lambda: "")
    tool_result = tool_fn()

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0.3,
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a helpful assistant for {LOCATION}. "
                    f"Follow these instructions:\n\n{skill_content}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User question: {user_message}\n\n"
                    f"Current data from tool:\n{tool_result}"
                ),
            },
        ],
    )
    return (response.choices[0].message.content or "").strip(), skill_name


st.title("Weather, Pollen & UV Assistant")
st.caption(f"Powered by Open-Meteo · Location: {LOCATION}")

SKILL_LABELS = {
    "check_weather.md": "Weather",
    "check_pollen.md": "Pollen",
    "check_uv_index.md": "UV Index",
    "general_wisdom.md": "Life Wisdom",
}


def skill_caption(skill):
    label = SKILL_LABELS.get(skill, skill)
    if skill == "general_wisdom.md":
        return f"Mode: {label} — outside environmental scope"
    return f"Skill used: {label}"

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            st.caption(skill_caption(message["skill"]))

if prompt := st.chat_input("Ask about weather, pollen, or UV in Karlsruhe..."):

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Fetching data and generating answer..."):
            answer, skill_used = run_agent(prompt)
        st.markdown(answer)
        st.caption(skill_caption(skill_used))

    st.session_state.messages.append({
        "role": "assistant",
        "content": answer,
        "skill": skill_used,
    })
