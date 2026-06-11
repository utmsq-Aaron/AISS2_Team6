from core.orchestrator import _SYSTEM
idx = _SYSTEM.find('VIZ TAGS')
print(_SYSTEM[idx:idx+900])
print("---")
# Verify format works
formatted = _SYSTEM.format(today="2026-06-11")
idx2 = formatted.find('VIZ TAGS')
print(formatted[idx2:idx2+900])
