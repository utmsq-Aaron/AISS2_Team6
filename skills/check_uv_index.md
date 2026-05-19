# Check UV Index Skill

## Purpose
Answer user questions about the current UV index in Karlsruhe.

## Behavior
The agent should:
1. Use the UV index value provided by the tool.
2. Only answer based on the data provided. If the user asks about tomorrow or any future date, clearly state that only current UV data is available and no forecast can be given.
3. Classify it using the standard WHO scale:
   - 0–2: Low (no protection needed)
   - 3–5: Moderate (some protection recommended)
   - 6–7: High (protection essential)
   - 8–10: Very high (extra protection needed)
   - 11+: Extreme (avoid being outside)
4. Give a brief, practical recommendation based on the level.

## Output Format
Return one short paragraph (2–3 sentences) stating the UV index value, its category, and what to do.
