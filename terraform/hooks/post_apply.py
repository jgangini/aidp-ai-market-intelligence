"""Install the exact paused TEST candidate from Deploy Studio's pinned source."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from ai_market_intelligence.hooks import run_hook

if __name__ == '__main__':
    raise SystemExit(run_hook('post_apply'))
