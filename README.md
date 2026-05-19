# Weather, Pollen & UV Assistant

A Streamlit-based chatbot for weather, pollen, and UV index information in Karlsruhe, powered by [Open-Meteo](https://open-meteo.com/).

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

Create a `.env` file in the project root. Choose one of the providers below:

**KI-Toolbox (KIT)**
```env
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://ki-toolbox.scc.kit.edu/api/v1
AGENT_MODEL=azure.gpt-4o-mini
```

**AI-Gateway (DSI)**
```env
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://ai-gateway.dsi-experimente.de
AGENT_MODEL=kit.gpt-4o-mini
```

### 3. Run the app

```bash
streamlit run app.py
```

The app will open at `http://localhost:8501`.
