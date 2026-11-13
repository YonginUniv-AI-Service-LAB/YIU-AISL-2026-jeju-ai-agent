"""contracts.py → schemas/*.json (계약이 바뀔 때마다 실행해 커밋)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from coach.contracts import AnalysisResult, Report, StoreInput, VerifyResult, CONTRACT_VERSION

out = Path(__file__).resolve().parents[1] / "schemas"
out.mkdir(exist_ok=True)
for name, model in {"store_input": StoreInput, "analysis_result": AnalysisResult,
                    "report": Report, "verify_result": VerifyResult}.items():
    s = model.model_json_schema()
    s["$id"] = f"jeju-coach/{name}/{CONTRACT_VERSION}"
    (out / f"{name}.schema.json").write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", name)
