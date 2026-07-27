import sys
import io
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import core.orchestrator as o
print('refresh_tools:', hasattr(o.FitDashOrchestrator, 'refresh_tools'))
print('_ALWAYS_KEEP_KEYS:', o._ALWAYS_KEEP_KEYS)
print('HISTORY_CHAR_LIMIT:', o.HISTORY_CHAR_LIMIT)
print('MAX_ROUNDS:', o.MAX_ROUNDS)
a, h = o._parse_viz_tag('answer <!--VIZ{"show":"get_activities"}-->')
print('show-as-string normalised:', h)
a, h = o._parse_viz_tag('answer <!--VIZ{"show":[]}-->')
print('show-as-empty-list:', h)
a, h = o._parse_viz_tag('just a plain answer')
print('no tag:', h)
print('EXECUTE IMMEDIATELY in prompt:', 'EXECUTE IMMEDIATELY' in o._SYSTEM)
print('NEVER ASK PERMISSION in prompt:', 'NEVER ASK PERMISSION' in o._SYSTEM)
