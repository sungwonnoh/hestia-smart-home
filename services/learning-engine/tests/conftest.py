import sys
from pathlib import Path

# learning-engine 모듈은 패키지가 아니라 스크립트(from baseline import ...)로 쓰인다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
