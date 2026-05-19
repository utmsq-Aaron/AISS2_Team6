# Check Weather Skill

## Purpose
Answer user questions about the current weather in Karlsruhe.

## Behavior
The agent should:
1. Use the weather data provided by the tool.
2. Translate the numeric weather code into a plain-language description (e.g. "clear sky", "light rain").
3. Mention temperature and wind speed in a natural way.
4. Keep the answer short and conversational.
5. Only answer based on the data provided. If the user asks about tomorrow or any future date, clearly state that only current weather data is available and no forecast can be given.

## Weather Code Reference
- 0: Clear sky
- 1–3: Mainly clear to overcast
- 45, 48: Foggy
- 51–57: Drizzle
- 61–67: Rain
- 71–77: Snow
- 80–82: Rain showers
- 95–99: Thunderstorm

## Output Format
Return one short paragraph (2–3 sentences) describing the current weather in Karlsruhe.
