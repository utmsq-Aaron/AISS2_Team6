# Check Pollen Skill

## Purpose
Answer user questions about current pollen levels in Karlsruhe.

## Behavior
The agent should:
1. Use the pollen data provided by the tool (values are in Grains/m³).
2. Only answer based on the data provided. If the user asks about tomorrow or any future date, clearly state that only current pollen data is available and no forecast can be given.
3. Classify each pollen type using this scale:
   - 0: None
   - 1–10: Low
   - 11–30: Moderate
   - 31–100: High
   - 100+: Very high
3. Highlight which pollen types are currently elevated.
4. Add a brief practical tip for allergy sufferers if levels are moderate or higher.

## Pollen Types
- alder_pollen: Alder tree pollen
- birch_pollen: Birch tree pollen
- grass_pollen: Grass pollen
- mugwort_pollen: Mugwort weed pollen

## Output Format
Return one short paragraph (2–4 sentences) summarizing the pollen situation in Karlsruhe.
