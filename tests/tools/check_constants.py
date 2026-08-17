"""Print the orchestrator adapter's constants and public surface.

A quick "is the module shaped the way I think it is" check after touching
``core/orchestrator.py``. Reads only — no network, no LLM, exit code always 0.
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import core.orchestrator as o  # noqa: E402

# Public contract every caller (FastAPI SSE, Telegram bridge, evaluation) depends on.
print('run:', hasattr(o.FitDashOrchestrator, 'run'))
print('refresh_tools:', hasattr(o.FitDashOrchestrator, 'refresh_tools'))

# History flattening — A2A messages are single-shot, so recent turns are folded
# into one prompt (see _flatten_history).
print('HISTORY_WINDOW:', o.HISTORY_WINDOW)
print('HISTORY_CHAR_LIMIT:', o.HISTORY_CHAR_LIMIT)
print('LOG_FILE:', o.LOG_FILE)

# The orchestrator agent it delegates to, per the core.config registry.
print('orchestrator URL:', o.A2A_AGENTS['orchestrator'])

# _flatten_history behaviour on the two shapes that matter.
print('empty history → passthrough:',
      o._flatten_history([], 'how did I sleep?') == 'how did I sleep?')
print('with history → framed:',
      'Conversation so far' in o._flatten_history(
          [{'role': 'user', 'content': 'hi'}], 'and now?'))
